from django.db import transaction
from django.db.models import Sum

from .models import AuditEvent, InventoryBalance, StockMovement
from .purchasing import line_amount, lock_purchasing_member, publish_purchase_change, scoped_purchases
from .services import record_inventory_movement


def scoped_balances(membership):
    return InventoryBalance.objects.filter(
        item__business=membership.business, location__in=membership.allowed_inventory_locations(),
    )


@transaction.atomic
def reconcile_count(*, business, actor, balance_id, expected, counted, reason, idempotency_key):
    member = lock_purchasing_member(business, actor)
    balance = scoped_balances(member).select_for_update(of=("self",)).select_related("item", "location").get(pk=balance_id)
    previous = balance.physical_counts.filter(idempotency_key=idempotency_key).first()
    if previous:
        if (previous.expected, previous.counted, previous.reason) != (expected, counted, reason):
            raise ValueError("This submission key was already used for a different count.")
        return False
    if not balance.item.active or not balance.location.active:
        raise ValueError("Choose an active item at an active location.")
    if balance.quantity != expected:
        raise ValueError("Stock changed after this form was opened. Open a fresh count and verify the physical quantity.")
    movement = None
    if counted != expected:
        movement = record_inventory_movement(
            business=business, actor=actor, item=balance.item, location=balance.location,
            delta=counted - expected, reason=StockMovement.Reason.COUNT, note=reason,
        )
    count = balance.physical_counts.create(
        actor=actor, expected=expected, counted=counted, reason=reason,
        movement=movement, idempotency_key=idempotency_key,
    )
    AuditEvent.objects.create(
        business=business, actor=actor, action="inventory.count_reconciled",
        object_type="StockCount", object_id=str(count.pk),
        metadata={"expected": str(expected), "counted": str(counted), "balance_id": balance.pk},
    )
    return True


def returnable_quantity(line):
    returned = line.supplier_returns.aggregate(total=Sum("quantity", default=0))["total"]
    return line.received_quantity - returned


@transaction.atomic
def record_supplier_return(*, business, actor, purchase_id, line_id, quantity, reason, idempotency_key):
    member = lock_purchasing_member(business, actor)
    purchase = scoped_purchases(member).select_for_update().select_related("location").get(pk=purchase_id)
    line = purchase.lines.select_related("item").get(pk=line_id, item__business=business)
    previous = line.supplier_returns.filter(idempotency_key=idempotency_key).first()
    if previous:
        if (previous.quantity, previous.reason) != (quantity, reason):
            raise ValueError("This submission key was already used for a different return.")
        return previous, False
    if not purchase.location.active:
        raise ValueError("Supplier returns require an active receiving location.")
    if quantity <= 0 or quantity > returnable_quantity(line):
        raise ValueError("The return exceeds the remaining accepted quantity for this item.")
    movement = record_inventory_movement(
        business=business, actor=actor, item=line.item, location=purchase.location,
        delta=-quantity, reason=StockMovement.Reason.SUPPLIER_RETURN,
        note=f"{purchase.reference} · {reason[:200]}",
    )
    returned = line.supplier_returns.create(
        actor=actor, quantity=quantity, unit_cost=line.unit_cost, subtotal=line_amount(quantity, line.unit_cost),
        reason=reason, movement=movement, idempotency_key=idempotency_key,
    )
    publish_purchase_change(purchase, actor, "supplier_return", reference=returned.reference, quantity=str(quantity))
    return returned, True
