import asyncio
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from .models import InventoryBalance, InventoryItem, Location, Membership, OutboxEvent, User
from .services import create_business, dispatch_outbox_event


class PurchaseFixture:
    def setUp(self):
        self.owner = User.objects.create_user("purchase-owner@example.invalid")
        self.business = create_business(owner=self.owner, name="Purchasing Shop", slug="purchasing-shop")
        self.client.force_login(self.owner)
        self.root = "/app/purchasing-shop/inventory/"
        self.supplier_data = {"name": "Coffee Growers", "contact_person": "Ari", "email": "supplier@example.invalid",
                              "phone": "021-123456", "address": "Main street", "notes": "Weekday delivery", "active": "on"}

    def make_purchase(self, **changes):
        supplier = self.client.post(self.root + "suppliers/new/", self.supplier_data)
        self.assertEqual(supplier.status_code, 302)
        self.supplier = self.business.suppliers.get()
        self.location = self.business.locations.get()
        self.beans = InventoryItem.objects.create(business=self.business, name="Coffee beans", unit="kg")
        self.milk = InventoryItem.objects.create(business=self.business, name="Whole milk", unit="L")
        self.draft = {
            "supplier": self.supplier.pk, "location": self.location.pk, "revision": "0",
            "lines-TOTAL_FORMS": "2", "lines-INITIAL_FORMS": "0",
            "lines-0-item": self.beans.pk, "lines-0-quantity": "20.000", "lines-0-unit_cost": "17.25",
            "lines-1-item": self.milk.pk, "lines-1-quantity": "30.000", "lines-1-unit_cost": "3.50",
            "total": "0.01", **changes,
        }
        created = self.client.post(self.root + "purchases/new/", self.draft)
        self.assertEqual(created.status_code, 302)
        self.detail = created.url
        self.receive = self.detail + "receive/"
        self.purchase = self.client.get(self.detail).context["purchase"]
        self.first, self.second = list(self.purchase.lines.all())

    def change_state(self, action):
        current = self.client.get(self.detail).context["purchase"]
        return self.client.post(self.detail, {"revision": current.revision, "action": action})

    def delivery(self, *, first="0", second="0", damaged="0", **changes):
        return {"idempotency_key": str(uuid4()), "delivery_reference": "DEL-42",
                f"accepted_{self.first.pk}": first, f"accepted_{self.second.pk}": second,
                f"damaged_{self.first.pk}": damaged, **changes}

    def assert_quantity(self, item, expected):
        row = InventoryBalance.objects.get(item=item, location=self.location)
        self.assertEqual(row.quantity, Decimal(expected))
        return row


