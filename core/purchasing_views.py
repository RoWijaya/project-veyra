from decimal import Decimal
from uuid import uuid4

from django import forms
from django.core.exceptions import PermissionDenied
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_safe

from .page_errors import access_restricted
from .models import AuditEvent, Business, InventoryBalance, Location, PurchaseOrder, PurchaseOrderLine, Supplier, SupplierReturn
from .purchasing import change_purchase_state, lock_purchasing_member, receive_purchase, save_purchase_draft, scoped_purchases
from .services import require_business_feature
from .stock_reconciliation import reconcile_count, record_supplier_return, returnable_quantity, scoped_balances
from .views import membership_for


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ("name", "contact_person", "phone", "email", "address", "notes", "active")
        widgets = {"notes": forms.Textarea(attrs={"rows": 3}), "phone": forms.TextInput(attrs={"type": "tel"})}


def purchasing_access(request, slug):
    membership = membership_for(request, slug)
    if not membership.allows("inventory.view"):
        raise PermissionError("You do not have access to purchasing.")
    if request.method not in ("GET", "HEAD"):
        require_business_feature(membership.business, "inventory")
    return membership


def paginate(request, queryset):
    filters = request.GET.copy()
    filters.pop("page", None)
    return {
        "page_obj": Paginator(queryset, 25).get_page(request.GET.get("page")),
        "pagination_query": filters.urlencode() + "&" if filters else "",
    }


@login_required
@require_safe
def suppliers(request, slug):
    try:
        membership = purchasing_access(request, slug)
    except PermissionError as error:
        return access_restricted(request, str(error))
    business = membership.business
    query = request.GET.get("q", "").strip()[:120]
    status = request.GET.get("status", "")
    records = business.suppliers.all()
    if query:
        records = records.filter(Q(name__icontains=query) | Q(contact_person__icontains=query) |
                                 Q(email__icontains=query) | Q(phone__icontains=query))
    if status in ("active", "inactive"):
        records = records.filter(active=status == "active")
    return render(request, "core/suppliers.html", {
        "business": business, "query": query, "status": status,
        "can_manage": membership.allows("inventory.manage"), **paginate(request, records),
    })


@login_required
@require_http_methods(["GET", "POST"])
def supplier_detail(request, slug, supplier_id=None):
    try:
        membership = purchasing_access(request, slug)
    except PermissionError as error:
        return access_restricted(request, str(error))
    business = membership.business
    can_manage = membership.allows("inventory.manage")
    if (supplier_id is None or request.method == "POST") and not can_manage:
        raise PermissionDenied
    supplier = get_object_or_404(business.suppliers, pk=supplier_id) if supplier_id else Supplier(business=business)
    form = SupplierForm(request.POST if request.method == "POST" else None, instance=supplier)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                lock_purchasing_member(business, request.user)
                supplier = form.save()
                AuditEvent.objects.create(
                    business=business, actor=request.user, object_type="Supplier", object_id=str(supplier.pk),
                    action="supplier.updated" if supplier_id else "supplier.created", metadata={"active": supplier.active},
                )
        except PermissionError as error:
            return access_restricted(request, str(error))
        except IntegrityError as error:
            if getattr(getattr(error.__cause__, "diag", None), "constraint_name", None) != "supplier_unique_name":
                raise
            form.add_error("name", "A supplier already uses this name.")
        else:
            messages.success(request, "Supplier saved.")
            return redirect("supplier_detail", slug=slug, supplier_id=supplier.pk)
    return render(request, "core/supplier_detail.html", {
        "business": business, "supplier": supplier if supplier_id else None,
        "form": form, "can_manage": can_manage,
        **(paginate(request, scoped_purchases(membership).filter(supplier=supplier)) if supplier_id else {}),
    }, status=400 if form.errors else 200)

