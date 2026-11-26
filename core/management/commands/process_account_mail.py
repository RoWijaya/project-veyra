from django.core.management.base import BaseCommand

from core.account_mail import deliver_verification, due_deliveries
from core.workers import run_worker
from core.password_recovery import deliver_password_recovery, due_password_recovery


class Command(BaseCommand):
    help = "Deliver pending account verification mail with durable retries."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--interval", type=float, default=2)

    def handle(self, *args, **options):
        run_worker("account_mail", self.deliver_batch, once=options["once"], interval=max(0.2, options["interval"]))

    def deliver_batch(self, stopping):
        delivery_ids = list(due_deliveries().order_by("next_attempt_at", "pk").values_list("pk", flat=True)[:50])
        for pk in delivery_ids:
            if stopping():
                return
            deliver_verification(pk)
        reset_ids = list(due_password_recovery().order_by("next_attempt_at", "pk").values_list("pk", flat=True)[:50])
        for pk in reset_ids:
            if stopping():
                return
            deliver_password_recovery(pk)
