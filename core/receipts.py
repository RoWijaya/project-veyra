import uuid

from django.db import transaction

from .models import AuditEvent, Membership, Order, PrintJob, Station
from .services import _queue_order_event, format_receipt


@transaction.atomic
def queue_receipt(*, business, order_id, actor, station_id, idempotency_key, action):
    membership = Membership.objects.get(business=business, user=actor, active=True)
    if not membership.allows("orders.view") or not membership.allows("orders.manage"):
        raise PermissionError("You cannot dispatch receipts for this business.")
    if action not in {"print", "reprint", "retry"}:
        raise ValueError("Choose print, reprint, or retry.")
    key = uuid.UUID(str(idempotency_key))
    order = Order.objects.select_for_update().get(business=business, pk=order_id)
    if order.payment_status != Order.PaymentStatus.PAID:
        raise ValueError("Payment must be recorded before printing a receipt.")
    station_id = uuid.UUID(str(station_id))
    station = Station.objects.select_related("printer").get(
        pk=station_id, business=business, active=True, location_id=order.location_id,
        printer__business=business, printer__is_active=True,
    )
    if "orders.view" not in station.capabilities:
        raise ValueError("The selected station needs permission to receive print jobs.")
    if station.printer.location_id not in {None, order.location_id}:
        raise ValueError("Choose a printer at the order's location.")
    if station.printer.paper_width not in {"58mm", "80mm"}:
        raise ValueError("Configure the printer for 58mm or 80mm paper.")
    existing = PrintJob.objects.filter(business=business, idempotency_key=key).first()
    if existing:
        if (existing.order_id != order.pk or existing.station_id != station.pk
                or existing.job_type != PrintJob.JobType.RECEIPT
                or existing.ticket_payload.get("request_action") != action):
            raise ValueError("This print request key was already used for a different request.")
        return existing, False
    jobs = order.print_jobs.filter(job_type=PrintJob.JobType.RECEIPT)
    if jobs.filter(status__in=[PrintJob.Status.PENDING, PrintJob.Status.PRINTING]).exists():
        raise ValueError("A receipt is already queued or being printed. Wait for its status.")
    latest = jobs.order_by("-created_at").first()
    if latest:
        expected = "reprint" if latest.status == PrintJob.Status.PRINTED else "retry"
        if action != expected:
            raise ValueError(f"Choose {expected} to request another copy of this receipt.")
    elif action != "print":
        raise ValueError("Print the first receipt before requesting another copy.")
    payload = dict(latest.ticket_payload) if latest else format_receipt(order=order)
    payload.update(request_action=action, is_reprint=bool(latest), paper_width=station.printer.paper_width)
    job = PrintJob.objects.create(
        business=business, order=order, station=station, printer=station.printer,
        job_type=PrintJob.JobType.RECEIPT, idempotency_key=key, ticket_payload=payload,
    )
    AuditEvent.objects.create(
        business=business, actor=actor, action="receipt.queued", object_type="PrintJob",
        object_id=str(job.pk), metadata={"order_id": str(order.pk), "action": action},
    )
    _queue_order_event(order, "receipt.updated")
    return job, True
