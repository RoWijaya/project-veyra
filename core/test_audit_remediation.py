import hashlib
import json
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from threading import Barrier, Event
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

from asgiref.sync import async_to_sync, sync_to_async
from channels.testing import WebsocketCommunicator
from cryptography.fernet import Fernet
from django.core import mail
from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone

from config.asgi import application
from .models import (
    AuditEvent, DiningTable, Location, Order, OrderTender, OutboxEvent, PairedDevice, PaymentConnection, PaymentTransaction, Product, Station, User,
)
from .payments.bca_snap import BcaSnapAdapter
from .payments.midtrans import MidtransAdapter
from .permissions import order_tracking_cookie_name, order_tracking_cookie_signer
from .refunds import reserve_refund
from .services import (
    acknowledge_print_job, create_business, create_order, create_print_job, dispatch_outbox_event,
    initiate_qris_payment, record_order_payment, reprint_order_ticket, save_payment_connection,
    transition_order,
)


class ShopFixture:
    def setUp(self):
        super().setUp()
        self.owner = User.objects.create_user("remediation-owner@example.invalid")
        self.business = create_business(owner=self.owner, name="Remediation Cafe", slug="remediation-cafe")
        self.location = self.business.locations.get()
        self.product = Product.objects.create(business=self.business, name="Coffee", price=25000)
        self.client.force_login(self.owner)

    def order(self, **options):
        return create_order(
            business=self.business, actor=self.owner, items=[(self.product.pk, 1)],
            location_id=self.location.pk, **options,
        )

    def tender(self, order, **options):
        return record_order_payment(
            business=self.business, actor=self.owner, order_id=order.pk,
            **{"method": "cash", "idempotency_key": uuid4(), "cash_received": "25000", **options},
        )

    def advance(self, order, status):
        return transition_order(business=self.business, actor=self.owner, order_id=order.pk, status=status)


