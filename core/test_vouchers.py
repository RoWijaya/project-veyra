import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest.mock import MagicMock, patch
from uuid import uuid4

from cryptography.fernet import Fernet
from django.core import signing
from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    Category, Customer, DiningTable, Location, Membership, ModifierGroup, ModifierOption, Printer,
    Product, Promotion, Station, User,
)
from .services import create_business, issue_station_pairing_code, pair_station_device, save_payment_connection


class VoucherFixture:
    def setUp(self):
        self.owner = User.objects.create_user("voucher-owner@example.invalid")
        self.business = create_business(owner=self.owner, slug="voucher-tests", name="Voucher Shop")
        self.staff = Client()
        self.staff.force_login(self.owner)
        self.location = self.business.locations.get()
        self.product = Product.objects.create(business=self.business, name="Coffee", price="30000.00")
        table = DiningTable.objects.create(business=self.business, location=self.location, name="T1")
        token = signing.dumps([str(table.pk), str(table.qr_version)], salt="veyra-table-qr")
        self.cart = reverse("your_order", args=[token])
        self.pos = reverse("point_of_sale", args=[self.business.slug])
        self.management = "/app/voucher-tests/promotions/vouchers/"
        self.voucher = {
            "name": "Welcome offer", "code": "welcome", "active": "on",
            "discount_type": "percent", "value": "10", "minimum_subtotal": "20000",
            "maximum_discount": "2500", "redemption_limit": "1",
        }

    def create_voucher(self, **changes):
        response = self.staff.post(self.management, {**self.voucher, **changes})
        self.assertEqual(response.status_code, 302)
        return response.url

    def add_to_cart(self, client):
        client.get(self.cart)
        response = client.post(self.cart, {"action": "add", "revision": 0, "product": self.product.pk})
        self.assertEqual(response.status_code, 302)

    def apply_guest(self, client, code="welcome"):
        return client.post(self.cart, {"action": "apply_voucher", "revision": 1, "voucher_code": code})

    def checkout_guest(self, client, **changes):
        page = client.get(self.cart)
        return client.post(self.cart, {
            "action": "checkout", "revision": page.context["cart"].revision, "quote": page.context["cart_quote"], **changes,
        })

    def pos_cart(self, **changes):
        return {
            f"quantity_{self.product.pk}": "1", "order_type": "takeaway", "location": self.location.pk,
            "voucher_code": "welcome", "idempotency_key": str(uuid4()), **changes,
        }

    def pos_preview(self, client, payload):
        return client.post(self.pos, {**payload, "action": "apply_voucher"}, HTTP_ACCEPT="application/json")