class PurchaseHeaderForm(forms.Form):
    supplier = forms.ModelChoiceField(queryset=Supplier.objects.none(), empty_label="Choose a supplier")
    location = forms.ModelChoiceField(
        queryset=Location.objects.none(), label="Destination", empty_label="Choose a destination"
    )
    expected_on = forms.DateField(required=False, label="Expected delivery",
                                  widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)

    def __init__(self, *args, membership, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["supplier"].queryset = membership.business.suppliers.filter(active=True)
        self.fields["location"].queryset = membership.allowed_inventory_locations().filter(active=True).order_by("name")


class PurchaseItemForm(forms.Form):
    quantity = forms.DecimalField(min_value=Decimal("0.001"), max_digits=12, decimal_places=3)
    unit_cost = forms.DecimalField(label="Unit cost (Rp)", min_value=Decimal("0.01"), max_digits=12, decimal_places=2)

    def __init__(self, *args, business, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item"] = forms.ModelChoiceField(queryset=business.inventory_items.filter(active=True).order_by("name"),
                                                    label="Inventory item", empty_label="Choose an inventory item")
        self.fields["item"].label_from_instance = lambda item: f"{item.name} · {item.unit}"
        self.order_fields(("item", "quantity", "unit_cost"))


PurchaseItems = forms.formset_factory(PurchaseItemForm, extra=1, can_delete=True, max_num=50,
                                     validate_max=True, absolute_max=55)


class ReceivingForm(forms.Form):
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)
    delivery_reference = forms.CharField(required=False, max_length=80)
    notes = forms.CharField(required=False, max_length=500, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, lines, **kwargs):
        super().__init__(*args, **kwargs)
        self.rows = []
        for line in lines:
            fields = []
            for kind in ("accepted", "rejected", "damaged"):
                name = f"{kind}_{line.pk}"
                self.fields[name] = forms.DecimalField(
                    label=f"{kind.capitalize()} for {line.item_name}", required=False,
                    min_value=0, max_digits=12, decimal_places=3,
                )
                fields.append(self[name])
            self.rows.append((line, fields))

    def clean(self):
        data = super().clean()
        if any(key.startswith(("accepted_", "rejected_", "damaged_")) and key not in self.fields for key in self.data):
            raise forms.ValidationError("Choose only items on this purchase order.")
        return data

    def quantities(self):
        quantities = {}
        for line, fields in self.rows:
            amounts = tuple(self.cleaned_data[field.name] or Decimal("0") for field in fields)
            if any(amounts):
                quantities[line.pk] = amounts
        return quantities


@login_required
@require_safe
def purchase_orders(request, slug):
    try:
        membership = purchasing_access(request, slug)
    except PermissionError as error:
        return access_restricted(request, str(error))
    business = membership.business
    orders = scoped_purchases(membership)
    filters = request.GET
    filter_form = forms.Form(filters)
    filter_form.fields["q"] = forms.CharField(required=False, max_length=80, label="Reference")
    filter_form.fields["status"] = forms.ChoiceField(required=False, choices=[("", "All statuses"), *PurchaseOrder.Status.choices])
    filter_form.fields["supplier"] = forms.ModelChoiceField(required=False, queryset=business.suppliers.all())
    filter_form.fields["location"] = forms.ModelChoiceField(required=False, queryset=membership.allowed_inventory_locations().order_by("name"))
    filter_form.fields["item"] = forms.ModelChoiceField(required=False, label="Inventory item", queryset=business.inventory_items.order_by("name"))
    filter_form.fields["start"] = forms.DateField(required=False, label="Created from", widget=forms.DateInput(attrs={"type": "date"}))
    filter_form.fields["end"] = forms.DateField(required=False, label="Created through", widget=forms.DateInput(attrs={"type": "date"}))
    if filter_form.is_valid():
        data = filter_form.cleaned_data
        for field in ("status", "supplier", "location"):
            if data[field]:
                orders = orders.filter(**{field: data[field]})
        if data["q"]:
            orders = orders.filter(reference__icontains=data["q"])
        if data["item"]:
            orders = orders.filter(lines__item=data["item"])
        if data["start"] and data["end"] and data["start"] > data["end"]:
            filter_form.add_error("end", "Choose a date on or after the start date.")
            orders = orders.none()
        if data["start"]:
            orders = orders.filter(created_at__date__gte=data["start"])
        if data["end"]:
            orders = orders.filter(created_at__date__lte=data["end"])
    else:
        orders = orders.none()
    return render(request, "core/purchase_orders.html", {
        "business": business, "filter_form": filter_form, "can_manage": membership.allows("inventory.manage"),
        **paginate(request, orders),
    }, status=400 if filter_form.errors else 200)


