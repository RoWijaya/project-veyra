from django import forms
from django.core.exceptions import PermissionDenied
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .page_errors import access_restricted
from .models import AuditEvent, Business, ModifierGroup, ModifierOption, OutboxEvent
from .services import require_business_feature


class ModifierGroupForm(forms.ModelForm):
    class Meta:
        model = ModifierGroup
        fields = ("name", "min_selections", "max_selections", "sort_order", "products", "locations", "active")
        help_texts = {
            "min_selections": "Use zero for an optional group.",
            "max_selections": "Use one for single selection.",
            "locations": "Leave empty to offer these options at all locations.",
        }
        widgets = {"products": forms.SelectMultiple(attrs={"size": 6}), "locations": forms.SelectMultiple(attrs={"size": 4})}

    def __init__(self, *args, business, **kwargs):
        super().__init__(*args, **kwargs)
        self.business = business
        self.fields["products"].queryset = business.products.order_by("name")
        self.fields["locations"].queryset = business.locations.order_by("name")
        for field_name in ("products", "locations"):
            self.fields[field_name].label_from_instance = lambda obj: obj.name if obj.active else f"{obj.name} (inactive)"

    def clean_name(self):
        name = self.cleaned_data["name"]
        if self.business.modifier_groups.filter(name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("A group with this name already exists.")
        return name


class ModifierOptionsFormSet(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        names = set()
        saved_names = {
            name.casefold(): pk for pk, name in self.instance.options.values_list("pk", "name")
        } if self.instance.pk else {}
        for form in self.forms:
            if not form.cleaned_data:
                continue
            option = form.cleaned_data.get("id")
            if option and option.pk != form.instance.pk:
                raise forms.ValidationError("An option does not belong to this group.")
            name = form.cleaned_data["name"].casefold()
            if name in names:
                raise forms.ValidationError("Give each option a unique name.")
            if name in saved_names and saved_names[name] != form.instance.pk:
                raise forms.ValidationError("This name is still assigned to another option.")
            names.add(name)


ModifierOptions = forms.inlineformset_factory(
    ModifierGroup, ModifierOption, formset=ModifierOptionsFormSet,
    fields=("name", "surcharge", "sort_order", "active"),
    extra=2, max_num=30, validate_max=True, absolute_max=32, can_delete=False,
)


@login_required
@require_http_methods(["GET", "POST"])
def modifiers(request, slug, group_id=None):
    from .views import membership_for

    membership = membership_for(request, slug)
    if not membership.allows("catalog.manage") or membership.location_restricted:
        raise PermissionDenied
    business = membership.business

    def editor():
        group = get_object_or_404(business.modifier_groups, pk=group_id) if group_id else ModifierGroup(business=business)
        data = request.POST if request.method == "POST" else None
        return ModifierGroupForm(data, instance=group, business=business), ModifierOptions(data, instance=group)

    if request.method == "POST":
        try:
            require_business_feature(business, "catalog")
        except PermissionError as error:
            return access_restricted(request, str(error))
        with transaction.atomic():
            Business.objects.select_for_update().get(pk=business.pk)
            form, option_forms = editor()
            if form.is_valid() and option_forms.is_valid():
                active_count = sum(
                    bool(option.cleaned_data.get("active")) for option in option_forms.forms
                    if option.cleaned_data
                )
                if form.cleaned_data["active"] and active_count < max(1, form.cleaned_data["min_selections"]):
                    form.add_error("min_selections", "Add enough active options before enabling this group.")
                else:
                    group = form.save()
                    option_forms.save()
                    action = "modifier_group.created" if group_id is None else "modifier_group.updated"
                    AuditEvent.objects.create(
                        business=business, actor=request.user, action=action,
                        object_type="ModifierGroup", object_id=str(group.pk),
                    )
                    OutboxEvent.objects.create(
                        business=business, event_type="menu.update",
                        payload={"modifier_group": group.pk}, next_attempt_at=timezone.now(),
                    )
                    messages.success(request, "Product options saved.")
                    return redirect("modifier_group_edit", slug=slug, group_id=group.pk)
    else:
        form, option_forms = editor()

    return render(request, "core/modifiers.html", {
        "business": business, "groups": business.modifier_groups.all(),
        "form": form, "option_forms": option_forms,
    }, status=400 if request.method == "POST" else 200)
