import base64
import hashlib
import json
from datetime import timedelta
from io import BytesIO, StringIO
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.core.management import call_command
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

    @patch("urllib.request.urlopen")
    def test_tenant_rotation_preserves_existing_attempts_then_honors_revocation(self, transport):
        self.assertEqual(self.callback(self.signed("retired-key")).status_code, 401)
        new_key = "SB-Mid-server-replacement"
        save_payment_connection(
            business=self.business, actor=self.owner, provider="midtrans", environment="sandbox",
            merchant_id="merchant-fixture", credentials={
                "server_key": new_key, "signature_secret_key": "replacement-signing",
            },
        )
        transport.return_value = BytesIO(json.dumps(self.payload).encode())
        self.assertEqual(self.callback(self.signed(self.signature_secret)).status_code, 200)
        authorized = transport.call_args.args[0].get_header("Authorization")
        self.assertEqual(base64.b64decode(authorized.removeprefix("Basic ")).decode(), new_key + ":")
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.status, PaymentTransaction.Status.PAID)
        call_command("retire_payment_signatures", connection=self.connection.pk, stdout=StringIO())
        self.assertEqual(self.callback(self.signed(self.signature_secret)).status_code, 401)
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.status, PaymentTransaction.Status.PAID)

    @patch("urllib.request.urlopen")
    def test_only_the_explicitly_listed_tenant_can_use_legacy_signing(self, transport):
        save_payment_connection(
            business=self.business, actor=self.owner, provider="midtrans", environment="sandbox",
            merchant_id="merchant-fixture", credentials={"server_key": self.server_key},
        )
        self.assertEqual(self.callback(self.signed(self.server_key)).status_code, 401)
        with override_settings(MIDTRANS_LEGACY_SIGNING_CONNECTIONS={"987654321"}):
            self.assertEqual(self.callback(self.signed(self.server_key)).status_code, 401)
        transport.assert_not_called()
        transport.return_value = BytesIO(json.dumps(self.payload).encode())
        with override_settings(MIDTRANS_LEGACY_SIGNING_CONNECTIONS={str(self.connection.pk)}):
            self.assertEqual(self.callback(self.signed(self.server_key)).status_code, 200)

    @patch("urllib.request.urlopen")
    def test_unbound_confirmation_requires_the_expected_merchant(self, transport):
        self.attempt.provider_transaction_id = ""
        self.attempt.save(update_fields=["provider_transaction_id"])
        for merchant in ({}, {"merchant_id": "different-merchant"}):
            transport.return_value = BytesIO(json.dumps({**self.payload, **merchant}).encode())
            self.assertEqual(self.callback(self.signed(self.signature_secret)).status_code, 503)
            self.attempt.refresh_from_db()
            self.assertEqual(self.attempt.status, PaymentTransaction.Status.PENDING)
            self.assertEqual(self.attempt.provider_transaction_id, "")
        transport.return_value = BytesIO(json.dumps({**self.payload, "merchant_id": "merchant-fixture"}).encode())
        self.assertEqual(self.callback(self.signed(self.signature_secret)).status_code, 200)
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


    @override_settings(
        VEYRA_BILLING_MIDTRANS_SIGNATURE_SECRET_KEY="",
        VEYRA_BILLING_MIDTRANS_ALLOW_LEGACY_SIGNATURE=False,
    )
    def test_platform_legacy_signing_requires_explicit_compatibility(self):
        reference = "VB-00000000000040008000000000000000"
        payload = {"order_id": reference, "status_code": "200", "gross_amount": "100.00"}
        payload["signature_key"] = hashlib.sha512(
            (reference + "200" + "100.00" + "synthetic-platform-api").encode()
        ).hexdigest()
        url = reverse("billing_notification")
        self.assertEqual(self.client.post(url, payload, content_type="application/json").status_code, 403)
        with override_settings(VEYRA_BILLING_MIDTRANS_ALLOW_LEGACY_SIGNATURE=True):
            self.assertEqual(self.client.post(url, payload, content_type="application/json").status_code, 404)

    @override_settings(VEYRA_BILLING_MIDTRANS_SIGNATURE_SECRET_KEY="synthetic-platform-api")
    def test_platform_does_not_accept_a_copied_api_key_as_the_signature_secret(self):
        reference = "VB-00000000000040008000000000000000"
        payload = {"order_id": reference, "status_code": "200", "gross_amount": "100.00"}
        payload["signature_key"] = hashlib.sha512(
            (reference + "200" + "100.00" + "synthetic-platform-api").encode()
        ).hexdigest()
        self.assertEqual(
            self.client.post(reverse("billing_notification"), payload, content_type="application/json").status_code, 403,
        )
