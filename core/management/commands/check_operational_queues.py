from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import BillingAttempt, OrderRefund, OutboxEvent, PrintJob, VerificationDelivery


class Command(BaseCommand):
    help = "Report aggregate queue delays and terminal failures without customer or provider data."

    def add_arguments(self, parser):
        parser.add_argument("--max-delay-minutes", type=int, default=15)

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(minutes=max(1, options["max_delay_minutes"]))
        delayed = {
            "outbox": OutboxEvent.objects.filter(published_at__isnull=True, created_at__lte=cutoff).count(),
            "account_mail": VerificationDelivery.objects.filter(state__in=("pending", "sending"), requested_at__lte=cutoff).count(),
            "billing_checks": BillingAttempt.objects.filter(status="pending", retry_after__lte=cutoff).count(),
            "refund_checks": OrderRefund.objects.filter(status="pending", payment__isnull=False, next_check_at__lte=cutoff).count(),
            "printing": PrintJob.objects.filter(status__in=("pending", "printing"), created_at__lte=cutoff).count(),
        }
        terminal = {
            "account_mail_failed": VerificationDelivery.objects.filter(state="failed").count(),
            "print_jobs_failed": PrintJob.objects.filter(status="failed").count(),
        }
        self.stdout.write("Delayed: " + ", ".join(f"{name}={count}" for name, count in delayed.items()))
        self.stdout.write("Terminal failures: " + ", ".join(f"{name}={count}" for name, count in terminal.items()))
        if any(delayed.values()) or any(terminal.values()):
            raise CommandError("Operational queues need review.")
