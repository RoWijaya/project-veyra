from zoneinfo import ZoneInfo

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import AuditEvent, Business, OutboxEvent, Voucher
from .pricing import VoucherError, normalize_voucher_code
from .services import require_business_feature


class VoucherForm(forms.ModelForm):
    class Meta:
        model = Voucher
        fields = (
            "name", "code", "description", "discount_type", "value", "active",
            "starts_at", "ends_at", "minimum_subtotal", "maximum_discount",
            "redemption_limit", "customer_limit", "order_type", "stack_with_promotions",
            "locations", "products", "categories",
        )
        widgets = {
            "starts_at": forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}),
            "ends_at": forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}),
            "description": forms.Textarea(attrs={"rows": 3}),
        }
        help_texts = {
            "minimum_subtotal": "Minimum eligible product total after promotions, in Rp.",
            "maximum_discount": "Optional cap in Rp.",
            "customer_limit": "Requires a saved customer selected by a cashier.",
            "stack_with_promotions": "Apply the voucher after existing promotions.",
            "products": "Choose matching products or categories. Leave both empty to include every product.",
            "locations": "Leave empty for all locations.",
        }

    def __init__(self, *args, business, **kwargs):
        super().__init__(*args, **kwargs)
        self.business = business
        for field_name in ("locations", "products", "categories"):
            field = self.fields[field_name]
            field.queryset = getattr(business, field_name).order_by("name")
            field.label_from_instance = lambda item: item.name
        self.fields["minimum_subtotal"].required = False
        self.fields["order_type"].choices = [("", "Dine in and takeaway"), *list(Voucher._meta.get_field("order_type").choices)]

    def clean_minimum_subtotal(self):
        return self.cleaned_data["minimum_subtotal"] or 0

    def clean_code(self):
        try:
            code = normalize_voucher_code(self.cleaned_data["code"])
        except VoucherError as error:
            raise forms.ValidationError(str(error)) from error
        if self.business.vouchers.filter(code=code).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("A voucher with this code already exists.")
        return code


def voucher_status(voucher, now, remaining):
    if not voucher.active:
        return "Inactive"
    if voucher.ends_at and voucher.ends_at <= now:
        return "Expired"
    if voucher.starts_at and voucher.starts_at > now:
        return "Scheduled"
    if remaining == 0:
        return "Used up"
    return "Active"


@login_required
@require_http_methods(["GET", "POST"])
def vouchers(request, slug, voucher_id=None):
    from .views import membership_for

    membership = membership_for(request, slug)
    business = membership.business
    if not membership.allows("catalog.manage"):
        return HttpResponseForbidden("You cannot manage vouchers for this business.")
    try:
        require_business_feature(business, "promotions")
    except PermissionError as error:
        return HttpResponseForbidden(str(error))

    queryset = business.vouchers.annotate(used=Count("redemptions"))
    now = timezone.now()
    with timezone.override(ZoneInfo(business.timezone_name)):
        if request.method == "POST":
            with transaction.atomic():
                Business.objects.select_for_update().get(pk=business.pk)
                voucher = get_object_or_404(business.vouchers, pk=voucher_id) if voucher_id else Voucher(business=business)
                form = VoucherForm(request.POST, instance=voucher, business=business)
                if form.is_valid():
                    saved = form.save()
                    AuditEvent.objects.create(
                        business=business, actor=request.user, object_type="Voucher", object_id=str(saved.pk),
                        action="voucher.updated" if voucher_id else "voucher.created",
                    )
                    OutboxEvent.objects.create(
                        business=business, event_type="menu.update",
                        payload={"voucher": saved.pk}, next_attempt_at=now,
                    )
                    messages.success(request, "Voucher saved.")
                    return redirect("voucher_detail", slug=slug, voucher_id=saved.pk)
        else:
            voucher = get_object_or_404(business.vouchers, pk=voucher_id) if voucher_id else Voucher(business=business)
            form = VoucherForm(instance=voucher, business=business)
        search = request.GET.get("q", "").strip()[:120]
        status_filter = request.GET.get("status", "")
        if search:
            queryset = queryset.filter(Q(name__icontains=search) | Q(code__icontains=search))
        if status_filter == "active":
            queryset = queryset.filter(active=True).filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now))
            queryset = queryset.filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
            queryset = queryset.filter(Q(redemption_limit__isnull=True) | Q(used__lt=F("redemption_limit")))
        elif status_filter == "inactive":
            queryset = queryset.filter(active=False)
        elif status_filter == "expired":
            queryset = queryset.filter(active=True, ends_at__lte=now)
        elif status_filter == "scheduled":
            queryset = queryset.filter(active=True, starts_at__gt=now)
        elif status_filter == "used":
            queryset = queryset.filter(active=True, redemption_limit__isnull=False, used__gte=F("redemption_limit"))
            queryset = queryset.filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now))
            queryset = queryset.filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
        page = Paginator(queryset, 25).get_page(request.GET.get("page"))
        for item in page.object_list:
            item.remaining = None if item.redemption_limit is None else max(0, item.redemption_limit - item.used)
            item.status_label = voucher_status(item, now, item.remaining)
        pagination = request.GET.copy()
        pagination.pop("page", None)
        context = {
            "pagination_query": pagination.urlencode() + "&" if pagination else "",
            "offer_fields": [form[name] for name in ("name", "code", "description", "discount_type", "value", "active")],
            "limit_fields": [
                form[name] for name in ("starts_at", "ends_at", "minimum_subtotal", "maximum_discount",
                                        "redemption_limit", "customer_limit")
            ],
            "eligibility_fields": [
                form[name] for name in ("order_type", "stack_with_promotions", "locations", "products", "categories")
            ],
            "business": business, "form": form, "editing": voucher if voucher_id else None,
            "page_obj": page, "search": search, "status_filter": status_filter,
            "can_view_orders": membership.allows("orders.view"),
        }
        if voucher_id:
            redemptions = voucher.redemptions.select_related("order", "order__location")
            used = redemptions.count()
            remaining = None if voucher.redemption_limit is None else max(voucher.redemption_limit - used, 0)
            context.update(
                redemptions=redemptions[:25], used=used, remaining=remaining,
                status_label=voucher_status(voucher, now, remaining),
                audit=AuditEvent.objects.filter(business=business, object_type="Voucher", object_id=str(voucher.pk))
                .order_by("-created_at")[:15],
            )
        return render(request, "core/vouchers.html", context, status=400 if request.method == "POST" else 200)
