from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.models import AuditEvent, PaymentConnection


class Command(BaseCommand):
    help = "Enable an evidence-checked Midtrans merchant after reviewing external Core API Write and callback acceptance."

    def add_arguments(self, parser):
        parser.add_argument("--business", required=True)
        parser.add_argument("--environment", choices=PaymentConnection.Environment.values, required=True)
        parser.add_argument("--evidence", required=True, help="Internal approval record identifier, without secrets.")

    @transaction.atomic
    def handle(self, *args, **options):
        reference = options["evidence"].strip()
        if not reference or len(reference) > 120 or not reference.isascii() or not reference.isprintable():
            raise CommandError("Enter an internal approval record identifier up to 120 characters.")
        connection = PaymentConnection.objects.select_for_update(of=("self",)).filter(
            business__slug=options["business"], provider=PaymentConnection.Provider.MIDTRANS,
            environment=options["environment"], verification_state=PaymentConnection.Verification.EVIDENCE,
        ).first()
        now = timezone.now()
        if not connection or not connection.last_verification_at or connection.last_verification_at < now - timedelta(days=1):
            raise CommandError("Ask the business owner to verify the current Midtrans credentials first.")
        connection.approval_reference = reference
        connection.verification_state = PaymentConnection.Verification.VERIFIED
        connection.verified_at = now
        connection.last_verification_result = "Merchant and QRIS evidence checked. QRIS approved for this environment."
        connection.save(update_fields=[
            "approval_reference", "verification_state", "verified_at", "last_verification_result", "updated_at",
        ])
        AuditEvent.objects.create(
            business=connection.business, action="payment_connection.qris_approved", object_type="PaymentConnection",
            object_id=str(connection.pk), metadata={"environment": connection.environment, "evidence": reference},
        )
        self.stdout.write("QRIS enabled for the selected Midtrans environment.")
