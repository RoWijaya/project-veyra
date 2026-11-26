import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import AuditEvent, Membership, Order, OrderRefund, OrderTender, PaymentConnection, PaymentTransaction
from .payments import get_payment_adapter
from .payments.midtrans import MidtransStatusUnavailable
from .services import _payment_fernet, _queue_order_event


def refund_total(records):
    return records.aggregate(total=Sum("amount", default=0))["total"]


def can_manage_refunds(membership):
    return (
        membership.active and membership.business.owner_id == membership.user_id
        and membership.allows("orders.view") and membership.allows("orders.manage")
    )


def refund_capture(order):
    if order.payment_status != Order.PaymentStatus.PAID:
        return None, None
    if order.payment_method in OrderTender.Method.values:
        return OrderTender.objects.filter(order=order, method=order.payment_method).first(), None
    paid = list(order.payments.filter(
        business=order.business, status=PaymentTransaction.Status.PAID,
    ).order_by("paid_at", "pk")[:2])
    if len(paid) == 1 and paid[0].provider == PaymentConnection.Provider.MIDTRANS:
        return None, paid[0]
    return None, None


def refundable_amount(order, tender, payment):
    capture = tender or payment
    if capture is None:
        return Decimal("0.00")
    reserved = order.refunds.filter(tender=tender, payment=payment).exclude(status=OrderRefund.Status.FAILED)
    return max(Decimal("0.00"), capture.amount - refund_total(reserved))


def audit_refund(refund, change, actor=None):
    AuditEvent.objects.create(
        business=refund.order.business, actor=actor, action=f"refund.{change}",
        object_type="OrderRefund", object_id=str(refund.pk),
        metadata={"order_id": str(refund.order_id), "amount": str(refund.amount), "reference": refund.reference},
    )
    _queue_order_event(refund.order, "refund_updated")


@transaction.atomic
def reserve_refund(*, business, actor, order_id, amount, reason, idempotency_key, reference=""):
    membership = Membership.objects.filter(business=business, user=actor, active=True).first()
    if membership is None or not can_manage_refunds(membership):
        raise PermissionError("Only the business owner can record refunds.")
    order = Order.objects.select_for_update().get(business=business, pk=order_id)
    previous = order.refunds.filter(idempotency_key=idempotency_key).first()
    if previous:
        if (previous.amount, previous.reason) != (amount, reason) or (
            previous.tender_id and previous.provider_reference != reference
        ):
            raise ValueError("This submission key was already used for a different refund.")
        return previous, False
    tender, payment = refund_capture(order)
    if not tender and not payment:
        raise ValueError("Refund recording is unavailable for this payment.")
    if not amount.is_finite() or amount <= 0 or amount > refundable_amount(order, tender, payment):
        raise ValueError("Enter an amount within the remaining refundable balance.")
    if payment and amount != amount.to_integral_value():
        raise ValueError("Enter a whole-rupiah amount for this QRIS refund.")
    if tender and tender.method == OrderTender.Method.CARD and not reference:
        raise ValueError("Record the refund on the card terminal first, then enter its reference.")
    refund = order.refunds.create(
        actor=actor, tender=tender, payment=payment, amount=amount, reason=reason,
        idempotency_key=idempotency_key, provider_reference=reference if tender else "",
        status=OrderRefund.Status.SUCCEEDED if tender else OrderRefund.Status.PENDING,
        resolved_at=timezone.now() if tender else None,
    )
    audit_refund(refund, "recorded" if tender else "requested", actor=actor)
    return refund, True


def verified_midtrans_status(adapter, payment, credentials):
    data = adapter.transaction_status(
        connection=payment.connection, credentials=credentials, reference=payment.merchant_reference,
    )
    adapter.verify_transaction(payment, data)
    return data


def apply_midtrans_refund_evidence(order, payment, data):
    """Apply authoritative status while the caller holds the order and payment locks."""
    if data.get("transaction_status") not in ("refund", "partial_refund"):
        return
    details = data.get("refunds")
    if not isinstance(details, list):
        return
    for detail in details:
        if not isinstance(detail, dict) or not detail.get("bank_confirmed_at"):
            continue
        provider_id = str(detail.get("refund_chargeback_id") or "")
        if not provider_id or len(provider_id) > 120:
            continue
        try:
            amount = Decimal(str(detail.get("refund_amount", "")))
        except InvalidOperation:
            continue
        if not amount.is_finite() or amount <= 0 or amount != amount.to_integral_value():
            continue
        records = order.refunds.filter(payment=payment)
        if records.filter(provider_reference=provider_id, status=OrderRefund.Status.SUCCEEDED).exists():
            continue
        if refund_total(records.filter(status=OrderRefund.Status.SUCCEEDED)) + amount > payment.amount:
            continue
        key = detail.get("refund_key")
        refund = next((row for row in records.exclude(status=OrderRefund.Status.SUCCEEDED)
                       if row.reference == key), None)
        if refund:
            if refund.amount != amount:
                continue
            refund.status = OrderRefund.Status.SUCCEEDED
            refund.provider_reference = provider_id
            refund.resolved_at = timezone.now()
            refund.save(update_fields=["status", "provider_reference", "resolved_at"])
        else:
            refund = records.create(
                order=order, payment=payment, amount=amount, provider_reference=provider_id,
                reason="Refund recorded by the payment provider.",
                status=OrderRefund.Status.SUCCEEDED, resolved_at=timezone.now(),
            )
        audit_refund(refund, "confirmed")


@transaction.atomic
def claim_refund_submission(refund):
    Order.objects.select_for_update().get(pk=refund.order_id)
    record = OrderRefund.objects.select_for_update().get(pk=refund.pk)
    now = timezone.now()
    if record.status != OrderRefund.Status.PENDING:
        return False
    if record.last_submitted_at and now - record.last_submitted_at < timedelta(minutes=1):
        return False
    if now - record.created_at >= timedelta(days=6):
        return False
    record.last_submitted_at = now
    record.save(update_fields=["last_submitted_at"])
    return True


def reconcile_midtrans_refund(refund, *, submit=False):
    payment = refund.payment
    adapter = get_payment_adapter(PaymentConnection.Provider.MIDTRANS)
    credentials = json.loads(_payment_fernet().decrypt(payment.connection.credentials_ciphertext.encode()))
    data = verified_midtrans_status(adapter, payment, credentials)
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=refund.order_id)
        locked = PaymentTransaction.objects.select_for_update().get(pk=payment.pk, order=order)
        apply_midtrans_refund_evidence(order, locked, data)
    if not submit or data.get("transaction_status") not in ("settlement", "partial_refund") or not claim_refund_submission(refund):
        return
    response = adapter.request_refund(
        connection=payment.connection, credentials=credentials, reference=payment.merchant_reference,
        key=refund.reference, amount=refund.amount, reason=refund.reason,
    )
    data = verified_midtrans_status(adapter, payment, credentials)
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=refund.order_id)
        locked = PaymentTransaction.objects.select_for_update().get(pk=payment.pk, order=order)
        apply_midtrans_refund_evidence(order, locked, data)
        refund.refresh_from_db()
        # A denial must be bound to this exact request; network failures and duplicates stay pending.
        if (
            refund.status == OrderRefund.Status.PENDING and str(response.get("status_code")) == "202"
            and response.get("refund_key") == refund.reference
            and response.get("transaction_id") == payment.provider_transaction_id
        ):
            refund.status = OrderRefund.Status.FAILED
            refund.resolved_at = timezone.now()
            refund.save(update_fields=["status", "resolved_at"])
            audit_refund(refund, "failed")