@login_required
@require_http_methods(["GET", "POST"])
def purchase_editor(request, slug, purchase_id=None):
    try:
        membership = purchasing_access(request, slug)
    except PermissionError as error:
        return access_restricted(request, str(error))
    if not membership.allows("inventory.manage"):
        raise PermissionDenied
    business = membership.business
    purchase = get_object_or_404(scoped_purchases(membership), pk=purchase_id) if purchase_id else None
    if purchase and purchase.status != PurchaseOrder.Status.DRAFT:
        if request.method == "POST":
            return render(request, "core/purchase_conflict.html", {"business": business, "purchase": purchase}, status=409)
        return redirect("purchase_detail", slug=slug, purchase_id=purchase.pk)
    posted = request.POST if request.method == "POST" else None
    initial = (
        {"supplier": purchase.supplier_id, "location": purchase.location_id,
         "expected_on": purchase.expected_on, "revision": purchase.revision} if purchase else {"revision": 0}
    )
    header = PurchaseHeaderForm(posted, membership=membership, initial=initial)
    items = PurchaseItems(
        posted, prefix="lines", form_kwargs={"business": business},
        initial=[{"item": line.item_id, "quantity": line.ordered_quantity, "unit_cost": line.unit_cost}
                 for line in purchase.lines.all()] if purchase else [],
    )
    if posted is not None and all((header.is_valid(), items.is_valid())):
        try:
            purchase = save_purchase_draft(
                business=business, actor=request.user, data=header.cleaned_data, purchase_id=purchase_id,
                lines=[row for row in items.cleaned_data if row and not row.get("DELETE")],
            )
        except PermissionError as error:
            return access_restricted(request, str(error))
        except PurchaseOrder.DoesNotExist:
            raise Http404 from None
        except ValueError as error:
            header.add_error(None, str(error))
        else:
            messages.success(request, "Purchase draft saved.")
            return redirect("purchase_detail", slug=slug, purchase_id=purchase.pk)
    return render(request, "core/purchase_editor.html", {
        "business": business, "purchase": purchase, "header": header, "items": items,
    }, status=400 if posted is not None else 200)


@login_required
@require_http_methods(["GET", "POST"])
def purchase_detail(request, slug, purchase_id):
    try:
        membership = purchasing_access(request, slug)
    except PermissionError as error:
        return access_restricted(request, str(error))
    business = membership.business
    records = scoped_purchases(membership)
    purchase = get_object_or_404(records, pk=purchase_id)
    can_manage = membership.allows("inventory.manage")
    error_message = ""
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied
        action = request.POST.get("action", "")
        revision = forms.IntegerField(min_value=0, max_value=2147483647)
        try:
            change_purchase_state(business=business, actor=request.user, purchase_id=purchase_id,
                                  action=action, revision=revision.clean(request.POST.get("revision")))
        except PermissionError as error:
            return access_restricted(request, str(error))
        except PurchaseOrder.DoesNotExist:
            raise Http404 from None
        except (ValueError, forms.ValidationError) as error:
            error_message = str(error) if isinstance(error, ValueError) else "Review the current purchase and try again."
            purchase.refresh_from_db()
        else:
            messages.success(request, "Purchase marked as ordered." if action == "submit" else "Purchase cancelled.")
            return redirect("purchase_detail", slug=slug, purchase_id=purchase_id)
    supplier_returns = SupplierReturn.objects.filter(purchase_line__purchase=purchase).select_related(
        "purchase_line", "actor",
    )
    receipts = purchase.receipts.select_related("actor").prefetch_related("lines__purchase_line")
    return render(request, "core/purchase_detail.html", {
        "business": business, "purchase": purchase,
        "can_manage": can_manage, "error": error_message, "supplier_returns": Paginator(supplier_returns, 25).get_page(request.GET.get("returns_page")),
        **paginate(request, receipts),
    }, status=400 if error_message else 200)


