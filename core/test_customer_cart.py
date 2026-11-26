import hashlib
import json
import re
from decimal import Decimal
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet

from django.core import signing
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from .models import Category, DiningTable, Membership, Product, Promotion, RecipeLine, User
from .services import create_business, create_inventory_item, create_location, save_payment_connection


class CustomerCartTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("cart-owner@example.invalid", "cart-test-password-42!")
        self.business = create_business(owner=self.owner, name="Cart cafe", slug="cart-cafe")
        self.table = DiningTable.objects.create(
            business=self.business, location=self.business.locations.get(), name="Table 4"
        )
        self.product = Product.objects.create(business=self.business, name="Kopi", price="25000")
        self.token = signing.dumps(
            [str(self.table.pk), str(self.table.qr_version)], salt="veyra-table-qr"
        )
        self.menu = f"/order/table/{self.token}/"
        self.cart = f"{self.menu}cart/"

    def test_guest_cart_persists_between_pages_and_is_private_to_browser(self):
        self.assertEqual(self.client.get(self.menu).status_code, 200)
        added = self.client.post(self.cart, {
            "action": "add", "product": self.product.pk, "quantity": "2", "revision": "0"
        })
        self.assertEqual(added.status_code, 302)
        order = self.client.get(self.cart)
        self.assertContains(order, "Your order")
        self.assertEqual(order.context["cart_count"], 2)
        self.assertEqual(order.context["total"], Decimal("50000"))
        self.assertEqual(Client().get(self.cart).context["cart_count"], 0)
        self.assertEqual(self.client.get(self.menu).context["cart_count"], 2)

    def test_qr_still_resolves_with_multiple_locations(self):
        create_location(business=self.business, actor=self.owner, name="Second branch")
        self.assertEqual(self.client.get(self.menu).status_code, 200)
        self.assertEqual(self.client.get(f"{self.menu}qr.svg").status_code, 200)

    def test_checkout_replays_to_same_private_order(self):
        self.client.get(self.menu)
        self.client.post(self.cart, {
            "action": "add", "product": self.product.pk, "quantity": "2", "revision": "0",
        })
        cart = self.client.get(self.cart)
        self.assertContains(cart, "Pay at the counter")
        payload = {
            "action": "checkout", "revision": "1",
            "quote": cart.context["cart_quote"],
            "payment_method": "counter", "note": "No sugar",
        }
        checkout = self.client.post(self.cart, payload)
        self.assertEqual(checkout.status_code, 302)
        replay = self.client.post(self.cart, payload)
        self.assertRedirects(replay, checkout.url, fetch_redirect_response=False)
        tracking = self.client.get(checkout.url)
        self.assertContains(tracking, "Order summary")
        self.assertContains(tracking, "Rp 50000")
        self.assertEqual(Client().get(checkout.url).status_code, 404)
        self.assertContains(self.client.get(self.cart), "Your order is empty")

    def test_cart_stepper_updates_total_and_rejects_stale_submissions(self):
        self.client.get(self.menu)
        self.client.post(self.cart, {
            "action": "add", "product": self.product.pk, "quantity": "1", "revision": "0",
        })
        page = self.client.get(self.cart)
        self.assertContains(page, 'aria-label="Decrease quantity of Kopi" disabled')
        self.assertContains(page, 'aria-label="Increase quantity of Kopi"')
        payload = {"action": "increment", "product": self.product.pk, "revision": "1"}
        self.assertEqual(self.client.post(self.cart, payload).status_code, 302)
        self.assertEqual(self.client.post(self.cart, payload).status_code, 400)
        self.assertEqual(self.client.get(self.cart).context["total"], Decimal("50000"))
        self.assertEqual(self.client.post(self.cart, {**payload, "action": "decrement", "revision": "2"}).status_code, 302)
        self.assertEqual(self.client.post(self.cart, {**payload, "action": "decrement", "revision": "3"}).status_code, 400)
        self.assertEqual(self.client.get(self.cart).context["total"], Decimal("25000"))

    def test_takeaway_link_flows_from_checkout_to_pos_kitchen_and_receipt(self):
        cashier = Client()
        cashier.force_login(self.owner)
        locations = "/app/cart-cafe/locations/"
        enabled = cashier.post(locations, {"action": "enable_pickup", "location": self.table.location_id})
        self.assertRedirects(enabled, locations, fetch_redirect_response=False)
        management = cashier.get(locations)
        menu_path = re.search(r'href="(/order/pickup/[^"]+/)"', management.content.decode())[1]

        self.assertContains(self.client.get(menu_path), "Takeaway")
        cart_path = f"{menu_path}cart/"
        self.assertEqual(self.client.post(cart_path, {
            "action": "add", "revision": "0", "product": self.product.pk,
        }).status_code, 302)
        cart = self.client.get(cart_path)
        payload = {
            "action": "checkout", "revision": "1", "quote": cart.context["cart_quote"],
            "order_type": "dine_in", "table": self.table.pk,
        }
        self.assertContains(self.client.post(cart_path, payload), "Choose an available order type", status_code=400)
        checkout = self.client.post(cart_path, {**payload, "order_type": "takeaway"})
        self.assertEqual(checkout.status_code, 302)
        tracking = self.client.get(checkout.url)
        self.assertContains(tracking, "Takeaway")
        self.assertNotContains(tracking, self.table.name)
        order = tracking.context["order"]
        self.assertEqual(order.location_id, self.table.location_id)
        self.assertIsNone(order.table_id)
        self.assertEqual(Client().get(checkout.url).status_code, 404)
        self.assertRedirects(self.client.post(cart_path, {**payload, "order_type": "takeaway"}), checkout.url, fetch_redirect_response=False)

        pos = cashier.get("/app/cart-cafe/pos/")
        self.assertContains(pos, f'data-order-id="{order.pk}"')
        self.assertContains(pos, "Takeaway")
        station = self.business.stations.create(name="Kitchen", kind="kitchen", location=self.table.location)
        stock = create_inventory_item(
            business=self.business, actor=self.owner, name="Milk", unit="ml",
            location_id=self.table.location_id, opening_stock="1000",
        )
        RecipeLine.objects.create(product=self.product, item=stock, quantity="200")
        queue = f"/app/cart-cafe/stations/{station.pk}/screen/"
        self.assertContains(cashier.get(queue), "Kopi")
        payment = f"/app/cart-cafe/orders/{order.pk}/payment/"
        self.assertEqual(cashier.post(payment, {
            "method": "cash", "cash_received": "30000", "idempotency_key": order.idempotency_key,
        }).status_code, 302)
        for status in ("preparing", "ready", "completed"):
            cashier.post(queue, {"action": "advance", "status": status, "order_id": order.pk})
            self.assertContains(self.client.get(checkout.url), f'data-order-status="{status.title()}"')
        inventory = cashier.get("/app/cart-cafe/inventory/")
        self.assertContains(inventory, "800.000 ml")
        self.assertContains(inventory, "Recipe consumption")
        receipt = cashier.get(f"/app/cart-cafe/orders/{order.pk}/receipt/")
        self.assertContains(receipt, "Takeaway")

        cashier.post(locations, {"action": "disable_pickup", "location": self.table.location_id})
        self.assertEqual(self.client.get(menu_path).status_code, 404)
        self.assertContains(self.client.get(checkout.url), 'data-order-status="Completed"')

    def test_table_guest_can_choose_takeaway_without_reusing_table_or_location_from_post(self):
        cashier = Client()
        cashier.force_login(self.owner)
        alternate = create_location(business=self.business, actor=self.owner, name="Other branch")
        cashier.post("/app/cart-cafe/locations/", {"action": "enable_pickup", "location": self.table.location_id})
        self.client.get(self.menu)
        self.client.post(self.cart, {"action": "add", "revision": "0", "product": self.product.pk})
        page = self.client.get(self.cart)
        self.assertContains(page, "How would you like your order?")
        response = self.client.post(self.cart, {
            "action": "checkout", "revision": "1", "quote": page.context["cart_quote"],
            "order_type": "takeaway", "table": self.table.pk, "location": alternate.pk,
        })
        self.assertEqual(response.status_code, 302)
        order = self.client.get(response.url).context["order"]
        self.assertEqual(order.order_type, "takeaway")
        self.assertEqual(order.location_id, self.table.location_id)
        self.assertIsNone(order.table_id)
        self.assertEqual(order.table_label, "")

    def test_pickup_management_is_scoped_to_location_and_permission(self):
        other_owner = User.objects.create_user("different-owner@example.invalid")
        other = create_business(owner=other_owner, name="Other business", slug="different-business")
        self.client.force_login(self.owner)
        path = "/app/cart-cafe/locations/"
        self.assertEqual(self.client.post(path, {
            "action": "enable_pickup", "location": other.locations.get().pk,
        }).status_code, 404)
        viewer = User.objects.create_user("location-viewer@example.invalid")
        Membership.objects.create(business=self.business, user=viewer, permissions=["locations.view"])
        self.client.force_login(viewer)
        self.assertEqual(self.client.post(path, {
            "action": "enable_pickup", "location": self.table.location_id,
        }).status_code, 403)

    def test_categorized_product_uses_category_price_in_storefront_and_pos(self):
        coffee = Category.objects.create(business=self.business, name="Coffee")
        self.product.category = coffee
        self.product.save(update_fields=["category"])
        promotion = Promotion.objects.create(
            business=self.business, name="Coffee offer", discount_type="percent", value="20",
        )
        promotion.categories.add(coffee)
        menu = self.client.get(self.menu)
        self.assertContains(menu, "Coffee offer")
        self.assertContains(menu, "Rp 20000")
        self.assertContains(menu, 'data-menu-category="%s"' % coffee.pk)
        self.client.post(self.cart, {"action": "add", "revision": "0", "product": self.product.pk})
        self.assertEqual(self.client.get(self.cart).context["total"], Decimal("20000"))
        self.client.force_login(self.owner)
        pos = self.client.get("/app/cart-cafe/pos/")
        self.assertContains(pos, "Rp 20000")
        self.assertContains(pos, 'name="quantity_%s"' % self.product.pk)

    @override_settings(PAYMENT_CREDENTIALS_ENCRYPTION_KEY=Fernet.generate_key().decode())
    @patch("urllib.request.urlopen")
    def test_pickup_qris_waits_for_provider_confirmed_settlement(self, provider_request):
        server_key = "SB-Mid-server-TESTPICKUP123456"
        signing_key = "pickup-notification-secret"
        connection = save_payment_connection(
            business=self.business, actor=self.owner, provider="midtrans",
            environment="sandbox", merchant_id="PICKUP-TEST", credentials={"server_key": server_key, "signature_secret_key": signing_key},
        )
        connection.verified_at = timezone.now()
        connection.verification_state = "verified"
        connection.approval_reference = "test-approval"
        connection.save(update_fields=["verified_at", "verification_state", "approval_reference"])
        response = MagicMock()
        response.read.return_value = json.dumps({
            "status_code": "201", "transaction_id": "pickup-midtrans-test", "qr_string": "000201pickup",
        }).encode()
        response.__enter__.return_value = response
        provider_request.return_value = response

        cashier = Client()
        cashier.force_login(self.owner)
        locations = "/app/cart-cafe/locations/"
        cashier.post(locations, {"action": "enable_pickup", "location": self.table.location_id})
        menu = re.search(r'href="(/order/pickup/[^"]+/)"', cashier.get(locations).content.decode())[1]
        self.client.get(menu)
        cart = menu + "cart/"
        self.client.post(cart, {"action": "add", "revision": "0", "product": self.product.pk})
        page = self.client.get(cart)
        self.assertContains(page, "QRIS")
        checkout = self.client.post(cart, {
            "action": "checkout", "revision": "1", "quote": page.context["cart_quote"],
            "payment_method": "qris:midtrans",
        })
        self.assertEqual(checkout.status_code, 302)
        tracking = self.client.get(checkout.url)
        self.assertContains(tracking, 'data-payment-status="Pending"')
        order = tracking.context["order"]
        qr = f"/order/{order.pk}/payment.svg"
        self.assertEqual(self.client.get(qr)["Content-Type"], "image/svg+xml")
        self.assertEqual(Client().get(qr).status_code, 404)

        cashier.post(f"/app/cart-cafe/orders/{order.pk}/status/", {"status": "preparing"})
        self.assertContains(self.client.get(checkout.url), 'data-order-status="New"')
        merchant_reference = tracking.context["payment"].merchant_reference
        amount = "25000"
        signature = hashlib.sha512(f"{merchant_reference}200{amount}{signing_key}".encode()).hexdigest()
        notification = {
            "order_id": merchant_reference, "status_code": "200", "gross_amount": amount,
            "transaction_status": "settlement", "fraud_status": "accept",
            "transaction_id": "pickup-midtrans-test", "signature_key": signature,
        }
        response.read.return_value = json.dumps({**notification, "payment_type": "qris"}).encode()
        webhook = "/api/payments/webhooks/midtrans/"
        for _ in range(2):
            self.assertEqual(self.client.post(webhook, notification, content_type="application/json").status_code, 200)
        paid = self.client.get(checkout.url)
        self.assertContains(paid, 'data-payment-status="Paid"')
        self.assertContains(paid, 'data-order-status="Preparing"')
        self.assertEqual([call.args[0].get_method() for call in provider_request.call_args_list], ["POST", "GET"])