class CommerceRemediationTests(ShopFixture, TestCase):
    def test_counter_checkout_can_prepare_then_requires_payment_for_completion(self):
        order = self.order()
        self.advance(order, Order.Status.PREPARING)
        self.advance(order, Order.Status.READY)
        status = reverse("update_order_status", args=[self.business.slug, order.pk])
        self.client.post(status, {"status": "completed"})
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.READY)
        self.assertEqual(order.payment_status, Order.PaymentStatus.UNPAID)
        self.tender(order)
        self.client.post(status, {"status": "completed"})
        self.client.post(status, {"status": "cancelled"})
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.COMPLETED)
        self.assertEqual(order.payment_status, Order.PaymentStatus.PAID)
        self.assertEqual(self.client.get(reverse("order_receipt", args=[self.business.slug, order.pk])).status_code, 200)

    def test_legacy_positive_balance_can_be_collected_after_completion(self):
        order = self.order()
        Order.objects.filter(pk=order.pk).update(status=Order.Status.COMPLETED)
        pos = reverse("point_of_sale", args=[self.business.slug])
        self.assertContains(self.client.get(pos), "Record payment")
        tender = self.tender(order)
        self.assertEqual(self.tender(order, idempotency_key=tender.idempotency_key).pk, tender.pk)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.COMPLETED)
        self.assertEqual(order.payment_status, Order.PaymentStatus.PAID)

    def test_report_filter_and_graph_share_business_days(self):
        order = self.order()
        tender = self.tender(order)
        Order.objects.filter(pk=order.pk).update(created_at=datetime(2026, 3, 8, 7, 59, tzinfo=UTC))
        type(tender).objects.filter(pk=tender.pk).update(created_at=datetime(2026, 3, 9, 8, 0, tzinfo=UTC))
        report = reverse("reports", args=[self.business.slug])
        for zone, correct_day, incorrect_day in [
            ("Asia/Jakarta", "2026-03-08", "2026-03-09"),
            ("UTC", "2026-03-08", "2026-03-09"),
            ("America/Los_Angeles", "2026-03-07", "2026-03-08"),
        ]:
            with self.subTest(zone=zone):
                self.business.timezone_name = zone
                self.business.save(update_fields=["timezone_name"])
                inside = self.client.get(report, {"start": correct_day, "end": correct_day})
                self.assertEqual(inside.context["paid_total"], Decimal("25000.00"))
                self.assertEqual([str(row["day"]) for row in inside.context["sales_by_day"]], [correct_day])
                outside = self.client.get(report, {"start": incorrect_day, "end": incorrect_day})
                self.assertEqual(outside.context["paid_total"], Decimal("0.00"))

    def test_dst_report_includes_midnight_and_keeps_refund_with_sale(self):
        self.business.timezone_name = "America/Los_Angeles"
        self.business.save(update_fields=["timezone_name"])
        instants = [
            datetime(2026, 3, 8, 8, 0, tzinfo=UTC),
            datetime(2026, 3, 9, 6, 59, 59, tzinfo=UTC),
            datetime(2026, 3, 9, 7, 0, tzinfo=UTC),
        ]
        for instant in instants:
            order = self.order()
            self.tender(order)
            Order.objects.filter(pk=order.pk).update(created_at=instant)
            if instant == instants[0]:
                with patch("django.utils.timezone.now", return_value=instants[-1] + timedelta(days=2)):
                    reserve_refund(
                        business=self.business, actor=self.owner, order_id=order.pk, amount=Decimal("10000"),
                        reason="Local verification", idempotency_key=uuid4(),
                    )
        path = reverse("reports", args=[self.business.slug])
        result = self.client.get(path, {"start": "2026-03-08", "end": "2026-03-08"}).context
        self.assertEqual(result["paid_total"], Decimal("50000"))
        self.assertEqual(result["refunded_total"], Decimal("10000"))
        self.assertEqual(result["retained_total"], Decimal("40000"))
        tomorrow = self.client.get(path, {"start": "2026-03-09", "end": "2026-03-09"}).context
        self.assertEqual(tomorrow["paid_total"], Decimal("25000"))
        self.assertEqual(tomorrow["refunded_total"], Decimal("0"))

    def test_print_acknowledgement_is_terminal_and_retry_has_its_own_job(self):
        order = self.order()
        station = Station.objects.create(business=self.business, name="Kitchen", kind="kitchen")
        initial = create_print_job(business=self.business, order=order, station=station)
        def acknowledge(job, status):
            return acknowledge_print_job(
                business=self.business, job_id=job.pk, status=status, actor=self.owner,
            )
        acknowledge(initial, "printing")
        acknowledge(initial, "failed")
        retry = reprint_order_ticket(
            business=self.business, order_id=order.pk, station_id=station.pk, actor=self.owner,
        )
        acknowledge(retry, "printed")
        printed = acknowledge(retry, "printed").printed_at
        self.assertEqual(acknowledge(initial, "printing").status, "failed")
        self.assertEqual(acknowledge(retry, "printing").status, "printed")
        self.assertEqual(acknowledge(retry, "printed").printed_at, printed)
        self.assertNotEqual(initial.pk, retry.pk)


class SignupRemediationTests(TestCase):
    def test_email_verification_in_another_browser_can_set_a_new_password(self):
        self.client.post(reverse("register"), {
            "email": "registration@example.invalid", "first_name": "New user",
            "password1": "First-Local-Signup-Password-2026",
            "password2": "First-Local-Signup-Password-2026",
        })
        path = urlsplit(mail.outbox[0].body.split("Confirm your email: ", 1)[1]).path
        other_browser = Client(enforce_csrf_checks=True)
        response = other_browser.get(path)
        self.assertContains(response, "Choose your password")
        self.assertFalse(User.objects.get(email="registration@example.invalid").is_active)
        self.assertEqual(other_browser.post(path, {}).status_code, 403)
        csrf = other_browser.cookies["csrftoken"].value
        response = other_browser.post(path, {
            "new_password1": "Verified-Mailbox-Password-2026",
            "new_password2": "Verified-Mailbox-Password-2026",
            "csrfmiddlewaretoken": csrf,
        })
        self.assertContains(response, "Your email is verified")
        user = User.objects.get(email="registration@example.invalid")
        self.assertTrue(user.is_active)
        self.assertTrue(user.check_password("Verified-Mailbox-Password-2026"))
        self.assertFalse(user.check_password("First-Local-Signup-Password-2026"))
        self.assertEqual(other_browser.get(path).status_code, 400)


