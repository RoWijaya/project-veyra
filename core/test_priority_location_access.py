import asyncio
from unittest.mock import patch
from uuid import uuid4

from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from config.asgi import application
from .models import DiningTable, Membership, Order, OutboxEvent, Product, Station, User, WorkspaceAssignment
from .permissions import order_tracking_cookie_name, order_tracking_cookie_signer
from .services import create_business, create_order, dispatch_outbox_event


class LocationFixture:
    def setUp(self):
        super().setUp()
        self.owner = User.objects.create_user("location-owner@example.invalid")
        self.staff = User.objects.create_user("location-staff@example.invalid")
        self.business = create_business(owner=self.owner, name="Branch Cafe", slug="branch-cafe")
        self.location = self.business.locations.get()
        self.sibling = self.business.locations.create(name="Other branch")
        self.product = Product.objects.create(business=self.business, name="Branch tea", price=100)
        self.member = Membership.objects.create(
            user=self.staff, business=self.business, inventory_restricted=True,
            permissions=["catalog.view", "orders.view", "orders.create", "orders.manage", "reports.view"],
        )
        self.member.inventory_locations.add(self.location)
        self.client.force_login(self.staff)

    def order(self, location, **kwargs):
        return create_order(
            business=self.business, actor=self.owner, items=[(self.product.pk, 1)],
            location_id=location.pk, **kwargs,
        )

    def route(self, name, *args):
        return reverse(name, args=[self.business.slug, *args])


class LocationHTTPTests(LocationFixture, TestCase):
    def test_history_and_tender_stay_in_granted_location(self):
        visible = self.order(self.location)
        hidden = self.order(self.sibling)
        history = self.client.get(self.route("order_history"))
        self.assertEqual(list(history.context["orders"]), [visible])
        tender = {"method": "cash", "cash_received": "100", "idempotency_key": uuid4()}
        self.assertEqual(self.client.post(self.route("record_payment", hidden.pk), tender).status_code, 404)
        self.assertEqual(Order.objects.get(pk=hidden.pk).payment_status, Order.PaymentStatus.UNPAID)
        self.assertEqual(self.client.post(self.route("record_payment", visible.pk), tender).status_code, 302)

    def test_pos_and_reports_reject_branch_outside_the_grant(self):
        workspace = self.business.workspaces.create(name="Other workspace", location=self.sibling)
        WorkspaceAssignment.objects.create(membership=self.member, workspace=workspace, permissions=["orders.create"])
        pos = self.route("point_of_sale")
        create = {"order_type": "takeaway", "idempotency_key": uuid4(), f"qty_{self.product.pk}": 1}
        self.assertIn(self.client.post(pos, {**create, "location": self.sibling.pk, "workspace": workspace.pk}).status_code, (400, 404))
        self.assertEqual(self.client.get(self.route("reports"), {"location": self.sibling.pk}).status_code, 404)
        self.assertIn(self.client.get(self.route("reports"), {"location": "bad-id"}).status_code, (400, 404))
        self.assertFalse(self.business.orders.exists())

    def test_station_screen_checks_station_and_order_locations(self):
        allowed = Station.objects.create(business=self.business, location=self.location, name="Local kitchen")
        other = Station.objects.create(business=self.business, location=self.sibling, name="Other kitchen")
        hidden = self.order(self.sibling)
        self.assertEqual(self.client.get(self.route("station_screen", other.pk)).status_code, 404)
        self.client.post(self.route("station_screen", allowed.pk), {"action": "advance", "order_id": hidden.pk, "status": "preparing"})
        self.assertEqual(Order.objects.get(pk=hidden.pk).status, Order.Status.NEW)

    def test_permissions_and_tenant_membership_remain_required(self):
        visible = self.order(self.location)
        outsider = User.objects.create_user("branch-outsider@example.invalid")
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(self.route("order_history")).status_code, 404)
        self.client.force_login(self.staff)
        self.member.permissions = ["orders.view"]
        self.member.save(update_fields=["permissions"])
        self.assertEqual(self.client.post(self.route("update_order_status", visible.pk), {"status": "preparing"}).status_code, 403)


