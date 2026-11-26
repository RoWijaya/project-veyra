from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from core.billing import GRACE_PERIOD, advance_subscription
from core.billing_gateway import BillingGatewayError, gateway_available, reconcile_payment
from core.models import BillingAttempt, BusinessSubscription
from core.workers import run_worker


class Command(BaseCommand):
    help = "Advance subscriptions and reconcile platform payments without visiting the UI."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--interval", type=int, default=60)

    def handle(self, *args, **options):
        run_worker("billing", self.reconcile_batch, once=options["once"], interval=max(1, options["interval"]))

    def reconcile_batch(self, stopping):
        now = timezone.now()
        upcoming = BusinessSubscription.objects.filter(
            Q(status="trial", trial_ends_at__lte=now)
            | Q(status="active", current_period_end__lte=now)
            | Q(status="past_due", current_period_end__lte=now - GRACE_PERIOD)
        ).order_by("updated_at").values_list("business_id", flat=True)[:200]
        for business_id in list(upcoming):
            if stopping():
                return
            advance_subscription(business_id, now)
        if gateway_available():
            stale_attempts = BillingAttempt.objects.filter(status="pending", retry_after__lte=now)
            for attempt_id in list(stale_attempts.order_by("retry_after").values_list("pk", flat=True)[:20]):
                if stopping():
                    return
                try:
                    reconcile_payment(attempt_id)
                except BillingGatewayError:
                    self.stderr.write("Platform payment reconciliation will retry.")
