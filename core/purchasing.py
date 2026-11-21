import hashlib
import json
import uuid
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone

from .models import (
    AuditEvent, Business, OutboxEvent, PurchaseOrder, PurchaseOrderLine,
    StockMovement,
)
from .services import record_inventory_movement, require_business_feature


def line_amount(quantity, unit_cost):
    return (quantity * unit_cost).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def lock_purchasing_member(business, actor):
    business = Business.objects.select_for_update().get(pk=business.pk)
    require_business_feature(business, "inventory")
    membership = business.memberships.filter(user=actor, active=True).first()
    if membership is None or not all(membership.allows(code) for code in ("inventory.manage", "inventory.view")):
        raise PermissionError("You cannot manage purchasing for this business.")
    return membership


def scoped_purchases(membership):
    return membership.business.purchase_orders.filter(location__in=membership.allowed_inventory_locations())


def publish_purchase_change(purchase, actor, action, **metadata):
    AuditEvent.objects.create(
        business=purchase.business, actor=actor, object_type="PurchaseOrder", object_id=str(purchase.pk),
        action=f"purchase.{action}", metadata=metadata,
    )
    OutboxEvent.objects.create(
        business=purchase.business, event_type="purchase.update",
        payload={"purchase_id": str(purchase.pk), "location_id": str(purchase.location_id)},
        next_attempt_at=timezone.now(),
    )


def require_revision(purchase, revision):
    if purchase.revision != revision:
        raise ValueError("The purchase changed. Review the current details before continuing.")


@transaction.atomic
def save_purchase_draft(*, business, actor, data, lines, purchase_id=None):
    membership = lock_purchasing_member(business, actor)
    location = membership.allowed_inventory_locations().filter(pk=data["location"].pk, active=True).first()
    supplier = business.suppliers.filter(pk=data["supplier"].pk, active=True).first()
    if location is None or supplier is None:
        raise ValueError("Choose an active supplier and an authorized active destination.")
    item_ids = {line["item"].pk for line in lines}
    items = business.inventory_items.filter(active=True).in_bulk(item_ids)
    if not lines or len(items) != len(lines):
        raise ValueError("Choose distinct, active inventory items for the order.")
    if purchase_id:
        purchase = scoped_purchases(membership).select_for_update().get(pk=purchase_id)
        require_revision(purchase, data["revision"])
        if purchase.status != PurchaseOrder.Status.DRAFT:
            raise ValueError("Only draft purchases can be edited.")
        previous_location = purchase.location_id
        purchase.lines.all().delete()
    else:
        previous_location = None
        purchase = PurchaseOrder(business=business, created_by=actor)
        purchase.reference = f"PO-{purchase.pk.hex[:12].upper()}"
    purchase.location, purchase.location_name = location, location.name
    purchase.supplier, purchase.supplier_name = supplier, supplier.name
    purchase.expected_on = data.get("expected_on")
    purchase.revision += 1
    purchase_lines = []
    for data_line in lines:
        item = items[data_line["item"].pk]
        quantity, unit_cost = data_line["quantity"], data_line["unit_cost"]
        purchase_lines.append(PurchaseOrderLine(
            purchase=purchase, item=item, item_name=item.name, unit=item.unit,
            ordered_quantity=quantity, unit_cost=unit_cost, subtotal=line_amount(quantity, unit_cost),
        ))
    purchase.total = sum(line.subtotal for line in purchase_lines)
    purchase.save()
    PurchaseOrderLine.objects.bulk_create(purchase_lines)
    publish_purchase_change(purchase, actor, "edited" if purchase_id else "created")
    if previous_location and previous_location != location.pk:
        OutboxEvent.objects.create(
            business=business, event_type="purchase.update",
            payload={"location_id": str(previous_location)},
            next_attempt_at=timezone.now(),
        )
    return purchase


