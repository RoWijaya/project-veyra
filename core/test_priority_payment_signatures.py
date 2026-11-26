import hashlib
import json
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import PaymentConnection, PaymentTransaction
from .services import save_payment_connection
from .test_audit_remediation import ShopFixture


@override_settings(PAYMENT_CREDENTIALS_ENCRYPTION_KEY=Fernet.generate_key().decode())
class TenantSignatureTests(ShopFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.order_record = self.order()
        self.server_key = "SB-Mid-server-test-API"
        self.signature_secret = "test-signing-only"
        self.connection = save_payment_connection(
            business=self.business, actor=self.owner, provider="midtrans", environment="sandbox",
            merchant_id="merchant-fixture", credentials={
                "server_key": self.server_key, "signature_secret_key": self.signature_secret,
            },
        )
        self.attempt = PaymentTransaction.objects.create(
            business=self.business, order=self.order_record, connection=self.connection,
            provider="midtrans", environment="sandbox", expires_at=timezone.now() + timedelta(minutes=10),
            merchant_reference="ORD-SPLIT-FIXTURE", amount=self.order_record.total,
            provider_transaction_id="synthetic-transaction",
        )
        self.payload = {
            "order_id": "ORD-SPLIT-FIXTURE", "status_code": "200", "gross_amount": "25000.00",
            "transaction_id": "synthetic-transaction", "transaction_status": "settlement", "payment_type": "qris",
        }

    def signed(self, key, **changes):
        payload = {**self.payload, **changes}
        message = payload["order_id"] + payload["status_code"] + payload["gross_amount"] + key
        return {**payload, "signature_key": hashlib.sha512(message.encode()).hexdigest()}

    def callback(self, payload):
        return self.client.post("/api/payments/webhooks/midtrans/", payload, content_type="application/json")

    @patch("urllib.request.urlopen")
    def test_tenant_accepts_only_the_configured_signing_key_and_replay_keeps_payment(self, transport):
        for payload in (
            self.payload, {**self.payload, "signature_key": "invalid"}, self.signed(self.server_key),
            self.signed("different-tenant-signature"),
        ):
            self.assertEqual(self.callback(payload).status_code, 401)
        transport.assert_not_called()
        transport.return_value = BytesIO(json.dumps(self.payload).encode())
        for _ in range(2):
            self.assertEqual(self.callback(self.signed(self.signature_secret)).status_code, 200)
        transport.assert_called_once()
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.status, PaymentTransaction.Status.PAID)
        transport.return_value = BytesIO(json.dumps({**self.payload, "transaction_status": "expire"}).encode())
        self.assertEqual(self.callback(self.signed(self.signature_secret, transaction_status="expire")).status_code, 200)
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.status, PaymentTransaction.Status.PAID)

    def test_unknown_transaction_cannot_be_applied(self):
        self.assertEqual(self.callback(self.signed(self.signature_secret, order_id="unknown")).status_code, 400)
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.status, PaymentTransaction.Status.PENDING)


@override_settings(
    VEYRA_BILLING_MIDTRANS_SERVER_KEY="synthetic-platform-api",
    VEYRA_BILLING_MIDTRANS_SIGNATURE_SECRET_KEY="synthetic-platform-signing",
)
class BillingSignatureTests(TestCase):
    def test_signature_is_checked_before_platform_reference_lookup(self):
        reference = "VB-00000000000040008000000000000000"
        payload = {"order_id": reference, "status_code": "200", "gross_amount": "100.00"}
        url = reverse("billing_notification")
        for key, status in ((None, 403), ("synthetic-platform-api", 403), ("synthetic-platform-signing", 404)):
            signed = dict(payload)
            if key:
                signed["signature_key"] = hashlib.sha512((reference + "200" + "100.00" + key).encode()).hexdigest()
            self.assertEqual(self.client.post(url, signed, content_type="application/json").status_code, status)