@override_settings(PAYMENT_CREDENTIALS_ENCRYPTION_KEY=Fernet.generate_key().decode())
class TenantPaymentRemediationTests(ShopFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.connection = save_payment_connection(
            business=self.business, actor=self.owner, provider="midtrans", environment="sandbox",
            merchant_id="test-merchant", credentials={"server_key": "SB-Mid-server-synthetic-fixture",
                                                      "signature_secret_key": "synthetic-remediation-signing"},
        )
        self.connection.verified_at = timezone.now()
        self.connection.verification_state = "verified"
        self.connection.approval_reference = "test-approval"
        self.connection.save(update_fields=["verified_at", "verification_state", "approval_reference"])

    def initiate(self, order):
        return initiate_qris_payment(
            business=self.business, order_id=order.pk, actor=self.owner, provider="midtrans",
        )

    @patch("urllib.request.urlopen")
    def test_provider_serialization_preserves_instants(self, request):
        instant = datetime(2026, 3, 8, 17, 55, tzinfo=UTC)
        connection = SimpleNamespace(environment="sandbox", merchant_id="local")
        order = SimpleNamespace(pk=uuid4())
        for zone in ("UTC", "Asia/Jakarta"):
            with self.subTest(zone=zone), patch("django.utils.timezone.now", return_value=instant.astimezone(ZoneInfo(zone))):
                for adapter, credentials, reply in [
                    (MidtransAdapter(), {"server_key": "synthetic"}, {"status_code": "201", "qr_string": "local"}),
                    (BcaSnapAdapter(), {"client_id": "local", "client_secret": "synthetic"}, {"responseCode": "200", "qrContent": "local"}),
                ]:
                    request.return_value = BytesIO(json.dumps(reply).encode())
                    result = adapter.create_qris(
                        connection=connection, credentials=credentials, order=order, amount=Decimal("25000"),
                    )
                    body = json.loads(request.call_args.args[0].data)
                    expires = instant + timedelta(minutes=10)
                    if isinstance(adapter, MidtransAdapter):
                        purchase_time = datetime.strptime(body["custom_expiry"]["order_time"], "%Y-%m-%d %H:%M:%S %z")
                        self.assertEqual(purchase_time, instant)
                        self.assertEqual(body["custom_expiry"]["expiry_duration"], 10)
                    else:
                        self.assertEqual(datetime.fromisoformat(body["validityPeriod"]), expires)
                        self.assertEqual(datetime.fromisoformat(request.call_args.args[0].get_header("X-timestamp")), instant)
                    self.assertEqual(result.expires_at, expires)

    @patch("urllib.request.urlopen")
    def test_open_qris_reservation_blocks_both_counter_tenders(self, request):
        request.return_value = BytesIO(b'{"status_code":"201","transaction_id":"one","qr_string":"local"}')
        order = self.order()
        attempt = self.initiate(order)
        for method in ("cash", "card"):
            with self.subTest(method=method), self.assertRaises(ValueError):
                self.tender(order, method=method)
        self.assertEqual(self.initiate(order).pk, attempt.pk)
        request.assert_called_once()
        self.assertFalse(hasattr(order, "tender"))

    @patch("urllib.request.urlopen")
    def test_lost_charge_response_keeps_reference_for_retry_and_provider_lookup(self, request):
        order = self.order()
        request.side_effect = TimeoutError("simulated lost provider response")
        with self.assertRaises(ValueError):
            self.initiate(order)
        charge_ref = json.loads(request.call_args.args[0].data)["transaction_details"]["order_id"]
        attempt = order.payments.get()
        self.assertEqual(attempt.merchant_reference, charge_ref)
        self.assertEqual(attempt.amount, order.total)
        with self.assertRaises(ValueError):
            self.initiate(order)
        self.assertTrue(request.call_args.args[0].full_url.endswith(f"/{charge_ref}/status"))
        self.assertEqual(order.payments.count(), 1)

    @patch("urllib.request.urlopen")
    def test_expiration_requires_provider_confirmation_before_a_new_attempt(self, request):
        order = self.order()
        request.return_value = BytesIO(b'{"status_code":"201","transaction_id":"original","qr_string":"local"}')
        attempt = self.initiate(order)
        order.payments.filter(pk=attempt.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        request.side_effect = [
            BytesIO(json.dumps({
                "transaction_id": "original", "order_id": attempt.merchant_reference,
                "gross_amount": "25000.00", "transaction_status": "expire", "payment_type": "qris",
            }).encode()),
            BytesIO(b'{"status_code":"201","transaction_id":"replacement","qr_string":"local two"}'),
        ]
        replacement = self.initiate(order)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, PaymentTransaction.Status.EXPIRED)
        self.assertNotEqual(replacement.merchant_reference, attempt.merchant_reference)
        self.assertEqual(order.payments.count(), 2)


    @patch("urllib.request.urlopen")
    def test_station_payments_use_current_location_and_session(self, request):
        request.return_value = BytesIO(b'{"status_code":"201","transaction_id":"one","qr_string":"local"}')
        secondary = Location.objects.create(business=self.business, name="Second location")
        other_owner = User.objects.create_user("other-owner@example.invalid")
        other_business = create_business(owner=other_owner, name="Other cafe", slug="other-cafe")
        other_product = Product.objects.create(business=other_business, name="Tea", price=25000)
        other_order = create_order(
            business=other_business, actor=other_owner, items=[(other_product.pk, 1)],
            location_id=other_business.locations.get().pk,
        )
        station = Station.objects.create(
            business=self.business, location=self.location, name="Main register",
            kind=Station.Kind.REGISTER, capabilities=["orders.view"],
        )
        token = secrets.token_urlsafe(32)
        device = PairedDevice.objects.create(
            station=station, label="Test register", token_digest=hashlib.sha256(token.encode()).hexdigest(),
        )
        order = self.order()
        def submit(order_id, supplied_location):
            return self.client.post(
                reverse("station_initiate_payment", args=[station.pk, order_id]),
                {"provider": "midtrans", "location_id": supplied_location}, content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {token}",
            )
        self.assertEqual(submit(order.pk, secondary.pk).status_code, 201)
        self.assertEqual(submit(other_order.pk, self.location.pk).status_code, 404)
        self.assertEqual(submit(uuid4(), self.location.pk).status_code, 404)
        Station.objects.filter(pk=station.pk).update(location=secondary)
        self.assertEqual(submit(order.pk, self.location.pk).status_code, 404)
        Station.objects.filter(pk=station.pk).update(active=False, location=self.location)
        self.assertEqual(submit(order.pk, self.location.pk).status_code, 401)
        Station.objects.filter(pk=station.pk).update(active=True)
        PairedDevice.objects.filter(pk=device.pk).update(revoked_at=timezone.now())
        self.assertEqual(submit(order.pk, self.location.pk).status_code, 401)
        request.assert_called_once()

    @patch("urllib.request.urlopen")
    def test_failed_retry_preserves_historical_reference_and_late_capture(self, request):
        order = self.order()
        request.return_value = BytesIO(b'{"status_code":"402"}')
        with self.assertRaises(ValueError):
            self.initiate(order)
        original = order.payments.get(status="failed")
        request.return_value = BytesIO(b'{"status_code":"201","transaction_id":"replacement","qr_string":"local"}')
        replacement = self.initiate(order)
        self.assertNotEqual(original.merchant_reference, replacement.merchant_reference)
        notify = {
            "order_id": original.merchant_reference, "transaction_id": "old-attempt",
            "status_code": "200", "gross_amount": "25000.00", "transaction_status": "settlement",
            "payment_type": "qris", "merchant_id": "test-merchant",
        }
        signature_source = original.merchant_reference + "20025000.00synthetic-remediation-signing"
        notify["signature_key"] = hashlib.sha512(signature_source.encode()).hexdigest()
        for _ in range(2):
            request.return_value = BytesIO(json.dumps(notify).encode())
            response = self.client.post("/api/payments/webhooks/midtrans/", notify, content_type="application/json")
            self.assertEqual(response.status_code, 200)
        original.refresh_from_db()
        replacement.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(original.status, PaymentTransaction.Status.REVIEW)
        self.assertEqual(replacement.status, PaymentTransaction.Status.PENDING)
        self.assertEqual(order.payment_status, Order.PaymentStatus.PENDING)
        self.assertEqual(order.status, Order.Status.NEW)
        self.assertEqual(AuditEvent.objects.filter(action="payment.additional_capture", object_id=str(original.pk)).count(), 1)
        with self.assertRaises(ValueError):
            self.tender(order)

    def test_provider_rejects_naive_request_time_before_transport(self):
        configuration = SimpleNamespace(environment="sandbox", merchant_id="local")
        for adapter, credentials in [
            (MidtransAdapter(), {"server_key": "synthetic"}),
            (BcaSnapAdapter(), {"client_id": "local", "client_secret": "synthetic"}),
        ]:
            with self.subTest(adapter=type(adapter)), patch("urllib.request.urlopen") as transport:
                with self.assertRaises(ValueError):
                    adapter.create_qris(
                        connection=configuration, credentials=credentials, order=SimpleNamespace(pk=uuid4()),
                        amount=Decimal("25000"), requested_at=datetime(2026, 3, 8, 17, 55),
                    )
                transport.assert_not_called()

@skipUnlessDBFeature("has_select_for_update")
class TenderConcurrencyTests(ShopFixture, TransactionTestCase):
    def test_concurrent_tenders_record_one_collection(self):
        order = self.order()
        gate = Barrier(2)
        def collect():
            close_old_connections()
            try:
                gate.wait(timeout=10)
                return self.tender(order).pk
            except ValueError:
                return None
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            receipts = list(pool.map(lambda _: collect(), range(2)))
        self.assertEqual(sum(receipt is not None for receipt in receipts), 1)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, Order.PaymentStatus.PAID)
        self.assertEqual(order.tender.amount, order.total)


    @override_settings(PAYMENT_CREDENTIALS_ENCRYPTION_KEY=Fernet.generate_key().decode())
    def test_provider_confirmation_wins_during_an_inflight_charge(self):
        order = self.order()
        key = "SB-Mid-server-local-concurrency-fixture"
        signing_key = "concurrency-notification-secret"
        connection = save_payment_connection(
            business=self.business, actor=self.owner, provider="midtrans", environment="sandbox",
            merchant_id="local", credentials={"server_key": key, "signature_secret_key": signing_key},
        )
        PaymentConnection.objects.filter(pk=connection.pk).update(
            verified_at=timezone.now(), verification_state="verified", approval_reference="test-approval",
        )
        charge_sent = Event()
        release_charge = Event()
        payload = {}

        def provider(request, timeout):
            if request.get_method() == "GET":
                return BytesIO(json.dumps(payload).encode())
            reference = json.loads(request.data)["transaction_details"]["order_id"]
            payload.update(order_id=reference, transaction_id="charge-one", status_code="200",
                           gross_amount="25000.00", payment_type="qris", transaction_status="settlement",
                           merchant_id="local")
            payload["signature_key"] = hashlib.sha512((reference + "20025000.00" + signing_key).encode()).hexdigest()
            charge_sent.set()
            if not release_charge.wait(timeout=10):
                raise TimeoutError("Local verification deadline")
            return BytesIO(b'{"status_code":"201","transaction_id":"charge-one","qr_string":"local"}')

        def start_payment():
            close_old_connections()
            try:
                return initiate_qris_payment(business=self.business, actor=self.owner, order_id=order.pk, provider="midtrans")
            finally:
                close_old_connections()

        with patch("urllib.request.urlopen", side_effect=provider), ThreadPoolExecutor(max_workers=1) as pool:
            started = pool.submit(start_payment)
            try:
                self.assertTrue(charge_sent.wait(timeout=10))
                with self.assertRaises(ValueError):
                    self.tender(order, method="card")
                response = self.client.post("/api/payments/webhooks/midtrans/", payload, content_type="application/json")
                self.assertEqual(response.status_code, 200)
                with self.assertRaises(ValueError):
                    self.tender(order)
            finally:
                release_charge.set()
            attempt = started.result(timeout=10)
        order.refresh_from_db()
        self.assertEqual(attempt.status, PaymentTransaction.Status.PAID)
        self.assertEqual(order.payment_method, Order.PaymentMethod.QRIS)
        self.assertEqual(order.status, Order.Status.PREPARING)
        self.assertEqual(order.payments.count(), 1)
        self.assertFalse(OrderTender.objects.filter(order=order).exists())