@transaction.atomic
def change_purchase_state(*, business, actor, purchase_id, action, revision):
    membership = lock_purchasing_member(business, actor)
    purchase = scoped_purchases(membership).select_for_update().select_related("supplier", "location").get(pk=purchase_id)
    require_revision(purchase, revision)
    status = PurchaseOrder.Status
    if action == "submit":
        if purchase.status != status.DRAFT or not purchase.supplier.active or not purchase.location.active:
            raise ValueError("Only a draft with an active supplier and destination can be ordered.")
        if not purchase.lines.exists() or purchase.lines.filter(item__active=False).exists():
            raise ValueError("Review the active inventory items before ordering.")
        purchase.status = status.ORDERED
        purchase.ordered_at = timezone.now()
    elif action == "cancel" and purchase.status in {status.DRAFT, status.ORDERED, status.PARTIAL}:
        purchase.status = status.CANCELLED
    else:
        raise ValueError("That change is unavailable for this purchase.")
    purchase.revision += 1
    purchase.save(update_fields=["status", "ordered_at", "revision"])
    publish_purchase_change(purchase, actor, "ordered" if action == "submit" else "cancelled")
    return purchase


@transaction.atomic
def receive_purchase(*, business, actor, purchase_id, data, quantities):
    membership = lock_purchasing_member(business, actor)
    purchase = scoped_purchases(membership).select_for_update().select_related("location").get(pk=purchase_id)
    payload = [[key, *(str(value.quantize(Decimal("0.001"))) for value in values)] for key, values in sorted(quantities.items())]
    digest = hashlib.sha256(json.dumps([payload, data["delivery_reference"], data["notes"]]).encode()).hexdigest()
    previous = purchase.receipts.filter(idempotency_key=data["idempotency_key"]).first()
    if previous:
        if previous.request_digest != digest:
            raise ValueError("This submission key was already used for a different delivery. Reopen receiving.")
        return previous, False
    status = PurchaseOrder.Status
    if purchase.status not in {status.ORDERED, status.PARTIAL} or not purchase.location.active:
        raise ValueError("This purchase is unavailable for receiving.")
    if not quantities:
        raise ValueError("Enter a delivered quantity for at least one item.")
    lines = {line.pk: line for line in purchase.lines.select_related("item")}
    if not quantities.keys() <= lines.keys():
        raise ValueError("Choose only items on this purchase order.")
    for key, values in quantities.items():
        line = lines[key]
        if any(amount < 0 for amount in values) or sum(values) > line.outstanding_quantity:
            raise ValueError(f"The delivery exceeds the outstanding quantity for {line.item_name}.")
    receipt = purchase.receipts.create(
        actor=actor, idempotency_key=data["idempotency_key"], request_digest=digest,
        delivery_reference=data["delivery_reference"], notes=data["notes"],
    )
    for line in sorted((lines[key] for key in quantities), key=lambda line: line.item_id):
        accepted, rejected, damaged = quantities[line.pk]
        receipt_line = receipt.lines.create(
            purchase_line=line, accepted_quantity=accepted, rejected_quantity=rejected,
            damaged_quantity=damaged, unit_cost=line.unit_cost, subtotal=line_amount(accepted, line.unit_cost),
        )
        if accepted:
            record_inventory_movement(
                business=business, actor=actor, item=line.item, location=purchase.location, delta=accepted,
                reason=StockMovement.Reason.RECEIVED, receiving_line=receipt_line,
                note=f"{purchase.reference} · {receipt.reference}",
            )
            line.received_quantity += accepted
            line.save(update_fields=["received_quantity"])
    if all(line.outstanding_quantity == 0 for line in lines.values()):
        purchase.status = status.RECEIVED
    elif any(line.received_quantity for line in lines.values()):
        purchase.status = status.PARTIAL
    purchase.revision += 1
    purchase.save(update_fields=["status", "revision"])
    publish_purchase_change(purchase, actor, "received", receipt_id=str(receipt.pk), status=purchase.status)
    return receipt, True
