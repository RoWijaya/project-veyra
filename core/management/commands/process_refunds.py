from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import OrderRefund
from core.observability import logger
from core.payments.midtrans import MidtransStatusUnavailable
from core.refunds import reconcile_midtrans_refund
from core.workers import run_worker


def claim_pending_refunds():
    now = timezone.now()
    with transaction.atomic():
        pending = list(OrderRefund.objects.select_for_update(skip_locked=True).filter(
            status=OrderRefund.Status.PENDING, payment__isnull=False, next_check_at__lte=now,
        ).order_by("next_check_at", "pk")[:20])
        for refund in pending:
            age = now - refund.created_at
            refund.next_check_at = now + (timedelta(minutes=2) if age < timedelta(hours=1) else timedelta(hours=1))
        OrderRefund.objects.bulk_update(pending, ["next_check_at"])
    return pending


class Command(BaseCommand):
    help = "Verify pending Midtrans refunds without submitting new refunds."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--interval", type=int, default=60)

    def handle(self, *args, **options):
        run_worker("refunds", self.reconcile_batch, once=options["once"], interval=max(10, options["interval"]))

    def reconcile_batch(self, stopping):
        for refund in claim_pending_refunds():
            if stopping():
                return
            try:
                reconcile_midtrans_refund(refund)
            except MidtransStatusUnavailable:
                logger.warning("Refund status check could not be verified", extra={"event": "refund.verification_delayed"})