@login_required
@require_http_methods(["GET", "POST"])
def purchase_receiving(request, slug, purchase_id):
    try:
        membership = purchasing_access(request, slug)
    except PermissionError as error:
        return access_restricted(request, str(error))
    if not membership.allows("inventory.manage"):
        raise PermissionDenied
    business = membership.business
    purchase = get_object_or_404(scoped_purchases(membership), pk=purchase_id)
    posted = request.POST if request.method == "POST" else None
    form = ReceivingForm(posted, lines=list(purchase.lines.all()), initial={"idempotency_key": uuid4()})
    can_receive = purchase.status in {PurchaseOrder.Status.ORDERED, PurchaseOrder.Status.PARTIAL} and purchase.location.active
    if posted is not None and form.is_valid():
        try:
            receipt, created = receive_purchase(
                business=business, actor=request.user, purchase_id=purchase_id,
                data=form.cleaned_data, quantities=form.quantities(),
            )
        except PermissionError as error:
            return access_restricted(request, str(error))
        except PurchaseOrder.DoesNotExist:
            raise Http404 from None
        except ValueError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, f"Delivery {receipt.reference} recorded." if created else f"Delivery {receipt.reference} was already recorded.")
            return redirect("purchase_detail", slug=slug, purchase_id=purchase_id)
    return render(request, "core/purchase_receiving.html", {
        "business": business, "purchase": purchase, "form": form, "can_receive": can_receive,
    }, status=400 if posted is not None else 200 if can_receive else 409)

class InventoryAccessForm(forms.Form):
    restricted = forms.BooleanField(required=False, label="Limit location access to the selected locations")
    locations = forms.ModelMultipleChoiceField(
        queryset=Location.objects.none(), required=False, label="Allowed locations",
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, business, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["locations"].queryset = business.locations.order_by("name")


@login_required
@require_http_methods(["GET", "POST"])
def inventory_access(request, slug, membership_id):
    owner = membership_for(request, slug)
    business = owner.business
    if business.owner_id != request.user.pk:
        raise PermissionDenied
    members = business.memberships.filter(active=True).exclude(user_id=business.owner_id)
    member = get_object_or_404(members.select_related("user"), pk=membership_id)
    form = InventoryAccessForm(
        request.POST if request.method == "POST" else None, business=business,
        initial={"restricted": member.inventory_restricted, "locations": member.inventory_locations.values_list("pk", flat=True)},
    )
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            locked = Business.objects.select_for_update().get(pk=business.pk)
            if locked.owner_id != request.user.pk:
                raise PermissionDenied
            member = get_object_or_404(members.select_for_update(), pk=membership_id)
            member.inventory_restricted = form.cleaned_data["restricted"]
            member.inventory_locations.set(form.cleaned_data["locations"] if member.inventory_restricted else [])
            member.save(update_fields=["inventory_restricted"])
            AuditEvent.objects.create(
                business=business, actor=request.user, action="inventory.location_access_updated",
                object_type="Membership", object_id=str(member.pk),
                metadata={"restricted": member.inventory_restricted, "location_ids": list(member.inventory_locations.values_list("pk", flat=True))},
            )
        messages.success(request, "Location access saved.")
        return redirect("team", slug=slug)
    return render(request, "core/inventory_access.html", {"business": business, "member": member, "form": form},
                  status=400 if form.errors else 200)


class ReconciliationForm(forms.Form):
    reason = forms.CharField(max_length=240, widget=forms.Textarea(attrs={"rows": 2}))
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)


