import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from cryptography.fernet import Fernet
from django.core.management import call_command
from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import AuditEvent, Membership, Order, OrderRefund, PaymentTransaction, Product, User
from .services import create_business, create_order, record_order_payment, save_payment_connection


class RefundFixture:
    def setUp(self):
        self.owner = User.objects.create_user("refund-owner@example.invalid")
        self.business = create_business(owner=self.owner, name="Refund Counter", slug="refund-counter")
        product = Product.objects.create(business=self.business, name="Coffee", price=25000)
        self.order = create_order(business=self.business, actor=self.owner, items=[(product.pk, 2)])
        self.url = reverse("order_refunds", args=[self.business.slug, self.order.pk])
        self.receipt = reverse("order_receipt", args=[self.business.slug, self.order.pk])
        self.client.force_login(self.owner)

    def pay_cash(self):
        return record_order_payment(
            business=self.business, order_id=self.order.pk, method="cash", actor=self.owner,
            idempotency_key=uuid4(), cash_received="55000",
        )

    def data(self, amount="10000", **changes):
        return {"action": "refund", "amount": amount, "reason": "Customer return", "idempotency_key": uuid4(), **changes}

    def midtrans_capture(self):
        self.server_key = "SB-Mid-server-LOCAL-REFUND-FIXTURE"
        self.signing_key = "refund-notification-secret"
        connection = save_payment_connection(
            business=self.business, actor=self.owner, provider="midtrans", environment="sandbox",
            merchant_id="local-fixture", credentials={"server_key": self.server_key, "signature_secret_key": self.signing_key},
        )
        self.order.payment_method = Order.PaymentMethod.QRIS
        self.order.payment_status = Order.PaymentStatus.PAID
        self.order.save(update_fields=["payment_method", "payment_status"])
        return PaymentTransaction.objects.create(
            business=self.business, order=self.order, connection=connection, provider="midtrans",
            environment="sandbox", amount=self.order.total, merchant_reference="ORD-refund-fixture",
            provider_transaction_id="mid-refund-fixture", status="paid", paid_at=timezone.now(),
            expires_at=timezone.now() + timedelta(minutes=10),
        )

    def provider_data(self, payment, status, **extra):
        return {
            "order_id": payment.merchant_reference, "transaction_id": payment.provider_transaction_id,
            "transaction_status": status, "payment_type": "qris", "gross_amount": str(payment.amount),
            "status_code": "200", **extra,
        }