class VoucherWorkflowTests(VoucherFixture, TestCase):
    def test_guest_voucher_preview_checkout_and_historical_receipt(self):
        self.create_voucher()
        self.add_to_cart(self.client)
        self.assertEqual(self.apply_guest(self.client, " Welcome ").status_code, 302)
        self.assertContains(self.client.get(self.cart), "27500")
        response = self.checkout_guest(self.client)
        self.assertEqual(response.status_code, 302)
        track = self.client.get(response.url)
        self.assertContains(track, "WELCOME")
        self.assertContains(track, "27500")
        order_id = track.context["order"].pk
        self.staff.post(reverse("record_payment", args=[self.business.slug, order_id]), {
            "method": "cash", "cash_received": "27500", "idempotency_key": str(uuid4()),
        })
        receipt = self.staff.get(reverse("order_receipt", args=[self.business.slug, order_id]))
        self.assertContains(receipt, "WELCOME")
        self.assertContains(receipt, "2500")
        another_guest = Client()
        self.add_to_cart(another_guest)
        self.assertContains(self.apply_guest(another_guest), "redemption limit", status_code=400)

    def test_pos_discount_uses_promoted_base_without_modifier_surcharge(self):
        group = ModifierGroup.objects.create(business=self.business, name="Milk")
        group.products.add(self.product)
        option = ModifierOption.objects.create(group=group, name="Oat", surcharge=Decimal("6000.50"))
        promotion = Promotion.objects.create(
            business=self.business, name="Happy hour", discount_type=Promotion.DiscountType.PERCENT, value=17,
        )
        promotion.products.add(self.product)
        detail = self.create_voucher(value="13.37", maximum_discount="", redemption_limit="")
        form = self.voucher | {"value": "13.37", "maximum_discount": "", "redemption_limit": "",
                               "stack_with_promotions": "on"}
        payload = self.pos_cart(**{f"modifiers_{self.product.pk}_{group.pk}": option.pk})
        denied = self.pos_preview(self.staff, payload)
        self.assertEqual(denied.status_code, 400)
        self.assertIn("combined", denied.json()["error"])
        self.assertEqual(self.staff.post(detail, form).status_code, 302)
        quote = self.pos_preview(self.staff, payload).json()
        self.assertEqual(quote["discount"], "3329.13")
        self.assertEqual(quote["total"], "27571.37")
        self.assertFalse(self.business.orders.exists())
        payload.update(voucher_quote_token=quote["token"], total="1", voucher_discount="29999")
        changed = self.staff.post(self.pos, {**payload, f"quantity_{self.product.pk}": "2"})
        self.assertContains(changed, "Apply the voucher again", status_code=400)
        self.assertFalse(self.business.orders.exists())
        self.assertEqual(self.staff.post(self.pos, payload).status_code, 302)
        self.assertEqual(self.staff.post(self.pos, payload).status_code, 302)
        order = self.business.orders.get()
        self.assertEqual(order.total, Decimal("27571.37"))
        self.assertEqual(order.voucher_discount, Decimal("3329.13"))
        self.assertEqual(self.business.vouchers.get().redemptions.count(), 1)
        self.assertContains(self.staff.get(detail), "1 redeemed")
        self.assertContains(self.staff.get(reverse("order_history", args=[self.business.slug])), "WELCOME")
        self.staff.post(reverse("record_payment", args=[self.business.slug, order.pk]), {
            "method": "cash", "cash_received": "28000", "idempotency_key": str(uuid4()),
        })
        report = self.staff.get(reverse("reports", args=[self.business.slug]))
        row = report.context["voucher_performance"].get()
        self.assertEqual(row["paid_sales"], Decimal("27571.37"))
        self.assertEqual(row["average_sale"], Decimal("27571.37"))
        self.assertEqual(row["redemptions"], 1)
        self.assertEqual(row["discounts"], Decimal("3329.13"))
        printer = Printer.objects.create(business=self.business, location=self.location, name="Counter")
        station = Station.objects.create(
            business=self.business, location=self.location, printer=printer, name="Till",
            kind="register", capabilities=["orders.view"],
        )
        _, pairing_code = issue_station_pairing_code(station=station, actor=self.owner)
        _, token = pair_station_device(code=pairing_code, label="Voucher receipt tester")
        device_jobs = f"/api/stations/{station.pk}/print-jobs/"
        receipt_url = reverse("order_receipt", args=[self.business.slug, order.pk])
        first = self.staff.post(receipt_url + "?format=json", {
            "station": station.pk, "action": "print", "idempotency_key": uuid4(),
        })
        self.assertEqual(first.status_code, 201)
        self.assertEqual(self.client.post(device_jobs + first.json()["job"] + "/ack/", {
            "status": "printed",
        }, content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}").status_code, 200)
        self.assertEqual(self.staff.post(detail, {**form, "code": "RENAMED"}).status_code, 302)
        reprint = self.staff.post(receipt_url + "?format=json", {
            "station": station.pk, "action": "reprint", "idempotency_key": uuid4(),
        })
        self.assertEqual(reprint.status_code, 201)
        reissued = order.print_jobs.get(pk=reprint.json()["job"]).ticket_payload
        self.assertEqual(reissued["voucher"], {"code": "WELCOME", "discount": "3329.13"})
        self.assertEqual(reissued["total"], "27571.37")
        receipt = self.staff.get(reverse("order_receipt", args=[self.business.slug, order.pk]), {"width": "58"})
        self.assertContains(receipt, "WELCOME")
        self.assertContains(receipt, "3329.13")

    def test_per_customer_scope_and_cancellation_keep_capacity_committed(self):
        self.create_voucher(customer_limit="1", redemption_limit="2", order_type="takeaway",
                            locations=[self.location.pk], products=[self.product.pk])
        customer = Customer.objects.create(business=self.business, name="Regular")
        other = Customer.objects.create(business=self.business, name="New customer")
        payload = self.pos_cart()
        self.assertIn("customer record", self.pos_preview(self.staff, payload).json()["error"])
        payload.update(customer=customer.pk, order_type="dine_in")
        self.assertIn("order type", self.pos_preview(self.staff, payload).json()["error"])
        payload["order_type"] = "takeaway"
        code = self.pos_preview(self.staff, payload).json()
        self.assertEqual(self.staff.post(self.pos, {**payload, "voucher_quote_token": code["token"]}).status_code, 302)
        order = self.business.orders.get()
        self.staff.post(reverse("update_order_status", args=[self.business.slug, order.pk]), {"status": "cancelled"})
        order.refresh_from_db()
        self.assertEqual(order.status, "cancelled")
        self.assertIn("customer has reached", self.pos_preview(self.staff, payload).json()["error"])
        self.assertEqual(self.pos_preview(self.staff, {**payload, "customer": other.pk}).status_code, 200)

    def test_tenant_scope_stale_guest_and_rate_limit(self):
        detail = self.create_voucher()
        outsider = User.objects.create_user("other-voucher-owner@example.invalid")
        other_business = create_business(owner=outsider, slug="other-voucher-shop", name="Other Shop")
        foreign_product = Product.objects.create(business=other_business, name="Tea", price=30000)
        self.staff.force_login(outsider)
        self.assertEqual(self.staff.get(detail).status_code, 404)
        Membership.objects.create(business=self.business, user=outsider, permissions=["catalog.view", "orders.view"])
        self.assertEqual(self.staff.get(detail).status_code, 403)
        self.assertEqual(self.staff.post(detail, self.voucher).status_code, 403)
        other_pos = reverse("point_of_sale", args=[other_business.slug])
        result = self.staff.post(other_pos, {
            "action": "apply_voucher", "order_type": "takeaway", f"quantity_{foreign_product.pk}": "1",
            "voucher_code": "WELCOME",
        }, HTTP_ACCEPT="application/json")
        self.assertEqual(result.status_code, 400)
        self.assertIn("unavailable", result.json()["error"])
        self.staff.force_login(self.owner)
        self.assertContains(self.staff.post(detail, {**self.voucher, "products": [foreign_product.pk]}),
                            "Select a valid choice", status_code=400)
        self.assertContains(self.staff.post(self.management, self.voucher), "already exists", status_code=400)
        self.add_to_cart(self.client)
        self.assertEqual(self.apply_guest(self.client).status_code, 302)
        stale = self.client.get(self.cart).context
        self.assertEqual(self.staff.post(detail, {**self.voucher, "active": ""}).status_code, 302)
        response = self.client.post(self.cart, {
            "action": "checkout", "revision": stale["cart"].revision, "quote": stale["cart_quote"],
        })
        self.assertContains(response, "This voucher is unavailable", status_code=400)
        removed = self.client.post(self.cart, {"action": "remove_voucher", "revision": stale["cart"].revision})
        self.assertEqual(removed.status_code, 302)
        page = self.client.get(self.cart)
        attempts = {"action": "apply_voucher", "revision": page.context["cart"].revision, "voucher_code": "UNKNOWN"}
        for _ in range(24):
            self.assertEqual(self.client.post(self.cart, attempts, REMOTE_ADDR="192.0.2.80").status_code, 400)
        self.assertContains(self.client.post(self.cart, attempts, REMOTE_ADDR="192.0.2.80"),
                            "Too many voucher attempts", status_code=429)
        self.assertFalse(self.business.orders.exists())

    def test_merchant_dates_and_fixed_discount_cap(self):
        detail = self.create_voucher(starts_at="2000-01-01T09:00", ends_at="2000-01-02T09:00")
        voucher = self.business.vouchers.get()
        self.assertEqual(voucher.starts_at.isoformat(), "2000-01-01T02:00:00+00:00")
        self.assertIn("expired", self.pos_preview(self.staff, self.pos_cart()).json()["error"])
        self.assertEqual(self.staff.post(detail, {
            **self.voucher, "discount_type": "fixed", "value": "40000", "maximum_discount": "",
        }).status_code, 302)
        response = self.pos_preview(self.staff, self.pos_cart())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], "0.00")

    def test_category_location_and_minimum_are_authoritative(self):
        category = Category.objects.create(business=self.business, name="Coffee")
        cart = self.pos_cart()
        other_location = Location.objects.create(business=self.business, name="Second location")
        self.create_voucher(locations=[self.location.pk], categories=[category.pk], minimum_subtotal="40000")
        self.assertIn("location", self.pos_preview(self.staff, {**cart, "location": other_location.pk}).json()["error"])
        self.assertIn("does not apply", self.pos_preview(self.staff, cart).json()["error"])
        self.product.category = category
        self.product.save(update_fields=["category"])
        self.assertIn("minimum purchase", self.pos_preview(self.staff, cart).json()["error"])
        self.assertEqual(self.pos_preview(self.staff, {**cart, f"quantity_{self.product.pk}": "2"}).status_code, 200)

    @override_settings(PAYMENT_CREDENTIALS_ENCRYPTION_KEY=Fernet.generate_key().decode())
    @patch("urllib.request.urlopen")
    def test_voucher_stays_committed_across_qris_failure_expiry_and_settlement(self, provider_request):
        key = "SB-Mid-server-TESTVOUCHER123456"
        signing_key = "voucher-notification-signature"
        connection = save_payment_connection(
            business=self.business, actor=self.owner, provider="midtrans", environment="sandbox",
            merchant_id="VOUCHER-QA", credentials={"server_key": key, "signature_secret_key": signing_key},
        )
        connection.verified_at = timezone.now()
        connection.verification_state = "verified"
        connection.approval_reference = "test-approval"
        connection.save(update_fields=["verified_at", "verification_state", "approval_reference"])
        self.create_voucher(redemption_limit="3")
        for index, (provider_status, order_status) in enumerate(
            [("deny", "failed"), ("expire", "expired"), ("settlement", "paid")]
        ):
            transaction_id = f"voucher-qris-test-{index}"
            mocked = MagicMock()
            mocked.__enter__.return_value = mocked
            mocked.read.return_value = json.dumps({
                "status_code": "201", "transaction_id": transaction_id, "qr_string": "000201voucher-qris",
            }).encode()
            provider_request.return_value = mocked
            guest = Client()
            self.add_to_cart(guest)
            self.assertEqual(self.apply_guest(guest).status_code, 302)
            placed = self.checkout_guest(guest, payment_method="qris:midtrans")
            self.assertEqual(placed.status_code, 302)
            tracking = guest.get(placed.url)
            self.assertContains(tracking, 'data-payment-status="Pending"')
            order = tracking.context["order"]
            payment = tracking.context["payment"]
            self.assertEqual(payment.amount, Decimal("27500.00"))
            signature = hashlib.sha512(f"{payment.merchant_reference}20027500{signing_key}".encode()).hexdigest()
            notification = {
                "order_id": payment.merchant_reference, "status_code": "200", "gross_amount": "27500",
                "transaction_status": provider_status, "fraud_status": "accept",
                "transaction_id": transaction_id, "signature_key": signature,
            }
            mocked.read.return_value = json.dumps({**notification, "payment_type": "qris"}).encode()
            webhook = reverse("payment_webhook", args=["midtrans"])
            for _ in range(2):
                self.assertEqual(guest.post(webhook, notification, content_type="application/json").status_code, 200)
            order.refresh_from_db()
            self.assertEqual(order.payment_status, order_status)
            self.assertEqual(order.voucher_discount, Decimal("2500.00"))
            self.assertEqual(self.business.vouchers.get().redemptions.count(), index + 1)
        self.assertEqual(self.business.orders.count(), 3)


class ConcurrentVoucherTests(VoucherFixture, TransactionTestCase):
    def test_two_transactions_can_redeem_only_once(self):
        self.create_voucher()
        clients = [Client(), Client()]
        payloads = [self.pos_cart(), self.pos_cart()]
        for client, payload in zip(clients, payloads):
            client.force_login(self.owner)
            quote = self.pos_preview(client, payload)
            self.assertEqual(quote.status_code, 200)
            payload["voucher_quote_token"] = quote.json()["token"]
        start = Barrier(2)

        def checkout(client, payload):
            close_old_connections()
            try:
                start.wait(timeout=10)
                return client.post(self.pos, payload).status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(checkout, client, payload) for client, payload in zip(clients, payloads)]
            self.assertEqual(sorted(result.result(timeout=20) for result in results), [302, 400])
        self.assertEqual(self.business.vouchers.get().redemptions.count(), 1)
        self.assertEqual(self.business.orders.count(), 1)