class StockCountForm(ReconciliationForm):
    expected = forms.DecimalField(min_value=0, max_digits=12, decimal_places=3, widget=forms.HiddenInput)
    counted = forms.DecimalField(label="Counted stock", min_value=0, max_digits=12, decimal_places=3)

    field_order = ("counted", "reason")


class SupplierReturnForm(ReconciliationForm):
    quantity = forms.DecimalField(label="Quantity returned", min_value=Decimal("0.001"), max_digits=12, decimal_places=3)

    field_order = ("quantity", "reason")


@login_required
@require_http_methods(["GET", "POST"])
def stock_count(request, slug, balance_id):
    try:
        membership = purchasing_access(request, slug)
    except PermissionError as error:
        return access_restricted(request, str(error))
    balance = get_object_or_404(scoped_balances(membership).select_related("item", "location"), pk=balance_id)
    posted = request.POST if request.method == "POST" else None
    can_manage = membership.allows("inventory.manage")
    if posted is not None and not can_manage:
        raise PermissionDenied
    form = StockCountForm(posted, initial={"expected": balance.quantity, "idempotency_key": uuid4()})
    status = 200
    if posted is not None:
        status = 400
        if form.is_valid():
            try:
                recorded = reconcile_count(
                    business=membership.business, actor=request.user, balance_id=balance_id, **form.cleaned_data,
                )
            except PermissionError as error:
                return access_restricted(request, str(error))
            except InventoryBalance.DoesNotExist:
                raise Http404 from None
            except ValueError as error:
                status = 409
                form.add_error(None, str(error))
                balance.refresh_from_db()
            else:
                messages.success(request, "Stock count reconciled." if recorded else "This count was already recorded.")
                return redirect("stock_count", slug=slug, balance_id=balance_id)
    return render(request, "core/stock_count.html", {
        "business": membership.business, "balance": balance, "form": form,
        "can_count": can_manage and balance.item.active and balance.location.active,
        **paginate(request, balance.physical_counts.select_related("actor")),
    }, status=status)


@login_required
@require_http_methods(["GET", "POST"])
def supplier_return(request, slug, purchase_id, line_id):
    try:
        membership = purchasing_access(request, slug)
    except PermissionError as error:
        return access_restricted(request, str(error))
    if not membership.allows("inventory.manage"):
        raise PermissionDenied
    business = membership.business
    purchase = get_object_or_404(scoped_purchases(membership).select_related("location"), pk=purchase_id)
    line = get_object_or_404(purchase.lines, pk=line_id)
    posted = request.POST if request.method == "POST" else None
    form = SupplierReturnForm(posted, initial={"idempotency_key": uuid4()})
    status = 200
    if posted is not None:
        status = 400
        if form.is_valid():
            try:
                returned, created = record_supplier_return(
                    business=business, actor=request.user, purchase_id=purchase_id, line_id=line_id,
                    **form.cleaned_data,
                )
            except PermissionError as error:
                return access_restricted(request, str(error))
            except (PurchaseOrder.DoesNotExist, PurchaseOrderLine.DoesNotExist):
                raise Http404 from None
            except ValueError as error:
                status = 409
                form.add_error(None, str(error))
                line.refresh_from_db()
            else:
                messages.success(request, f"Return {returned.reference} recorded." if created else
                                 f"Return {returned.reference} was already recorded.")
                return redirect("purchase_detail", slug=slug, purchase_id=purchase_id)
    available = returnable_quantity(line)
    return render(request, "core/supplier_return.html", {
        "business": business, "purchase": purchase, "line": line, "form": form,
        "returnable": available, "can_return": purchase.location.active and available > 0,
    }, status=status)
