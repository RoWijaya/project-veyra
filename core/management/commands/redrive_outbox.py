from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.models import AuditEvent, OutboxEvent


class Command(BaseCommand):
    help = "Retry one reviewed, exhausted outbox event."

    def add_arguments(self, parser):
        parser.add_argument("--business", required=True)
        parser.add_argument("--event", required=True)
        parser.add_argument("--evidence", required=True, help="A short incident or change reference.")

    @transaction.atomic
    def handle(self, *args, **options):
        reference = options["evidence"].strip()
        if not reference or len(reference) > 120 or not reference.isascii() or not reference.isprintable():
            raise CommandError("Enter a printable incident reference up to 120 characters.")
        try:
            event = OutboxEvent.objects.select_for_update(of=("self",)).get(
                business__slug=options["business"], pk=options["event"],
                published_at__isnull=True, failed_at__isnull=False,
            )
        except (OutboxEvent.DoesNotExist, ValueError) as error:
            raise CommandError("No matching exhausted event.") from error
        AuditEvent.objects.create(
            business_id=event.business_id, action="outbox.redrive", object_type="OutboxEvent",
            object_id=str(event.pk), metadata={"evidence": reference, "previous_attempts": event.attempts},
        )
        event.attempts = 0
        event.next_attempt_at = timezone.now()
        event.failed_at = None
        event.save(update_fields=["attempts", "next_attempt_at", "failed_at"])
        self.stdout.write("Exhausted event queued for retry.")