class PurchasingTests(PurchaseFixture, TestCase):
    def test_create_edit_search_and_deactivate_supplier(self):
        created = self.client.post(self.root + "suppliers/new/", self.supplier_data)
        self.assertEqual(created.status_code, 302)
        self.assertContains(self.client.get(created.url), "Coffee Growers")
        self.assertEqual(self.client.post(created.url, {**self.supplier_data, "contact_person": "Dewi", "active": ""}).status_code, 302)
        self.assertContains(self.client.get(created.url), "Dewi")
        self.assertContains(self.client.get(self.root + "suppliers/", {"q": "Dewi", "status": "inactive"}), "Coffee Growers")
        self.assertNotContains(self.client.get(self.root + "suppliers/", {"status": "active"}), "Coffee Growers")
        other = User.objects.create_user("stock-reader@example.invalid")
        self.client.force_login(other)
        self.assertEqual(self.client.get(created.url).status_code, 404)
        Membership.objects.create(business=self.business, user=other, permissions=["inventory.view"])
        self.assertEqual(self.client.get(created.url).status_code, 200)
        self.assertEqual(self.client.post(created.url, self.supplier_data).status_code, 403)

    def test_edit_draft_and_submit_freezes_cost_and_parties(self):
        self.make_purchase()
        self.assertEqual(self.purchase.total, Decimal("450.00"))
        self.assertEqual(self.client.post(self.receive, self.delivery(first="2")).status_code, 400)
        edited = self.client.post(self.detail + "edit/", {
            **self.draft, "revision": self.purchase.revision, "lines-INITIAL_FORMS": "2", "lines-0-quantity": "21.125",
        })
        self.assertEqual(edited.status_code, 302)
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.total, Decimal("469.41"))
        self.assertEqual(self.change_state("submit").status_code, 302)
        self.assertEqual(self.change_state("submit").status_code, 400)
        self.assertEqual(self.client.post(self.detail + "edit/", self.draft).status_code, 409)
        self.beans.name = "Renamed beans"
        self.beans.save(update_fields=["name"])
        supplier_url = reverse("supplier_detail", args=[self.business.slug, self.supplier.pk])
        self.assertEqual(self.client.post(supplier_url, {"name": "New supplier name"}).status_code, 302)
        detail = self.client.get(self.detail)
        self.assertContains(detail, "Coffee beans")
        self.assertContains(detail, "Coffee Growers")
        self.assertContains(detail, "17.25")
        self.assertContains(self.client.get(supplier_url), self.purchase.reference)
        self.assertContains(self.client.get(self.root + "purchases/", {
            "q": self.purchase.reference, "status": "ordered", "supplier": self.supplier.pk, "location": self.location.pk,
        }), self.purchase.reference)

    def test_receives_multiple_deliveries_and_replays_exactly_once(self):
        self.make_purchase()
        secondary = Location.objects.create(business=self.business, name="Uptown")
        InventoryBalance.objects.create(item=self.beans, location=secondary, quantity=7)
        self.assertEqual(self.change_state("submit").status_code, 302)
        first_delivery = self.delivery(first="8", second="0", damaged="2")
        self.assertEqual(self.client.post(self.receive, first_delivery).status_code, 302)
        self.assertEqual(self.client.post(self.receive, first_delivery).status_code, 302)
        self.assertEqual(self.client.post(self.receive, {**first_delivery, f"accepted_{self.first.pk}": "3"}).status_code, 400)
        self.assertContains(self.client.get(self.detail), "Partially received")
        row = self.assert_quantity(self.beans, "8")
        self.assertEqual(row.last_purchase_unit_cost, Decimal("17.25"))
        self.assertEqual(InventoryBalance.objects.get(item=self.beans, location=secondary).quantity, 7)
        self.assertEqual(self.client.post(self.receive, self.delivery(first="13")).status_code, 400)
        self.assertEqual(self.client.post(self.receive, self.delivery(first="0")).status_code, 400)
        self.assertEqual(self.client.post(self.receive, self.delivery(first="-1")).status_code, 400)
        self.assertEqual(self.client.post(self.receive, self.delivery(first="12", second="30")).status_code, 302)
        self.assertContains(self.client.get(self.detail), 'data-purchase-status>Received')
        self.assert_quantity(self.beans, "20")
        self.assert_quantity(self.milk, "30")
        self.assertEqual(self.purchase.receipts.count(), 2)
        self.assertEqual(self.business.stock_movements.count(), 3)
        movement = self.business.stock_movements.get(receiving_line__damaged_quantity=2)
        self.assertEqual(movement.location_id, self.location.pk)
        self.assertEqual(movement.receiving_line.receipt.purchase_id, self.purchase.pk)
        self.assertEqual(movement.receiving_line.unit_cost, Decimal("17.25"))
        self.assertEqual(self.change_state("cancel").status_code, 400)
        self.assertContains(self.client.get(self.root), "Last purchase cost")

    def test_cancel_after_partial_delivery_keeps_history_and_stock(self):
        self.make_purchase()
        self.assertEqual(self.change_state("submit").status_code, 302)
        self.assertEqual(self.client.post(self.receive, self.delivery(first="5")).status_code, 302)
        self.assertEqual(self.change_state("cancel").status_code, 302)
        self.assertEqual(self.client.post(self.receive, self.delivery(first="1")).status_code, 400)
        self.assertContains(self.client.get(self.detail), "Cancelled")
        self.assert_quantity(self.beans, "5")
        self.assertEqual(self.purchase.receipts.count(), 1)

    def test_unauthorized_location_and_cross_tenant_objects(self):
        self.make_purchase()
        other = User.objects.create_user("purchase-outsider@example.invalid")
        other_business = create_business(owner=other, name="Foreign", slug="foreign-purchasing")
        foreign_item = InventoryItem.objects.create(business=other_business, name="Foreign stock", unit="kg")
        self.assertEqual(self.client.post(self.root + "purchases/new/", {**self.draft, "lines-0-item": foreign_item.pk}).status_code, 400)
        self.client.force_login(other)
        self.assertEqual(self.client.get(self.detail).status_code, 404)
        foreign_path = self.detail.replace(self.business.slug, other_business.slug)
        self.assertEqual(self.client.get(foreign_path).status_code, 404)
        member = Membership.objects.create(business=self.business, user=other, permissions=["inventory.view", "inventory.manage"])
        scope = reverse("inventory_access", args=[self.business.slug, member.pk])
        self.assertEqual(self.client.post(scope, {"restricted": "on", "locations": self.location.pk}).status_code, 403)
        self.client.force_login(self.owner)
        foreign_location = other_business.locations.get()
        self.assertEqual(self.client.post(scope, {"restricted": "on", "locations": foreign_location.pk}).status_code, 400)
        nearby = Location.objects.create(business=self.business, name="Nearby")
        self.assertEqual(self.client.post(scope, {"restricted": "on", "locations": nearby.pk}).status_code, 302)
        self.client.force_login(other)
        self.assertEqual(self.client.get(self.detail).status_code, 404)
        self.assertEqual(self.client.post(self.receive, self.delivery(first="2")).status_code, 404)
        self.assertEqual(self.client.post(self.root + "purchases/new/", self.draft).status_code, 400)
        self.assertNotContains(self.client.get(self.root + "purchases/"), self.purchase.reference)
        self.assertNotContains(self.client.get(self.root + "suppliers/" + str(self.supplier.pk) + "/"), self.purchase.reference)

    def test_failed_stock_update_rolls_back_entire_delivery_and_outbox(self):
        self.make_purchase()
        self.assertEqual(self.change_state("submit").status_code, 302)
        InventoryBalance.objects.create(item=self.milk, location=self.location, quantity="999999999.999")
        before = OutboxEvent.objects.filter(business=self.business).count()
        response = self.client.post(self.receive, self.delivery(first="2", second="1"))
        self.assertContains(response, "supported stock balance", status_code=400)
        self.assertFalse(self.purchase.receipts.exists())
        self.assertFalse(self.business.stock_movements.exists())
        self.assertFalse(InventoryBalance.objects.filter(item=self.beans, location=self.location).exists())
        self.assertEqual(OutboxEvent.objects.filter(business=self.business).count(), before)
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, "ordered")