@override_settings(PAYMENT_CREDENTIALS_ENCRYPTION_KEY=Fernet.generate_key().decode())
class RefundTests(RefundFixture, TestCase):
    def test_partial_and_full_cash_refund_preserves_original_capture(self):
        tender = self.pay_cash()
        self.assertContains(self.client.get(self.receipt), "Refunds and payment history")
        data = self.data()
        self.assertRedirects(self.client.post(self.url, data), self.url)
        self.assertRedirects(self.client.post(self.url, data), self.url)
        self.assertContains(self.client.get(self.receipt), "Partially refunded")
        self.assertContains(self.client.post(self.url, {**data, "amount": "20000"}),
                            "already used", status_code=409)
        self.assertContains(self.client.post(self.url, self.data("41000")),
                            "remaining refundable balance", status_code=409)
        self.assertRedirects(self.client.post(self.url, self.data("40000")), self.url)
        self.assertContains(self.client.get(self.receipt), "Fully refunded")
        self.order.refresh_from_db()
        tender.refresh_from_db()
        self.assertEqual(self.order.payment_status, Order.PaymentStatus.PAID)
        self.assertEqual(tender.amount, Decimal("50000.00"))
        self.assertEqual(self.order.refunds.count(), 2)

    def test_owner_only_and_tenant_scoped(self):
        self.pay_cash()
        other = User.objects.create_user("refund-staff@example.invalid")
        self.client.force_login(other)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        Membership.objects.create(user=other, business=self.business, permissions=["orders.view", "orders.manage"])
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.post(self.url, self.data()).status_code, 403)
        self.assertFalse(self.order.refunds.exists())

    @patch("core.payments.midtrans.MidtransAdapter.request_refund")
    @patch("core.payments.midtrans.MidtransAdapter.transaction_status")
    def test_waits_for_bank_confirmation_and_verifies_refund_callback_once(self, status, submit):
        payment = self.midtrans_capture()
        status.return_value = self.provider_data(payment, "settlement")
        submit.return_value = {"status_code": "200"}
        data = self.data()
        self.assertRedirects(self.client.post(self.url, data), self.url)
        refund = self.order.refunds.get()
        self.assertEqual(refund.status, OrderRefund.Status.PENDING)
        self.assertEqual(submit.call_args.kwargs["key"], refund.reference)
        self.assertRedirects(self.client.post(self.url, data), self.url)
        submit.assert_called_once()
        call_command("process_refunds", "--once")
        submit.assert_called_once()
        provider_refund = {
            "refund_key": refund.reference, "refund_chargeback_id": 7001, "refund_amount": "10000",
        }
        payload = self.provider_data(payment, "partial_refund", refunds=[provider_refund])
        payload["signature_key"] = hashlib.sha512(
            f"{payload['order_id']}200{payload['gross_amount']}{self.signing_key}".encode(),
        ).hexdigest()
        status.return_value = payload
        webhook = reverse("payment_webhook", args=["midtrans"])
        raw = json.dumps(payload)
        self.assertEqual(self.client.post(webhook, raw, content_type="application/json").status_code, 200)
        refund.refresh_from_db()
        self.assertEqual(refund.status, OrderRefund.Status.PENDING)
        provider_refund["bank_confirmed_at"] = "2026-11-24 12:00:00"
        for _ in range(2):
            self.assertEqual(self.client.post(webhook, raw, content_type="application/json").status_code, 200)
        refund.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(refund.status, OrderRefund.Status.SUCCEEDED)
        self.assertEqual(refund.provider_reference, "7001")
        self.assertEqual(payment.status, PaymentTransaction.Status.PAID)
        self.assertEqual(self.order.refunds.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(business=self.business, action="refund.confirmed").count(), 1)

    @patch("core.payments.midtrans.MidtransAdapter.request_refund")
    @patch("core.payments.midtrans.MidtransAdapter.transaction_status")
    def test_bound_provider_denial_releases_reservation(self, status, submit):
        payment = self.midtrans_capture()
        status.return_value = self.provider_data(payment, "settlement")
        submit.side_effect = lambda **request: {
            "status_code": "202", "refund_key": request["key"], "transaction_id": payment.provider_transaction_id,
        }
        self.assertRedirects(self.client.post(self.url, self.data("50000")), self.url)
        self.assertEqual(self.order.refunds.get().status, OrderRefund.Status.FAILED)
        self.assertContains(self.client.get(self.url), "Available to refund")

    @patch("core.payments.midtrans.MidtransAdapter.cancel_transaction")
    @patch("core.payments.midtrans.MidtransAdapter.transaction_status")
    def test_cancellation_of_unpaid_qris_requires_provider_confirmation(self, status, cancel):
        payment = self.midtrans_capture()
        PaymentTransaction.objects.filter(pk=payment.pk).update(status="pending", paid_at=None)
        Order.objects.filter(pk=self.order.pk).update(payment_status=Order.PaymentStatus.PENDING)
        url = reverse("update_order_status", args=[self.business.slug, self.order.pk])
        status.return_value = self.provider_data(payment, "settlement")
        self.client.post(url, {"status": "cancelled"})
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.NEW)
        cancel.assert_not_called()
        status.side_effect = [self.provider_data(payment, "pending"), self.provider_data(payment, "cancel")]
        self.assertEqual(self.client.post(url, {"status": "cancelled"}).status_code, 302)
        cancel.assert_called_once()
        self.order.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CANCELLED)
        self.assertEqual(payment.status, PaymentTransaction.Status.FAILED)
        self.assertFalse(self.order.refunds.exists())

    @patch("core.payments.midtrans.MidtransAdapter.transaction_status")
    def test_paid_capture_survives_later_provider_expiry(self, status):
        payment = self.midtrans_capture()
        payload = self.provider_data(payment, "expire")
        payload["signature_key"] = hashlib.sha512(
            f"{payload['order_id']}200{payload['gross_amount']}{self.signing_key}".encode(),
        ).hexdigest()
        status.return_value = payload
        webhook = reverse("payment_webhook", args=["midtrans"])
        self.assertEqual(self.client.post(webhook, payload, content_type="application/json").status_code, 200)
        payment.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(payment.status, PaymentTransaction.Status.PAID)
        self.assertEqual(self.order.payment_status, Order.PaymentStatus.PAID)


class RefundConcurrencyTests(RefundFixture, TransactionTestCase):
    def test_concurrent_refunds_cannot_exceed_capture(self):
        self.pay_cash()
        gate = Barrier(2, timeout=10)

        def refund():
            close_old_connections()
            try:
                client = Client()
                client.force_login(self.owner)
                gate.wait()
                return client.post(self.url, self.data("40000")).status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = [pool.submit(refund) for _ in range(2)]
            self.assertEqual(sorted(result.result(timeout=20) for result in statuses), [302, 409])
        self.assertEqual(self.order.refunds.get().amount, Decimal("40000.00"))
