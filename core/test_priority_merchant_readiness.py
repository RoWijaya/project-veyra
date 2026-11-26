import hashlib
from unittest.mock import Mock, patch

import requests
from cryptography.fernet import Fernet
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import PaymentConnection
from .test_audit_remediation import ShopFixture


@override_settings(PAYMENT_CREDENTIALS_ENCRYPTION_KEY=Fernet.generate_key().decode())
class MerchantReadinessTests(ShopFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("payment_settings", args=[self.business.slug])
        self.client.post(self.url, {
            "action": "save", "provider": "midtrans", "environment": "sandbox", "merchant_id": "MID-fixture",
            "server_key": "SB-Mid-server-fixture", "signature_secret_key": "different-fixture-signature",
        })
        self.connection = self.business.payment_connections.get()

    def verify(self):
        return self.client.post(self.url, {
            "action": "verify", "connection_id": self.connection.pk, "verification_reference": "QRIS-validation",
        })

    def reply(self, **changes):
        payload = {
            "merchant_id": "MID-fixture", "order_id": "QRIS-validation", "status_code": "200",
            "gross_amount": "5000.00", "payment_type": "qris", **changes,
        }
        message = payload["order_id"] + payload["status_code"] + payload["gross_amount"] + "different-fixture-signature"
        return Mock(status_code=200, json=Mock(return_value={**payload, "signature_key": hashlib.sha512(message.encode()).hexdigest()}))

    @patch("core.payments.midtrans.requests.get")
    def test_read_only_evidence_and_operator_approval_are_separate(self, transport):
        transport.return_value = self.reply()
        self.assertRedirects(self.verify(), self.url)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.verification_state, PaymentConnection.Verification.EVIDENCE)
        self.assertIsNone(self.connection.verified_at)
        self.assertEqual(transport.call_args.args[0], "https://api.sandbox.midtrans.com/v2/QRIS-validation/status")
        self.assertEqual(transport.call_args.kwargs["auth"], ("SB-Mid-server-fixture", ""))
        self.assertFalse(transport.call_args.kwargs["allow_redirects"])
        call_command("approve_tenant_qris", business=self.business.slug, environment="sandbox", evidence="local-contract-fixture")
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.verification_state, PaymentConnection.Verification.VERIFIED)
        self.assertIsNotNone(self.connection.verified_at)

    @patch("core.payments.midtrans.requests.get")
    def test_errors_leave_checkout_disabled_and_persist_safe_status(self, transport):
        for status in (401, 403, 404, 502):
            with self.subTest(status=status):
                transport.return_value = Mock(status_code=status)
                self.verify()
                self.connection.refresh_from_db()
                self.assertEqual(self.connection.verification_state, PaymentConnection.Verification.FAILED)
                self.assertIsNone(self.connection.verified_at)
        transport.return_value = self.reply(merchant_id="different-merchant")
        self.verify()
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.verification_state, PaymentConnection.Verification.AUTHENTICATED)
        self.assertIsNone(self.connection.verified_at)
        transport.return_value = self.reply(payment_type="credit_card")
        self.verify()
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.verification_state, PaymentConnection.Verification.AUTHENTICATED)
        transport.side_effect = requests.Timeout
        self.verify()
        self.connection.refresh_from_db()
        self.assertIn("timed out", self.connection.last_verification_result)
        self.assertIsNone(self.connection.verified_at)

    @patch("core.payments.midtrans.requests.get")
    def test_bca_requires_provider_approval_and_stays_disabled(self, transport):
        self.client.post(self.url, {
            "action": "save", "provider": "bca_qris", "environment": "sandbox", "merchant_id": "test-bca",
            "client_id": "fixture", "client_secret": "fixture-only",
        })
        connection = self.business.payment_connections.get(provider="bca_qris")
        self.client.post(self.url, {"action": "verify", "connection_id": connection.pk})
        connection.refresh_from_db()
        self.assertEqual(connection.verification_state, PaymentConnection.Verification.EXTERNAL)
        self.assertIsNone(connection.verified_at)
        transport.assert_not_called()
