import json

from cryptography.fernet import InvalidToken
from django.core import signing
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from datetime import timedelta
from pathlib import Path

from core.account_mail import VERIFICATION_TTL, redirect_cipher
from core.identity import account_cipher
from core.models import Authenticator, PaymentConnection, PaymentSigningVersion, PasswordRecoveryDelivery, VerificationDelivery
from core.services import _payment_fernet


class Command(BaseCommand):
    help = "Verify restored encryption keys and optionally a signed backup marker."

    def add_arguments(self, parser):
        parser.add_argument("--signing-probe", type=Path, help="Protected manifest file containing the backup signing marker.")

    def handle(self, *args, **options):
        now = timezone.now()
        signing_versions = PaymentSigningVersion.objects.filter(
            revoked_at__isnull=True, valid_until__gt=now,
        ).values_list("ciphertext", flat=True)
        redirects = VerificationDelivery.objects.filter(
            state__in=("pending", "sending"), requested_at__gt=now - timedelta(seconds=VERIFICATION_TTL),
        ).exclude(next_url="").values_list("next_url", flat=True)
        recovery_requests = PasswordRecoveryDelivery.objects.filter(
            finished_at__isnull=True, requested_at__gt=now - timedelta(seconds=VERIFICATION_TTL),
        ).values_list("recipient_ciphertext", flat=True)
        counts = []
        for label, ciphertexts, cipher, validate in (
            ("payment bundles", PaymentConnection.objects.values_list("credentials_ciphertext", flat=True), _payment_fernet, json.loads),
            ("retained payment signing keys", signing_versions, _payment_fernet, json.loads),
            ("pending verification redirects", redirects, redirect_cipher, bytes.decode),
            ("pending password recovery recipients", recovery_requests, redirect_cipher, bytes.decode),
            ("MFA secrets", Authenticator.objects.values_list("secret_ciphertext", flat=True), account_cipher, bytes.decode),
        ):
            count, failures = 0, 0
            for ciphertext in ciphertexts.iterator():
                try:
                    validate(cipher().decrypt(ciphertext.encode()))
                except (InvalidToken, UnicodeError, RuntimeError, ValueError):
                    failures += 1
                count += 1
            if failures:
                raise CommandError(f"Cannot decrypt {failures} of {count} restored {label}. Check the current and retained keys.")
            counts.append(f"{label}={count}")
        if options["signing_probe"]:
            try:
                marker = options["signing_probe"].read_text(encoding="utf-8").strip()
                if signing.loads(marker, salt="veyra.backup-probe") != "veyra-backup":
                    raise signing.BadSignature
            except (OSError, ValueError, signing.BadSignature):
                raise CommandError("Restore signing marker could not be verified. Check retained Django keys.") from None
            self.stdout.write("Backup signing marker verified.")
        self.stdout.write("Restored key validation passed: " + ", ".join(counts))