class GuestEventRemediationTests(ShopFixture, TransactionTestCase):
    def test_delayed_outbox_hint_reads_current_order_after_ready(self):
        table = DiningTable.objects.create(business=self.business, location=self.location, name="Audit table")
        token = secrets.token_urlsafe(32)
        order = create_order(
            business=self.business, actor=None, table=table, items=[(self.product.pk, 1)],
            order_type=Order.OrderType.DINE_IN, guest_tracking_token=token,
        )
        channel = f"/ws/guest/orders/{order.pk}/"
        cookie = order_tracking_cookie_signer(order.pk).sign(token)
        headers = [
            (b"origin", b"http://localhost"),
            (b"cookie", f"{order_tracking_cookie_name(order.pk)}={cookie}".encode()),
        ]
        old = OutboxEvent.objects.get(payload__order_id=str(order.pk), payload__status="new")
        self.advance(order, "preparing")
        self.advance(order, "ready")
        latest = OutboxEvent.objects.get(payload__order_id=str(order.pk), payload__status="ready")
        async def observe():
            viewer = WebsocketCommunicator(application, channel, headers=headers)
            connected, _ = await viewer.connect()
            self.assertTrue(connected)
            try:
                snapshot = await viewer.receive_json_from()
                self.assertEqual(snapshot["status"], "ready")
                self.assertFalse(snapshot["payment_needs_review"])
                await sync_to_async(dispatch_outbox_event)(latest.pk)
                self.assertEqual((await viewer.receive_json_from())["status"], "ready")
                await sync_to_async(dispatch_outbox_event)(old.pk)
                self.assertEqual((await viewer.receive_json_from())["status"], "ready")
            finally:
                await viewer.disconnect()
            reconnected = WebsocketCommunicator(application, channel, headers=headers)
            connected, _ = await reconnected.connect()
            self.assertTrue(connected)
            try:
                self.assertEqual((await reconnected.receive_json_from())["status"], "ready")
                await sync_to_async(Order.objects.filter(pk=order.pk).update)(guest_tracking_digest="revoked")
                await sync_to_async(self.tender)(order)
                paid = await sync_to_async(OutboxEvent.objects.get)(payload__order_id=str(order.pk), payload__event="payment_recorded")
                await sync_to_async(dispatch_outbox_event)(paid.pk)
                self.assertEqual((await reconnected.receive_output())["code"], 4403)
            finally:
                await reconnected.disconnect()
        async_to_sync(observe)()
