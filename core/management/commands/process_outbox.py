from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import OutboxEvent
from core.media_services import queue_banner_boundary_events
from core.services import dispatch_outbox_event, queue_promotion_boundary_events
from core.workers import run_worker


class Command(BaseCommand):
    help = "Publish pending operational events to the configured Channels layer."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Process one batch and exit.")
        parser.add_argument("--interval", type=float, default=2.0, help="Seconds between polls.")

    def handle(self, *args, **options):
        run_worker("outbox", self.publish_batch, once=options["once"], interval=max(0.2, options["interval"]))

    def publish_batch(self, stopping):
        queue_promotion_boundary_events()
        queue_banner_boundary_events()
        event_ids = list(
            OutboxEvent.objects.filter(
                published_at__isnull=True,
                failed_at__isnull=True,
                next_attempt_at__lte=timezone.now(),
            ).order_by("created_at").values_list("pk", flat=True)[:100]
        )
        for event_id in event_ids:
            if stopping():
                return
            dispatch_outbox_event(event_id)
