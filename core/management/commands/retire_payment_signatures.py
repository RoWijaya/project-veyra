from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import PaymentSigningVersion


class Command(BaseCommand):
    help = "Erase retained Midtrans callback signing keys for one reviewed tenant connection."

    def add_arguments(self, parser):
        parser.add_argument("--connection", type=int, required=True)

    def handle(self, *args, **options):
        count = PaymentSigningVersion.objects.filter(
            connection_id=options["connection"], revoked_at__isnull=True,
        ).update(ciphertext="", revoked_at=timezone.now())
        self.stdout.write(f"Retired historical signing versions: {count}")