class LocationStreamTests(LocationFixture, TransactionTestCase):
    def test_idle_guest_tracking_closes_when_cookie_expires(self):
        table = DiningTable.objects.create(business=self.business, location=self.location, name="QR table")
        token = uuid4().hex
        order = create_order(
            business=self.business, actor=None, table=table, items=[(self.product.pk, 1)],
            order_type=Order.OrderType.DINE_IN, guest_tracking_token=token,
        )
        signed = order_tracking_cookie_signer(order.pk).sign(token)
        cookie = f"{order_tracking_cookie_name(order.pk)}={signed}".encode()
        socket = WebsocketCommunicator(
            application, f"/ws/guest/orders/{order.pk}/",
            headers=[(b"origin", b"http://localhost"), (b"cookie", cookie)],
        )

        async def verify():
            self.assertTrue((await socket.connect())[0])
            try:
                self.assertEqual((await socket.receive_json_from())["order_id"], str(order.pk))
                with patch("core.permissions.ORDER_TRACKING_MAX_AGE", -1):
                    self.assertEqual((await socket.receive_output(timeout=3))["code"], 4403)
            finally:
                await socket.disconnect()

        with patch("core.consumers.ACCESS_RECHECK_SECONDS", 0.1):
            asyncio.run(verify())

    def test_revocation_closes_an_idle_socket_without_an_order_event(self):
        cookie = f"{settings.SESSION_COOKIE_NAME}={self.client.cookies[settings.SESSION_COOKIE_NAME].value}"
        socket = WebsocketCommunicator(
            application, f"/ws/business/{self.business.slug}/orders/",
            headers=[(b"origin", b"http://localhost"), (b"cookie", cookie.encode())],
        )

        async def verify():
            self.assertTrue((await socket.connect())[0])
            try:
                owner = await sync_to_async(self.client_class)()
                await sync_to_async(owner.force_login)(self.owner)
                response = await sync_to_async(owner.post)(
                    self.route("team"), {"action": "deactivate_membership", "member_id": self.member.pk},
                )
                self.assertEqual(response.status_code, 302)
                self.assertEqual((await socket.receive_output(timeout=3))["code"], 4403)
            finally:
                await socket.disconnect()

        with patch("core.consumers.ACCESS_RECHECK_SECONDS", 0.1):
            asyncio.run(verify())

    def test_only_allowed_order_events_and_revoked_socket_closes(self):
        visible = self.order(self.location)
        hidden = self.order(self.sibling)
        visible_event = OutboxEvent.objects.get(business=self.business, payload__order_id=str(visible.pk))
        hidden_event = OutboxEvent.objects.get(business=self.business, payload__order_id=str(hidden.pk))
        cookie = f"{settings.SESSION_COOKIE_NAME}={self.client.cookies[settings.SESSION_COOKIE_NAME].value}"
        socket = WebsocketCommunicator(
            application, f"/ws/business/{self.business.slug}/orders/",
            headers=[(b"origin", b"http://localhost"), (b"cookie", cookie.encode())],
        )

        async def verify():
            self.assertTrue((await socket.connect())[0])
            try:
                await sync_to_async(dispatch_outbox_event)(hidden_event.pk)
                await sync_to_async(dispatch_outbox_event)(visible_event.pk)
                self.assertEqual((await socket.receive_json_from(timeout=3))["order_id"], str(visible.pk))
                await Membership.objects.filter(pk=self.member.pk).aupdate(permissions=[])
                await socket.send_json_to({"type": "group.add", "location": self.sibling.pk})
                self.assertEqual((await socket.receive_output(timeout=3))["code"], 4403)
            finally:
                await socket.disconnect()

        asyncio.run(verify())