class PurchasingConcurrencyTests(PurchaseFixture, TransactionTestCase):
    def test_concurrent_deliveries_cannot_over_receive(self):
        self.make_purchase()
        self.assertEqual(self.change_state("submit").status_code, 302)
        barrier = Barrier(2)
        clients = [Client(), Client()]
        for browser in clients:
            browser.force_login(self.owner)

        def receive(browser, payload):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return browser.post(self.receive, payload).status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(receive, browser, self.delivery(first="15")) for browser in clients]
            self.assertEqual(sorted(f.result(timeout=20) for f in futures), [302, 400])
        self.assert_quantity(self.beans, "15")
        self.assertEqual(self.purchase.receipts.count(), 1)

    def test_outbox_updates_authorized_purchase_and_stock_sockets(self):
        from config.asgi import application

        self.make_purchase()
        self.assertEqual(self.change_state("submit").status_code, 302)
        other = User.objects.create_user("purchase-limited@example.invalid")
        member = Membership.objects.create(business=self.business, user=other, permissions=["inventory.view"],
                                           inventory_restricted=True)
        other_location = Location.objects.create(business=self.business, name="Other destination")
        member.inventory_locations.add(other_location)
        blocked = Client()
        blocked.force_login(other)

        def socket(browser, stream):
            conn = WebsocketCommunicator(application, f"/ws/business/{self.business.slug}/{stream}/")
            cookie = browser.cookies.get(settings.SESSION_COOKIE_NAME)
            conn.scope["headers"] = [(b"host", b"localhost"), (b"origin", b"http://localhost")]
            if cookie is not None:
                conn.scope["headers"].append((b"cookie", f"{settings.SESSION_COOKIE_NAME}={cookie.value}".encode()))
            return conn

        async def exercise():
            owner = socket(self.client, "purchasing")
            stock = socket(self.client, "inventory")
            restricted = socket(blocked, "purchasing")
            restricted_stock = socket(blocked, "inventory")
            anonymous = socket(Client(), "purchasing")
            sockets = [owner, stock, restricted, restricted_stock]
            for stream in sockets:
                self.assertTrue((await stream.connect())[0])
            self.assertFalse((await anonymous.connect())[0])
            payload = self.delivery(first="4")
            response = await sync_to_async(self.client.post)(self.receive, payload)
            self.assertEqual(response.status_code, 302)
            for event_type in ("purchase.update", "inventory.update"):
                event = await OutboxEvent.objects.filter(business=self.business, event_type=event_type).alatest("created_at")
                self.assertTrue(await sync_to_async(dispatch_outbox_event)(event.pk))
            self.assertEqual((await owner.receive_json_from(timeout=3))["purchase_id"], str(self.purchase.pk))
            balance = await stock.receive_json_from(timeout=3)
            self.assertEqual(balance["location_id"], str(self.location.pk))
            self.assertEqual(balance["quantity"], "4.000")
            self.assertEqual(balance["last_purchase_unit_cost"], "17.25")
            self.assertTrue(await restricted.receive_nothing())
            self.assertTrue(await restricted_stock.receive_nothing())
            for stream in sockets:
                await stream.disconnect()
            await anonymous.disconnect()

        asyncio.run(exercise())
