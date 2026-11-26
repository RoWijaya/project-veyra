import json

from cryptography.fernet import InvalidToken
from django.core.management.base import BaseCommand, CommandError

from core.identity import account_cipher
from core.models import Authenticator, PaymentConnection
from core.services import _payment_fernet


class Command(BaseCommand):
    help = "Check that restored tenant payment credentials and MFA secrets remain decryptable."

    def handle(self, *args, **options):
        counts = []
        for label, ciphertexts, cipher, validate in (
            ("payment bundles", PaymentConnection.objects.values_list("credentials_ciphertext", flat=True), _payment_fernet, json.loads),
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
        self.stdout.write("Restored key validation passed: " + ", ".join(counts))
