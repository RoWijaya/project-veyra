import asyncio
import io
import json
import uuid
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from cryptography.fernet import Fernet
from django.core import mail
from django.core.cache import cache
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase
from django.test.utils import override_settings
from django.utils import timezone

from .models import (
    AuditEvent,
    Business,
    BusinessSubscription,
    Customer,
    DiningTable,
    InventoryBalance,
    InventoryItem,
    Invitation,
    Membership,
    Order,
    OrderTender,
    OutboxEvent,
    PairedDevice,
    PaymentConnection,
    PaymentTransaction,
    PaymentWebhookEvent,
    Printer,
    PrintJob,
    Product,
    Promotion,
    RecipeLine,
    Station,
    StockMovement,
    SubscriptionEvent,
    User,
)
from .permissions import (
    order_stream_business_id,
    order_tracking_cookie_name,
    order_tracking_cookie_signer,
)
from .pricing import price_product
from .services import (
    accept_invitation,
    acknowledge_print_job,
    adjust_stock,
    create_business,
    create_inventory_item,
    create_invitation,
    create_location,
    create_order,
    create_print_job,
    create_printer,
    create_station,
    delete_payment_connection,
    dispatch_outbox_event,
    format_prep_ticket,
    format_receipt,
    initiate_qris_payment,
    issue_station_pairing_code,
    pair_station_device,
    process_payment_webhook,
    read_payment_credentials,
    reprint_order_ticket,
    revoke_invitation,
    revoke_paired_device,
    rotate_station_device_token,
    route_order_to_stations,
    save_payment_connection,
    transition_order,
    verify_payment_connection,
)


class TenantAndOrderTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner@example.com", "Very-Long-Test-Password-738!")
        self.business = create_business(owner=self.owner, name="North Star", slug="north-star")
        self.product = Product.objects.create(
            business=self.business, name="House coffee", price=Decimal("4.50")
        )

    def test_business_creation_grants_owner_membership(self):
        membership = Membership.objects.get(business=self.business, user=self.owner)
        self.assertEqual(self.business.owner, self.owner)
        self.assertFalse(membership.permissions)
        self.assertTrue(membership.allows("catalog.manage"))

    def test_business_creation_starts_audited_trial(self):
        subscription = BusinessSubscription.objects.select_related("plan").get(
            business=self.business
        )
        self.assertEqual(subscription.status, BusinessSubscription.Status.TRIAL)
        self.assertEqual(subscription.plan.code, "starter-trial")
        self.assertEqual(subscription.trial_ends_at - subscription.trial_started_at, timedelta(days=14))
        self.assertTrue(SubscriptionEvent.objects.filter(
            subscription=subscription, event_type="trial.started", actor=self.owner
        ).exists())
        event = SubscriptionEvent.objects.get(subscription=subscription)
        with self.assertRaises(ValueError):
            SubscriptionEvent.objects.filter(pk=event.pk).update(event_type="tampered")
        with self.assertRaises(ValueError):
            SubscriptionEvent.objects.filter(pk=event.pk).delete()

    def test_expired_trial_blocks_pos_http_order_creation(self):
        subscription = self.business.subscription
        subscription.status = BusinessSubscription.Status.EXPIRED
        subscription.save(update_fields=["status", "updated_at"])
        self.client.force_login(self.owner)
        response = self.client.post(
            f"/app/{self.business.slug}/pos/",
            {
                "idempotency_key": str(uuid.uuid4()),
                "order_type": "takeaway",
                f"quantity_{self.product.pk}": "1",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.business.orders.exists())

        unprovisioned = Business.objects.create(
            owner=self.owner, name="Unprovisioned", slug="unprovisioned"
        )
        unprovisioned_product = Product.objects.create(
            business=unprovisioned, name="Tea", price=Decimal("2.00")
        )
        response = self.client.post(
            f"/app/{unprovisioned.slug}/pos/",
            {
                "idempotency_key": str(uuid.uuid4()),
                "order_type": "takeaway",
                f"quantity_{unprovisioned_product.pk}": "1",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(unprovisioned.orders.exists())

    def test_platform_admin_exposes_subscription_plans_and_tenant_subscriptions(self):
        platform_admin = User.objects.create_superuser(
            "platform@example.com", "Very-Long-Test-Password-740!"
        )
        self.client.force_login(platform_admin)
        plans = self.client.get("/admin/core/subscriptionplan/")
        subscriptions = self.client.get("/admin/core/businesssubscription/")
        history = self.client.get("/admin/core/subscriptionevent/")
        self.assertEqual(plans.status_code, 200)
        self.assertEqual(subscriptions.status_code, 200)
        self.assertEqual(history.status_code, 200)
        self.assertContains(subscriptions, self.business.name)

    def test_order_price_is_snapshotted_and_idempotent(self):
        key = uuid.uuid4()
        first = create_order(
            business=self.business,
            actor=self.owner,
            items=[(self.product.pk, 2)],
            idempotency_key=key,
        )
        self.product.price = Decimal("9.00")
        self.product.save(update_fields=["price"])
        retry = create_order(
            business=self.business,
            actor=self.owner,
            items=[(self.product.pk, 2)],
            idempotency_key=key,
        )
        first.refresh_from_db()
        self.assertEqual(retry.pk, first.pk)
        self.assertEqual(first.total, Decimal("9.00"))
        self.assertEqual(first.lines.get().unit_price, Decimal("4.50"))

    def test_order_rejects_product_from_another_tenant(self):
        other = Business.objects.create(name="Other shop", slug="other-shop")
        foreign_product = Product.objects.create(
            business=other, name="Private item", price=Decimal("1.00")
        )
        with self.assertRaises(Product.DoesNotExist):
            create_order(business=self.business, actor=self.owner, items=[(foreign_product.pk, 1)])
        self.assertEqual(self.business.orders.count(), 0)

    def test_order_customer_is_tenant_scoped_and_name_is_snapshotted(self):
        customer = Customer.objects.create(
            business=self.business, name="Maya", email="maya@example.com", phone="08123"
        )
        order = create_order(
            business=self.business,
            actor=self.owner,
            items=[(self.product.pk, 1)],
            customer_id=customer.pk,
        )
        customer.name = "Maya Updated"
        customer.save(update_fields=["name"])
        order.refresh_from_db()
        self.assertEqual(order.customer_name_snapshot, "Maya")
        self.assertEqual(order.customer_email_snapshot, "maya@example.com")
        other = Business.objects.create(name="Other shop", slug="other-shop")
        foreign_customer = Customer.objects.create(business=other, name="Private customer")
        with self.assertRaises(Customer.DoesNotExist):
            create_order(
                business=self.business,
                actor=self.owner,
                items=[(self.product.pk, 1)],
                customer_id=foreign_customer.pk,
            )

    def test_idempotency_key_cannot_be_reused_for_different_items(self):
        key = uuid.uuid4()
        create_order(
            business=self.business,
            actor=self.owner,
            items=[(self.product.pk, 1)],
            idempotency_key=key,
        )
        with self.assertRaises(ValueError):
            create_order(
                business=self.business,
                actor=self.owner,
                items=[(self.product.pk, 2)],
                idempotency_key=key,
            )
        self.assertEqual(self.business.orders.count(), 1)

    def test_order_status_transition_is_validated(self):
        order = create_order(business=self.business, actor=self.owner, items=[(self.product.pk, 1)])
        transition_order(
            business=self.business,
            order_id=order.pk,
            status=order.Status.PREPARING,
            actor=self.owner,
        )
        with self.assertRaises(ValueError):
            transition_order(
                business=self.business,
                order_id=order.pk,
                status=order.Status.COMPLETED,
                actor=self.owner,
            )

    def test_order_events_are_written_to_outbox_and_published_idempotently(self):
        order = create_order(business=self.business, actor=self.owner, items=[(self.product.pk, 1)])
        event = OutboxEvent.objects.get(business=self.business, payload__order_id=str(order.pk))
        self.assertIsNone(event.published_at)
        with patch("core.services.asyncio.run", side_effect=lambda coroutine: coroutine.close()):
            self.assertTrue(dispatch_outbox_event(event.pk))
        self.assertFalse(dispatch_outbox_event(event.pk))
        event.refresh_from_db()
        self.assertIsNotNone(event.published_at)
        self.assertEqual(event.attempts, 1)

    def test_audit_event_cannot_be_updated_or_deleted(self):
        event = AuditEvent.objects.create(business=self.business, actor=self.owner, action="test")
        event.action = "changed"
        with self.assertRaises(ValueError):
            event.save()
        with self.assertRaises(ValueError):
            event.delete()

    def test_other_tenant_business_route_returns_not_found(self):
        other_owner = User.objects.create_user("other@example.com", "Very-Long-Test-Password-739!")
        other = create_business(owner=other_owner, name="Other shop", slug="other-shop")
        self.client.force_login(self.owner)
        response = self.client.get(f"/app/{other.slug}/")
        self.assertEqual(response.status_code, 404)

    def test_order_stream_requires_active_tenant_membership_and_view_permission(self):
        self.assertEqual(
            order_stream_business_id(self.owner.pk, self.business.slug), self.business.pk.hex
        )
        staff = User.objects.create_user("stream@example.com", "Very-Long-Test-Password-741!")
        Membership.objects.create(business=self.business, user=staff)
        self.assertIsNone(order_stream_business_id(staff.pk, self.business.slug))
        other = Business.objects.create(name="Other shop", slug="other-shop")
        self.assertIsNone(order_stream_business_id(self.owner.pk, other.slug))

    def test_pos_creates_a_tenant_scoped_order(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            f"/app/{self.business.slug}/pos/",
            {
                "idempotency_key": str(uuid.uuid4()),
                "order_type": "takeaway",
                f"quantity_{self.product.pk}": "2",
            },
        )
        self.assertEqual(response.status_code, 302)
        order = self.business.orders.get()
        self.assertEqual(order.total, Decimal("9.00"))
        self.assertEqual(order.lines.count(), 1)

    def test_table_qr_order_uses_bound_table_and_server_price(self):
        self.client.force_login(self.owner)
        location = self.business.locations.get(name="Main location")
        response = self.client.post(
            f"/app/{self.business.slug}/tables/",
            {"name": "T4", "location": str(location.pk), "area": "Patio"},
        )
        self.assertEqual(response.status_code, 302)
        table = DiningTable.objects.get(business=self.business)
        token = self.client.get(f"/app/{self.business.slug}/tables/").context[
            "table_tokens"
        ][table.pk]
        qr = self.client.get(f"/order/table/{token}/qr.svg")
        self.assertEqual(qr.status_code, 200)
        self.assertEqual(qr["Content-Type"], "image/svg+xml")
        edit = self.client.post(
            f"/app/{self.business.slug}/tables/",
            {
                "action": "update",
                "table": str(table.pk),
                "name": "T4",
                "area": "Patio",
                "location": str(location.pk),
            },
        )
        self.assertEqual(edit.status_code, 302)
        table.refresh_from_db()
        self.assertEqual(table.area, "Patio")

        menu = self.client.get(f"/order/table/{token}/")
        self.assertEqual(menu.status_code, 200)
        self.assertContains(menu, "Your order")
        self.assertNotContains(menu, 'name="payment_method"')
        guest = self.client.post(
            f"/order/table/{token}/",
            {
                "idempotency_key": str(menu.context["idempotency_key"]),
                "tracking_token": menu.context["tracking_token"],
                "payment_method": "counter",
                f"quantity_{self.product.pk}": "2",
            },
        )
        self.assertEqual(guest.status_code, 302)
        order = self.business.orders.get()
        self.assertEqual(order.table, table)
        self.assertEqual(order.location, location)
        self.assertEqual(order.payment_method, Order.PaymentMethod.COUNTER)
        self.assertEqual(order.guest_tracking_digest is not None, True)
        from .permissions import order_tracking_cookie_name

        tracking_cookie = guest.cookies[order_tracking_cookie_name(order.pk)]
        self.assertTrue(tracking_cookie["httponly"])
        tracking = self.client.get(guest.url)
        self.assertEqual(tracking.status_code, 200)
        self.assertContains(tracking, "Order summary")
        self.assertContains(tracking, "Rp 9")
        self.assertEqual(self.client.get(f"/order/{order.pk}/status/").status_code, 200)
        another_guest = Client()
        self.assertEqual(another_guest.get(guest.url).status_code, 404)
        another_guest.cookies[order_tracking_cookie_name(order.pk)] = "tampered"
        self.assertEqual(another_guest.get(guest.url).status_code, 404)

    def test_table_order_cannot_select_unverified_qris_provider(self):
        self.client.force_login(self.owner)
        location = self.business.locations.get(name="Main location")
        self.client.post(
            f"/app/{self.business.slug}/tables/",
            {"name": "T4", "location": str(location.pk)},
        )
        table = DiningTable.objects.get(business=self.business)
        token = self.client.get(f"/app/{self.business.slug}/tables/").context[
            "table_tokens"
        ][table.pk]
        PaymentConnection.objects.create(
            business=self.business,
            provider="midtrans",
            environment="sandbox",
            merchant_id="test",
            credentials_ciphertext="encrypted",
        )
        menu = self.client.get(f"/order/table/{token}/")
        response = self.client.post(
            f"/order/table/{token}/",
            {
                "idempotency_key": str(menu.context["idempotency_key"]),
                "tracking_token": menu.context["tracking_token"],
                "payment_method": "qris:midtrans",
                f"quantity_{self.product.pk}": "1",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.business.orders.exists())

    def test_catalog_manager_creates_promotion_used_by_customer_menu_and_order(self):
        self.client.force_login(self.owner)
        data = {
            "name": "Coffee hour",
            "section": "Today’s Promotions",
            "active": "on",
            "discount_type": Promotion.DiscountType.FIXED_PRICE,
            "value": "2.00",
            "priority": "5",
            "products": [str(self.product.pk)],
            "days_of_week": [str(day) for day in range(7)],
        }
        saved = self.client.post(f"/app/{self.business.slug}/promotions/", data)
        self.assertEqual(saved.status_code, 302)
        promotion = self.business.promotions.get(name="Coffee hour")
        self.assertEqual(list(promotion.products.values_list("pk", flat=True)), [self.product.pk])
        location = self.business.locations.get(name="Main location")
        table = DiningTable.objects.create(
            business=self.business, location=location, name="Promo table"
        )
        from django.core import signing

        table_token = signing.dumps(
            [str(table.pk), str(table.qr_version)], salt="veyra-table-qr"
        )
        menu = self.client.get(f"/order/table/{table_token}/")
        self.assertContains(menu, "Coffee hour")
        self.assertContains(menu, "Rp 2")
        menu_data = self.client.get(f"/order/table/{table_token}/menu.json")
        self.assertEqual(menu_data.status_code, 200)
        self.assertEqual(menu_data.json()["products"][0]["price"], "2.00")
        self.assertEqual(
            OutboxEvent.objects.filter(business=self.business, event_type="menu.update").count(),
            1,
        )
        placed = self.client.post(
            f"/order/table/{table_token}/",
            {
                "idempotency_key": str(menu.context["idempotency_key"]),
                "tracking_token": menu.context["tracking_token"],
                "payment_method": "counter",
                f"quantity_{self.product.pk}": "1",
            },
        )
        self.assertEqual(placed.status_code, 302)
        line = self.business.orders.get().lines.get()
        self.assertEqual(line.base_unit_price, Decimal("4.50"))
        self.assertEqual(line.unit_price, Decimal("2.00"))

    def test_reports_separate_paid_pending_and_cancelled_and_reject_other_tenant(self):
        paid = create_order(
            business=self.business, actor=self.owner, items=[(self.product.pk, 2)]
        )
        Order.objects.filter(pk=paid.pk).update(payment_status=Order.PaymentStatus.PAID)
        create_order(
            business=self.business, actor=self.owner, items=[(self.product.pk, 1)]
        )
        cancelled = create_order(
            business=self.business, actor=self.owner, items=[(self.product.pk, 1)]
        )
        Order.objects.filter(pk=cancelled.pk).update(status=Order.Status.CANCELLED)
        self.client.force_login(self.owner)
        reports = self.client.get(f"/app/{self.business.slug}/reports/")
        self.assertEqual(reports.status_code, 200)
        self.assertEqual(reports.context["paid_total"], Decimal("9.00"))
        self.assertEqual(reports.context["pending_total"], Decimal("4.50"))
        self.assertEqual(reports.context["cancelled_count"], 1)
        self.assertEqual(reports.context["best_sellers"][0]["units"], 2)
        other_owner = User.objects.create_user(
            "reports-other@example.com", "Long-Test-Password-7231!safe"
        )
        other_business = create_business(
            owner=other_owner, name="Other cafe", slug="other-report-cafe"
        )
        self.assertEqual(
            self.client.get(f"/app/{other_business.slug}/reports/").status_code, 404
        )

    def test_recipe_stock_consumes_on_preparation_and_returns_on_cancellation(self):
        self.client.force_login(self.owner)
        location = self.business.locations.get(name="Main location")
        item = create_inventory_item(
            business=self.business,
            actor=self.owner,
            name="Coffee beans",
            unit="g",
            location_id=location.pk,
            opening_stock=5,
        )
        recipe = self.client.post(
            f"/app/{self.business.slug}/recipes/",
            {"product": str(self.product.pk), "item": str(item.pk), "quantity": "0.500"},
        )
        self.assertEqual(recipe.status_code, 302)
        order = create_order(
            business=self.business,
            actor=self.owner,
            items=[(self.product.pk, 2)],
            location_id=location.pk,
        )
        status_url = f"/app/{self.business.slug}/orders/{order.pk}/status/"
        self.assertEqual(self.client.post(status_url, {"status": "preparing"}).status_code, 302)
        inventory = self.client.get(f"/app/{self.business.slug}/inventory/")
        self.assertContains(inventory, "4.000 g")
        self.assertEqual(
            self.client.post(status_url, {"status": "cancelled"}).status_code, 302
        )
        inventory = self.client.get(f"/app/{self.business.slug}/inventory/")
        self.assertContains(inventory, "5.000 g")
        self.assertEqual(
            self.business.stock_movements.filter(order=order).count(), 2
        )

    def test_pos_records_cash_tender_change_and_idempotent_retry(self):
        self.client.force_login(self.owner)
        order = create_order(
            business=self.business, actor=self.owner, items=[(self.product.pk, 2)]
        )
        key = str(uuid.uuid4())
        endpoint = f"/app/{self.business.slug}/orders/{order.pk}/payment/"
        payment = {
            "method": "cash",
            "cash_received": "10.00",
            "reference": "",
            "idempotency_key": key,
        }
        self.assertEqual(self.client.post(endpoint, payment).status_code, 302)
        order.refresh_from_db()
        tender = OrderTender.objects.get(order=order)
        self.assertEqual(order.payment_status, Order.PaymentStatus.PAID)
        self.assertEqual(order.payment_method, Order.PaymentMethod.CASH)
        self.assertEqual(tender.amount, Decimal("9.00"))
        self.assertEqual(tender.change_due, Decimal("1.00"))
        self.assertEqual(self.client.post(endpoint, payment).status_code, 302)
        self.assertEqual(OrderTender.objects.filter(order=order).count(), 1)

        unpaid = create_order(
            business=self.business, actor=self.owner, items=[(self.product.pk, 1)]
        )
        underpaid = self.client.post(
            f"/app/{self.business.slug}/orders/{unpaid.pk}/payment/",
            {**payment, "cash_received": "1.00", "idempotency_key": str(uuid.uuid4())},
        )
        self.assertEqual(underpaid.status_code, 302)
        unpaid.refresh_from_db()
        self.assertEqual(unpaid.payment_status, Order.PaymentStatus.UNPAID)

        card_order = create_order(
            business=self.business, actor=self.owner, items=[(self.product.pk, 1)]
        )
        card_response = self.client.post(
            f"/app/{self.business.slug}/orders/{card_order.pk}/payment/",
            {
                "method": "card",
                "cash_received": "",
                "reference": "terminal-receipt-42",
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        self.assertEqual(card_response.status_code, 302)
        self.assertEqual(card_order.tender.method, OrderTender.Method.CARD)
        self.assertEqual(card_order.tender.change_due, Decimal("0.00"))

    def test_unpaid_qris_table_order_cannot_enter_preparation(self):
        self.product.price = Decimal("25000.00")
        self.product.save(update_fields=["price"])
        location = self.business.locations.get(name="Main location")
        table = DiningTable.objects.create(
            business=self.business, location=location, name="T9"
        )
        order = create_order(
            business=self.business,
            actor=None,
            items=[(self.product.pk, 1)],
            order_type=Order.OrderType.DINE_IN,
            table=table,
            guest_tracking_token=uuid.uuid4().hex + uuid.uuid4().hex,
            payment_method=Order.PaymentMethod.QRIS,
            payment_provider=PaymentConnection.Provider.MIDTRANS,
        )
        with self.assertRaisesRegex(ValueError, "before payment is confirmed"):
            transition_order(
                business=self.business,
                order_id=order.pk,
                status=Order.Status.PREPARING,
                actor=self.owner,
            )

    def test_fractional_midtrans_order_is_rejected_before_creating_payment(self):
        self.product.price = Decimal("25000.50")
        self.product.save(update_fields=["price"])
        with self.assertRaisesRegex(ValueError, "Choose counter payment"):
            create_order(
                business=self.business, actor=self.owner, items=[(self.product.pk, 1)],
                payment_method=Order.PaymentMethod.QRIS, payment_provider=PaymentConnection.Provider.MIDTRANS,
            )
        self.assertEqual(self.business.orders.count(), 0)

    @override_settings(PAYMENT_CREDENTIALS_ENCRYPTION_KEY=Fernet.generate_key().decode())
    @patch("urllib.request.urlopen")
    def test_guest_qris_checkout_tracks_payment_and_reuses_pending_transaction(self, mock_urlopen):
        self.product.price = Decimal("25000.00")
        self.product.save(update_fields=["price"])
        connection = save_payment_connection(
            business=self.business,
            actor=self.owner,
            provider="midtrans",
            environment="sandbox",
            merchant_id="GUEST-MERCHANT",
            credentials={"server_key": "SB-Mid-server-TESTKEY12345678"},
        )
        connection.verified_at = timezone.now()
        connection.verification_state = "verified"
        connection.approval_reference = "test-approval"
        connection.save(update_fields=["verified_at", "verification_state", "approval_reference"])
        self.client.force_login(self.owner)
        location = self.business.locations.get(name="Main location")
        self.client.post(
            f"/app/{self.business.slug}/tables/",
            {"name": "T8", "location": str(location.pk)},
        )
        table = DiningTable.objects.get(business=self.business)
        token = self.client.get(f"/app/{self.business.slug}/tables/").context[
            "table_tokens"
        ][table.pk]
        menu = self.client.get(f"/order/table/{token}/")
        response_mock = MagicMock()
        response_mock.read.return_value = json.dumps({
            "status_code": "201",
            "transaction_id": "guest-mid-tx",
            "qr_string": "000201guestqris",
        }).encode()
        response_mock.__enter__.return_value = response_mock
        mock_urlopen.return_value = response_mock
        form = {
            "idempotency_key": str(menu.context["idempotency_key"]),
            "tracking_token": menu.context["tracking_token"],
            "payment_method": "qris:midtrans",
            f"quantity_{self.product.pk}": "1",
        }
        placed = self.client.post(f"/order/table/{token}/", form)
        self.assertEqual(placed.status_code, 302)
        order = self.business.orders.get()
        self.assertEqual(order.payment_status, Order.PaymentStatus.PENDING)
        self.assertEqual(order.payments.count(), 1)
        tracking = self.client.get(placed.url)
        self.assertEqual(tracking.status_code, 200)
        qr = self.client.get(f"/order/{order.pk}/payment.svg")
        self.assertEqual(qr.status_code, 200)
        self.assertEqual(qr["Content-Type"], "image/svg+xml")
        self.client.post(f"/order/table/{token}/", form)
        self.assertEqual(order.payments.count(), 1)

    def test_revoked_table_qr_cannot_submit_orders(self):
        self.client.force_login(self.owner)
        location = self.business.locations.get(name="Main location")
        self.client.post(
            f"/app/{self.business.slug}/tables/",
            {"name": "T4", "location": str(location.pk)},
        )
        table = DiningTable.objects.get(business=self.business)
        token = self.client.get(f"/app/{self.business.slug}/tables/").context[
            "table_tokens"
        ][table.pk]
        self.client.post(
            f"/app/{self.business.slug}/tables/",
            {"action": "revoke", "table": str(table.pk)},
        )
        response = self.client.post(
            f"/order/table/{token}/",
            {"idempotency_key": str(uuid.uuid4()), f"quantity_{self.product.pk}": "1"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(self.business.orders.exists())

    def test_regenerated_table_qr_invalidates_previous_link(self):
        self.client.force_login(self.owner)
        location = self.business.locations.get(name="Main location")
        self.client.post(
            f"/app/{self.business.slug}/tables/",
            {"name": "T4", "location": str(location.pk)},
        )
        table = DiningTable.objects.get(business=self.business)
        token = self.client.get(f"/app/{self.business.slug}/tables/").context[
            "table_tokens"
        ][table.pk]
        self.client.post(
            f"/app/{self.business.slug}/tables/",
            {"action": "rotate", "table": str(table.pk)},
        )
        self.assertEqual(self.client.get(f"/order/table/{token}/").status_code, 404)

    def test_owner_can_open_and_use_the_core_operations_pages(self):
        self.client.force_login(self.owner)
        routes = [
            f"/app/{self.business.slug}/",
            f"/app/{self.business.slug}/catalog/",
            f"/app/{self.business.slug}/pos/",
            f"/app/{self.business.slug}/locations/",
            f"/app/{self.business.slug}/customers/",
            f"/app/{self.business.slug}/inventory/",
            f"/app/{self.business.slug}/team/",
            f"/app/{self.business.slug}/stations/",
            f"/app/{self.business.slug}/payments/",
        ]
        for route in routes:
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route).status_code, 200)
        customer_response = self.client.post(
            f"/app/{self.business.slug}/customers/",
            {"name": "Maya", "email": "maya@example.com", "phone": "08123", "note": "Regular"},
        )
        self.assertEqual(customer_response.status_code, 302)
        customer = self.business.customers.get(email="maya@example.com")
        location = self.business.locations.get(name="Main location")
        inventory_response = self.client.post(
            f"/app/{self.business.slug}/inventory/",
            {
                "action": "add",
                "name": "Oat milk",
                "sku": "OAT-1",
                "unit": "L",
                "location": str(location.pk),
                "opening_stock": "12.500",
            },
        )
        self.assertEqual(inventory_response.status_code, 302)
        order_response = self.client.post(
            f"/app/{self.business.slug}/pos/",
            {
                "idempotency_key": str(uuid.uuid4()),
                "order_type": "takeaway",
                "customer": str(customer.pk),
                f"quantity_{self.product.pk}": "1",
            },
        )
        self.assertEqual(order_response.status_code, 302)
        order = self.business.orders.get()
        self.assertEqual(order.customer_name_snapshot, "Maya")

    def test_one_primary_account_can_manage_multiple_businesses(self):
        second = create_business(owner=self.owner, name="Second shop", slug="second-shop")
        self.client.force_login(self.owner)
        response = self.client.get("/app/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.business.name)
        self.assertContains(response, second.name)

    def test_inventory_opening_stock_and_adjustment_are_audited(self):
        location = self.business.locations.get(name="Main location")
        item = create_inventory_item(
            business=self.business,
            actor=self.owner,
            name="Coffee beans",
            unit="kg",
            location_id=location.pk,
            opening_stock=Decimal("5.000"),
        )
        movement = adjust_stock(
            business=self.business,
            actor=self.owner,
            item_id=item.pk,
            location_id=location.pk,
            quantity_delta=Decimal("-1.250"),
            reason=StockMovement.Reason.WASTE,
            idempotency_key=uuid.uuid4(),
            note="Damaged bag",
        )
        balance = InventoryBalance.objects.get(item=item, location=location)
        self.assertEqual(balance.quantity, Decimal("3.750"))
        self.assertEqual(movement.quantity_delta, Decimal("-1.250"))
        with self.assertRaises(ValueError):
            StockMovement.objects.filter(pk=movement.pk).update(note="tampered")
        with self.assertRaises(ValueError):
            StockMovement.objects.filter(pk=movement.pk).delete()
        self.assertTrue(
            self.business.audit_events.filter(action="inventory.stock_adjusted").exists()
        )

    def test_inventory_adjustments_are_idempotent_and_cannot_go_negative(self):
        location = self.business.locations.get(name="Main location")
        item = create_inventory_item(
            business=self.business,
            actor=self.owner,
            name="Milk",
            unit="L",
            location_id=location.pk,
            opening_stock=Decimal("2"),
        )
        key = uuid.uuid4()
        kwargs = {
            "business": self.business,
            "actor": self.owner,
            "item_id": item.pk,
            "location_id": location.pk,
            "quantity_delta": Decimal("-1"),
            "reason": StockMovement.Reason.ADJUSTMENT,
            "idempotency_key": key,
        }
        first = adjust_stock(**kwargs)
        retry = adjust_stock(**kwargs)
        self.assertEqual(first.pk, retry.pk)
        self.assertEqual(
            InventoryBalance.objects.get(item=item, location=location).quantity, Decimal("1.000")
        )
        with self.assertRaises(ValueError):
            adjust_stock(**{**kwargs, "quantity_delta": Decimal("-2")})
        with self.assertRaises(ValueError):
            adjust_stock(
                **{
                    **kwargs,
                    "idempotency_key": uuid.uuid4(),
                    "quantity_delta": Decimal("-2"),
                }
            )
        self.assertEqual(StockMovement.objects.filter(item=item).count(), 2)
        self.assertEqual(
            InventoryBalance.objects.get(item=item, location=location).quantity, Decimal("1.000")
        )

    def test_inventory_adjustment_creates_missing_location_balance(self):
        location = self.business.locations.create(name="Second shop")
        item = InventoryItem.objects.create(business=self.business, name="Tea", unit="box")
        adjust_stock(
            business=self.business,
            actor=self.owner,
            item_id=item.pk,
            location_id=location.pk,
            quantity_delta=Decimal("3"),
            reason=StockMovement.Reason.RECEIVED,
            idempotency_key=uuid.uuid4(),
        )
        self.assertEqual(
            InventoryBalance.objects.get(item=item, location=location).quantity, Decimal("3.000")
        )

    def test_new_location_gets_zero_balance_for_existing_inventory(self):
        main = self.business.locations.get(name="Main location")
        item = create_inventory_item(
            business=self.business,
            actor=self.owner,
            name="Flour",
            unit="kg",
            location_id=main.pk,
            opening_stock=Decimal("8"),
        )
        second = create_location(
            business=self.business, actor=self.owner, name="Second shop", address="Main road"
        )
        self.assertEqual(
            InventoryBalance.objects.get(item=item, location=second).quantity, Decimal("0")
        )

    def test_invitation_token_is_hashed_and_acceptance_grants_only_selected_permissions(self):
        invitation, token = create_invitation(
            business=self.business,
            actor=self.owner,
            email="new-staff@example.com",
            permissions=["orders.view"],
        )
        self.assertNotEqual(invitation.token_digest, token)
        self.assertEqual(len(invitation.token_digest), 64)
        wrong_user = User.objects.create_user(
            "wrong-person@example.com", "Long-Test-Password-8848!safe"
        )
        with self.assertRaises(ValueError):
            accept_invitation(token=token, business_slug=self.business.slug, actor=wrong_user)
        invitee = User.objects.create_user("new-staff@example.com", "Long-Test-Password-8848!safe")
        membership = accept_invitation(token=token, business_slug=self.business.slug, actor=invitee)
        self.assertEqual(membership.permissions, ["orders.view"])
        with self.assertRaises(ValueError):
            accept_invitation(token=token, business_slug=self.business.slug, actor=invitee)

    def test_collaborator_cannot_grant_permissions_they_do_not_have(self):
        manager = User.objects.create_user("manager@example.com", "Long-Test-Password-8849!safe")
        Membership.objects.create(
            business=self.business,
            user=manager,
            permissions=["employees.manage", "orders.view"],
        )
        with self.assertRaises(PermissionError):
            create_invitation(
                business=self.business,
                actor=manager,
                email="new-staff@example.com",
                permissions=["inventory.manage"],
            )
        invitation, _ = create_invitation(
            business=self.business,
            actor=manager,
            email="invited@example.com",
            permissions=["orders.view"],
        )
        self.assertEqual(invitation.permissions, ["orders.view"])

    def test_staff_without_inventory_permission_cannot_adjust_stock(self):
        staff = User.objects.create_user("stock@example.com", "Very-Long-Test-Password-741!")
        Membership.objects.create(
            business=self.business,
            user=staff,
            permissions=["inventory.view"],
        )
        location = self.business.locations.get(name="Main location")
        item = InventoryItem.objects.create(business=self.business, name="Tea", unit="box")
        self.client.force_login(staff)
        self.assertEqual(self.client.get(f"/app/{self.business.slug}/inventory/").status_code, 200)
        response = self.client.post(
            f"/app/{self.business.slug}/inventory/",
            {
                "action": "adjust",
                "item": str(item.pk),
                "location": str(location.pk),
                "quantity_delta": "1",
                "reason": StockMovement.Reason.RECEIVED,
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_order_status_web_action_requires_post_and_owner_permission(self):
        order = create_order(business=self.business, actor=self.owner, items=[(self.product.pk, 1)])
        self.client.force_login(self.owner)
        response = self.client.post(
            f"/app/{self.business.slug}/orders/{order.pk}/status/", {"status": "preparing"}
        )
        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PREPARING)

    def test_staff_needs_explicit_permissions_to_open_catalog_or_create_orders(self):
        staff = User.objects.create_user("staff@example.com", "Very-Long-Test-Password-740!")
        Membership.objects.create(business=self.business, user=staff)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(f"/app/{self.business.slug}/catalog/").status_code, 403)
        self.assertNotContains(self.client.get(f"/app/{self.business.slug}/"), self.product.name)
        response = self.client.post(
            f"/app/{self.business.slug}/pos/",
            {
                "idempotency_key": str(uuid.uuid4()),
                "order_type": "takeaway",
                f"quantity_{self.product.pk}": "1",
            },
        )
        self.assertEqual(response.status_code, 403)


class PromotionPricingTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("pricing@example.com", "Long-Test-Password-9941!safe")
        self.business = create_business(owner=self.owner, name="Pricing cafe", slug="pricing-cafe")
        self.product = Product.objects.create(
            business=self.business, name="Regular coffee", price=Decimal("25000.00")
        )

    def test_recurring_midnight_price_uses_business_timezone_and_restores_base_price(self):
        promotion = Promotion.objects.create(
            business=self.business,
            name="Midnight Specials",
            section="Midnight Specials",
            discount_type=Promotion.DiscountType.FIXED_PRICE,
            value=Decimal("18000.00"),
            start_time=timezone.datetime(2026, 1, 1, 0, 0).time(),
            end_time=timezone.datetime(2026, 1, 1, 6, 0).time(),
        )
        promotion.products.add(self.product)
        midnight_jakarta = datetime(2026, 11, 15, 17, 0, tzinfo=dt_timezone.utc)
        after_end_jakarta = datetime(2026, 11, 15, 23, 0, tzinfo=dt_timezone.utc)
        active = price_product(product=self.product, at=midnight_jakarta)
        expired = price_product(product=self.product, at=after_end_jakarta)
        self.assertEqual(active.unit_price, Decimal("18000.00"))
        self.assertEqual(active.discount_amount, Decimal("7000.00"))
        self.assertEqual(active.promotions[0].name, "Midnight Specials")
        self.assertEqual(expired.unit_price, Decimal("25000.00"))

    def test_highest_priority_wins_unless_every_applied_promotion_is_stackable(self):
        featured = Promotion.objects.create(
            business=self.business,
            name="Featured price",
            discount_type=Promotion.DiscountType.FIXED_PRICE,
            value=Decimal("20000.00"),
            priority=10,
        )
        featured.products.add(self.product)
        additional = Promotion.objects.create(
            business=self.business,
            name="Extra ten percent",
            discount_type=Promotion.DiscountType.PERCENT,
            value=Decimal("10.00"),
            priority=5,
            stackable=True,
        )
        additional.products.add(self.product)
        self.assertEqual(price_product(product=self.product).unit_price, Decimal("20000.00"))
        featured.stackable = True
        featured.save(update_fields=["stackable"])
        quote = price_product(product=self.product)
        self.assertEqual(quote.unit_price, Decimal("18000.00"))
        self.assertEqual(len(quote.promotions), 2)

    @patch("core.pricing.timezone.now")
    def test_order_lines_keep_base_price_and_applied_discount_snapshot(self, mock_now):
        mock_now.return_value = datetime(2026, 11, 15, 17, 0, tzinfo=dt_timezone.utc)
        promotion = Promotion.objects.create(
            business=self.business,
            name="Midnight Specials",
            discount_type=Promotion.DiscountType.FIXED_PRICE,
            value=Decimal("18000.00"),
            start_time=timezone.datetime(2026, 1, 1, 0, 0).time(),
            end_time=timezone.datetime(2026, 1, 1, 6, 0).time(),
        )
        promotion.products.add(self.product)
        order = create_order(
            business=self.business,
            actor=self.owner,
            items=[(self.product.pk, 2)],
        )
        line = order.lines.get()
        self.assertEqual(order.total, Decimal("36000.00"))
        self.assertEqual(line.base_unit_price, Decimal("25000.00"))
        self.assertEqual(line.unit_price, Decimal("18000.00"))
        self.assertEqual(line.promotions.get().discount_amount, Decimal("14000.00"))


class GuestOrderRealtimeTests(TransactionTestCase):
    def test_open_order_stream_closes_after_membership_revocation(self):
        from django.conf import settings

        from config.asgi import application

        owner = User.objects.create_user("stream-owner@example.invalid")
        cashier = User.objects.create_user("revoked-cashier@example.invalid")
        business = create_business(owner=owner, name="Order stream", slug="orders-live")
        member = Membership.objects.create(user=cashier, business=business, permissions=["orders.view"])
        browser = Client()
        browser.force_login(cashier)
        session = browser.cookies[settings.SESSION_COOKIE_NAME].value
        communicator = WebsocketCommunicator(application, f"/ws/business/{business.slug}/orders/", headers=[
            (b"host", b"localhost"), (b"origin", b"http://localhost"),
            (b"cookie", f"{settings.SESSION_COOKIE_NAME}={session}".encode()),
        ])
        product = Product.objects.create(business=business, name="Tea", price="5.00")

        async def exercise():
            self.assertTrue((await communicator.connect())[0])
            await Membership.objects.filter(pk=member.pk).aupdate(active=False)
            order = await sync_to_async(create_order)(business=business, actor=owner, items=[(product.pk, 1)])
            event = await OutboxEvent.objects.aget(business=business, payload__order_id=str(order.pk), event_type="order.update")
            self.assertTrue(await sync_to_async(dispatch_outbox_event)(event.pk))
            message = await communicator.receive_output(timeout=5)
            self.assertEqual(message["type"], "websocket.close")
            self.assertEqual(message["code"], 4403)
            await communicator.disconnect()

        asyncio.run(exercise())

    def test_cashier_payment_http_workflow_updates_live_order_stream(self):
        from django.conf import settings

        from config.asgi import application

        owner = User.objects.create_user("payment-ws@example.com", "Long-Test-Password-5172!safe")
        business = create_business(owner=owner, name="Payment cafe", slug="payment-cafe")
        product = Product.objects.create(business=business, name="Tea", price=Decimal("5.00"))
        order = create_order(business=business, actor=owner, items=[(product.pk, 1)])
        browser = Client()
        browser.force_login(owner)
        session = browser.cookies[settings.SESSION_COOKIE_NAME].value
        communicator = WebsocketCommunicator(
            application, f"/ws/business/{business.slug}/orders/"
        )
        communicator.scope["headers"] = [
            (b"host", b"localhost"),
            (b"origin", b"http://localhost"),
            (b"cookie", f"{settings.SESSION_COOKIE_NAME}={session}".encode()),
        ]

        async def exercise():
            connected, _ = await communicator.connect(timeout=5)
            self.assertTrue(connected)
            response = await sync_to_async(browser.post, thread_sensitive=True)(
                f"/app/{business.slug}/orders/{order.pk}/payment/",
                {
                    "method": "cash",
                    "cash_received": "5.00",
                    "reference": "",
                    "idempotency_key": str(uuid.uuid4()),
                },
            )
            self.assertEqual(response.status_code, 302)
            event_id = await sync_to_async(
                lambda: OutboxEvent.objects.filter(
                    business=business,
                    event_type="order.update",
                    payload__order_id=str(order.pk),
                    payload__event="payment_recorded",
                ).values_list("pk", flat=True).get(),
                thread_sensitive=True,
            )()
            self.assertTrue(
                await sync_to_async(dispatch_outbox_event, thread_sensitive=True)(event_id)
            )
            event = await communicator.receive_json_from(timeout=3)
            self.assertEqual(event["payment_status"], Order.PaymentStatus.PAID)
            self.assertEqual(event["payment_method"], Order.PaymentMethod.CASH)
            await communicator.disconnect()

        asyncio.run(exercise())

    def test_inventory_websocket_is_authorized_and_receives_recipe_stock_change(self):
        from django.conf import settings

        from config.asgi import application

        owner = User.objects.create_user("inventory-ws@example.com", "Long-Test-Password-8201!safe")
        business = create_business(owner=owner, name="Stock cafe", slug="stock-cafe")
        location = business.locations.get()
        product = Product.objects.create(business=business, name="Tea", price=Decimal("5.00"))
        item = create_inventory_item(
            business=business, actor=owner, name="Tea leaves", unit="g",
            location_id=location.pk, opening_stock=5,
        )
        RecipeLine.objects.create(product=product, item=item, quantity=Decimal("0.500"))
        order = create_order(
            business=business, actor=owner, items=[(product.pk, 2)], location_id=location.pk
        )
        browser = Client()
        browser.force_login(owner)
        session = browser.cookies[settings.SESSION_COOKIE_NAME].value
        communicator = WebsocketCommunicator(
            application, f"/ws/business/{business.slug}/inventory/"
        )
        communicator.scope["headers"] = [
            (b"host", b"localhost"),
            (b"origin", b"http://localhost"),
            (b"cookie", f"{settings.SESSION_COOKIE_NAME}={session}".encode()),
        ]

        async def exercise():
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            await sync_to_async(transition_order, thread_sensitive=True)(
                business=business, order_id=order.pk,
                status=Order.Status.PREPARING, actor=owner,
            )
            event_id = await sync_to_async(
                lambda: OutboxEvent.objects.filter(
                    business=business, event_type="inventory.update"
                ).latest("created_at").pk,
                thread_sensitive=True,
            )()
            self.assertTrue(
                await sync_to_async(dispatch_outbox_event, thread_sensitive=True)(event_id)
            )
            update = await communicator.receive_json_from(timeout=3)
            self.assertEqual(update["event"], "inventory.update")
            self.assertEqual(update["quantity"], "4.000")
            adjusted = await sync_to_async(browser.post, thread_sensitive=True)(
                f"/app/{business.slug}/inventory/",
                {
                    "action": "adjust",
                    "item": str(item.pk),
                    "location": str(location.pk),
                    "quantity_delta": "1.000",
                    "reason": "received",
                    "note": "Delivery",
                    "idempotency_key": str(uuid.uuid4()),
                },
            )
            self.assertEqual(adjusted.status_code, 302)
            adjustment_event = await sync_to_async(
                lambda: OutboxEvent.objects.filter(
                    business=business,
                    event_type="inventory.update",
                    published_at__isnull=True,
                ).latest("created_at").pk,
                thread_sensitive=True,
            )()
            self.assertTrue(
                await sync_to_async(dispatch_outbox_event, thread_sensitive=True)(
                    adjustment_event
                )
            )
            update = await communicator.receive_json_from(timeout=3)
            self.assertEqual(update["quantity"], "5.000")
            await communicator.disconnect()

        asyncio.run(exercise())

    def test_scheduled_price_boundary_publishes_live_menu_event(self):
        from django.core import signing
        from django.core.management import call_command

        from config.asgi import application

        owner = User.objects.create_user("schedule-ws@example.com", "Long-Test-Password-6881!safe")
        business = create_business(owner=owner, name="Schedule cafe", slug="schedule-cafe")
        table = DiningTable.objects.create(
            business=business, location=business.locations.get(), name="T1"
        )
        product = Product.objects.create(business=business, name="Tea", price=Decimal("5.00"))
        start = timezone.now() + timedelta(minutes=1)
        promotion = Promotion.objects.create(
            business=business,
            name="Soon",
            discount_type=Promotion.DiscountType.PERCENT,
            value=Decimal("10.00"),
            starts_at=start,
        )
        promotion.products.add(product)
        token = signing.dumps([str(table.pk), str(table.qr_version)], salt="veyra-table-qr")
        communicator = WebsocketCommunicator(application, f"/ws/guest/menu/{token}/")
        communicator.scope["headers"] = [
            (b"host", b"localhost"),
            (b"origin", b"http://localhost"),
        ]
        initial = start - timedelta(seconds=5)
        after_start = start + timedelta(seconds=5)

        async def exercise():
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            with patch("core.services.timezone.now", return_value=initial), patch(
                "core.management.commands.process_outbox.timezone.now", return_value=initial
            ):
                await sync_to_async(call_command, thread_sensitive=True)(
                    "process_outbox", once=True, stdout=io.StringIO()
                )
            with patch("core.services.timezone.now", return_value=after_start), patch(
                "core.management.commands.process_outbox.timezone.now", return_value=after_start
            ):
                await sync_to_async(call_command, thread_sensitive=True)(
                    "process_outbox", once=True, stdout=io.StringIO()
                )
            message = await communicator.receive_json_from(timeout=3)
            self.assertEqual(message["event"], "menu.update")
            await communicator.disconnect()

        asyncio.run(exercise())

    def test_qr_menu_websocket_receives_promotion_update(self):
        from django.core import signing

        from config.asgi import application

        owner = User.objects.create_user("menu-ws@example.com", "Long-Test-Password-2209!safe")
        business = create_business(owner=owner, name="Menu cafe", slug="menu-cafe")
        table = DiningTable.objects.create(
            business=business, location=business.locations.get(), name="T1"
        )
        token = signing.dumps([str(table.pk), str(table.qr_version)], salt="veyra-table-qr")
        communicator = WebsocketCommunicator(application, f"/ws/guest/menu/{token}/")
        communicator.scope["headers"] = [
            (b"host", b"localhost"),
            (b"origin", b"http://localhost"),
        ]

        async def exercise():
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            event = await sync_to_async(
                OutboxEvent.objects.create,
                thread_sensitive=True,
            )(
                business=business,
                event_type="menu.update",
                payload={"promotion_id": 1},
                next_attempt_at=timezone.now(),
            )
            self.assertTrue(await sync_to_async(dispatch_outbox_event, thread_sensitive=True)(event.pk))
            message = await communicator.receive_json_from(timeout=3)
            self.assertEqual(message["event"], "menu.update")
            await communicator.disconnect()

        asyncio.run(exercise())

    def test_private_websocket_snapshot_and_live_order_update(self):
        from config.asgi import application

        owner = User.objects.create_user("guest-ws@example.com", "Long-Test-Password-9901!safe")
        business = create_business(owner=owner, name="Realtime cafe", slug="realtime-cafe")
        location = business.locations.get(name="Main location")
        table = DiningTable.objects.create(business=business, location=location, name="T1")
        product = Product.objects.create(business=business, name="Tea", price=Decimal("5.00"))
        token = uuid.uuid4().hex + uuid.uuid4().hex
        order = create_order(
            business=business,
            actor=None,
            items=[(product.pk, 1)],
            table=table,
            order_type=Order.OrderType.DINE_IN,
            guest_tracking_token=token,
        )
        cookie_name = order_tracking_cookie_name(order.pk)
        cookie_value = order_tracking_cookie_signer(order.pk).sign(token)
        communicator = WebsocketCommunicator(application, f"/ws/guest/orders/{order.pk}/")
        communicator.scope["headers"] = [
            (b"host", b"localhost"),
            (b"origin", b"http://localhost"),
            (b"cookie", f"{cookie_name}={cookie_value}".encode()),
        ]

        async def exercise():
            anonymous = WebsocketCommunicator(
                application, f"/ws/guest/orders/{order.pk}/"
            )
            anonymous.scope["headers"] = [
                (b"host", b"localhost"),
                (b"origin", b"http://localhost"),
            ]
            rejected, code = await anonymous.connect()
            self.assertFalse(rejected)
            self.assertEqual(code, 4403)
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            snapshot = await communicator.receive_json_from(timeout=3)
            self.assertEqual(snapshot["event"], "snapshot")
            self.assertEqual(snapshot["status"], Order.Status.NEW)
            await sync_to_async(transition_order, thread_sensitive=True)(
                business=business, order_id=order.pk,
                status=Order.Status.PREPARING, actor=owner,
            )
            event_id = await sync_to_async(
                lambda: OutboxEvent.objects.filter(business=business)
                .order_by("-created_at").first().pk,
                thread_sensitive=True,
            )()
            self.assertTrue(await sync_to_async(dispatch_outbox_event, thread_sensitive=True)(event_id))
            update = await communicator.receive_json_from(timeout=3)
            self.assertEqual(update["status"], Order.Status.PREPARING)
            await communicator.disconnect()
            await communicator.wait(timeout=3)

        asyncio.run(exercise())


class StationAndPaymentTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user("owner@example.com", "Very-Long-Test-Password-738!")
        self.business = create_business(owner=self.owner, name="North Star", slug="north-star")
        self.product = Product.objects.create(
            business=self.business, name="House coffee", price=Decimal("4.50")
        )

    def create_station(self, kind, capabilities, name):
        return create_station(
            business=self.business,
            actor=self.owner,
            name=name,
            kind=kind,
            location_id=None,
            capabilities=capabilities,
        )

    def pair(self, station, label):
        _, code = issue_station_pairing_code(station=station, actor=self.owner)
        response = self.client.post(
            "/api/stations/pair/",
            data=json.dumps({"code": code, "label": label}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        return response.json()["device_token"]

    def test_station_pairing_returns_one_time_token_and_hashes_both_secrets(self):
        station = self.create_station("register", ["catalog.view", "orders.create"], "Front")
        pairing, code = issue_station_pairing_code(station=station, actor=self.owner)
        self.assertNotEqual(pairing.code_digest, code)
        response = self.client.post(
            "/api/stations/pair/",
            data=json.dumps({"code": code, "label": "Tablet 1"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        token = response.json()["device_token"]
        device = PairedDevice.objects.get(station=station)
        self.assertNotEqual(device.token_digest, token)
        self.assertEqual(
            self.client.post(
                "/api/stations/pair/",
                data=json.dumps({"code": code, "label": "Tablet 2"}),
                content_type="application/json",
            ).status_code,
            400,
        )

    def test_register_capabilities_allow_catalog_and_orders_but_not_status_updates(self):
        station = self.create_station(
            "register", ["catalog.view", "orders.view", "orders.create"], "Front"
        )
        token = self.pair(station, "Register tablet")
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
        self.assertEqual(
            self.client.get(f"/api/stations/{station.pk}/catalog/", **headers).status_code, 200
        )
        response = self.client.post(
            f"/api/stations/{station.pk}/orders/create/",
            data=json.dumps(
                {
                    "idempotency_key": str(uuid.uuid4()),
                    "items": [{"product_id": str(self.product.pk), "quantity": 2}],
                }
            ),
            content_type="application/json",
            **headers,
        )
        self.assertEqual(response.status_code, 201)
        order = Order.objects.get(pk=response.json()["id"])
        self.assertEqual(order.total, Decimal("9.00"))
        self.assertEqual(order.created_by_device.station, station)
        denied = self.client.post(
            f"/api/stations/{station.pk}/orders/{order.pk}/status/",
            data=json.dumps({"status": "preparing"}),
            content_type="application/json",
            **headers,
        )
        self.assertEqual(denied.status_code, 403)

    def test_kitchen_device_updates_orders_and_cannot_use_another_station_route(self):
        kitchen = self.create_station("kitchen", ["orders.view", "orders.status.update"], "Kitchen")
        token = self.pair(kitchen, "Kitchen screen")
        order = create_order(business=self.business, actor=self.owner, items=[(self.product.pk, 1)])
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
        response = self.client.post(
            f"/api/stations/{kitchen.pk}/orders/{order.pk}/status/",
            data=json.dumps({"status": "preparing"}),
            content_type="application/json",
            **headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], Order.Status.PREPARING)
        other_station = self.create_station("bar", ["orders.view"], "Bar")
        self.assertEqual(
            self.client.get(f"/api/stations/{other_station.pk}/orders/", **headers).status_code, 401
        )

    def test_revoked_station_session_stops_working(self):
        station = self.create_station("register", ["catalog.view"], "Front")
        token = self.pair(station, "Register tablet")
        device = station.devices.get()
        revoke_paired_device(business=self.business, device_id=device.pk, actor=self.owner)
        response = self.client.get(
            f"/api/stations/{station.pk}/catalog/", HTTP_AUTHORIZATION=f"Bearer {token}"
        )
        self.assertEqual(response.status_code, 401)

    @override_settings(PAYMENT_CREDENTIALS_ENCRYPTION_KEY="")
    def test_payment_credentials_require_encryption_key(self):
        with self.assertRaisesRegex(RuntimeError, "not configured"):
            save_payment_connection(
                business=self.business,
                actor=self.owner,
                provider=PaymentConnection.Provider.MIDTRANS,
                environment=PaymentConnection.Environment.SANDBOX,
                merchant_id="merchant-1",
                credentials={"server_key": "secret"},
            )

    def test_payment_credentials_are_encrypted_and_business_owner_controlled(self):
        key = Fernet.generate_key().decode()
        with override_settings(PAYMENT_CREDENTIALS_ENCRYPTION_KEY=key):
            connection = save_payment_connection(
                business=self.business,
                actor=self.owner,
                provider=PaymentConnection.Provider.MIDTRANS,
                environment=PaymentConnection.Environment.SANDBOX,
                merchant_id="merchant-1",
                credentials={"server_key": "top-secret", "client_key": "client-secret"},
            )
            self.assertNotIn("top-secret", connection.credentials_ciphertext)
            self.assertEqual(
                read_payment_credentials(connection=connection, actor=self.owner),
                {"server_key": "top-secret", "client_key": "client-secret"},
            )
            other = User.objects.create_user("other@example.com", "Very-Long-Test-Password-739!")
            with self.assertRaises(PermissionError):
                read_payment_credentials(connection=connection, actor=other)
            with self.assertRaises(PermissionError):
                save_payment_connection(
                    business=self.business,
                    actor=other,
                    provider=PaymentConnection.Provider.MIDTRANS,
                    environment=PaymentConnection.Environment.SANDBOX,
                    merchant_id="merchant-2",
                    credentials={"server_key": "other-secret"},
                )
            delete_payment_connection(
                business=self.business, connection_id=connection.pk, actor=self.owner
            )
            self.assertFalse(PaymentConnection.objects.filter(pk=connection.pk).exists())

    def test_management_pages_are_restricted_to_business_owner(self):
        other = User.objects.create_user("other@example.com", "Very-Long-Test-Password-739!")
        self.client.force_login(other)
        self.assertEqual(self.client.get(f"/app/{self.business.slug}/stations/").status_code, 404)
        self.assertEqual(self.client.get(f"/app/{self.business.slug}/payments/").status_code, 404)


class OwnerBackfillMigrationTests(TransactionTestCase):
    migrate_from = [("core", "0007_invitation")]
    migrate_to = [("core", "0008_owner_stations_payment_connections")]

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        User = old_apps.get_model("core", "User")
        Business = old_apps.get_model("core", "Business")
        Membership = old_apps.get_model("core", "Membership")
        owner = User.objects.create(email="legacy-owner@example.com", password="!hash")
        collaborator = User.objects.create(
            email="legacy-collaborator@example.com", password="!hash"
        )
        business = Business.objects.create(
            name="Legacy business", slug="legacy-business", business_type="other"
        )
        Membership.objects.create(business=business, user=owner, role="owner", permissions=[])
        Membership.objects.create(
            business=business,
            user=collaborator,
            role="manager",
            permissions=["orders.view"],
        )
        self.business_id = business.pk
        self.owner_id = owner.pk
        self.collaborator_id = collaborator.pk
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.new_apps = executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_owner_migration_preserves_permissions_without_role_hierarchy(self):
        Business = self.new_apps.get_model("core", "Business")
        Membership = self.new_apps.get_model("core", "Membership")
        business = Business.objects.get(pk=self.business_id)
        collaborator = Membership.objects.get(
            business_id=self.business_id, user_id=self.collaborator_id
        )
        self.assertEqual(business.owner_id, self.owner_id)
        self.assertEqual(collaborator.permissions, ["orders.view"])
        self.assertNotIn("role", [field.name for field in Membership._meta.fields])


@override_settings(
    MAILERS={"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}
)
class AccountTests(TransactionTestCase):
    def test_landing_page_is_public_and_has_security_headers(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Everything<br><em>in sync.</em>")
        self.assertContains(response, "veyra-pos-example.png")
        self.assertContains(response, "data-cinematic-story")
        self.assertContains(response, "One account.")
        self.assertContains(response, "Each station")
        self.assertContains(response, "/media/landing-film/")
        self.assertContains(response, "veyra-story-poster.webp")
        self.assertContains(response, 'preload="metadata"')
        self.assertContains(response, "muted playsinline")
        self.assertNotContains(response, " autoplay")
        self.assertNotContains(response, "mini-window")
        self.assertIn("Content-Security-Policy", response.headers)
        self.assertEqual(self.client.get("/healthz/").content, b"ok")

    def test_landing_film_supports_byte_range_seeking(self):
        film = Path(__file__).parent / "static/core/landing/veyra-story.mp4"
        size = film.stat().st_size
        with film.open("rb") as source:
            source.seek(100)
            expected = source.read(100)

        response = self.client.get("/media/landing-film/", HTTP_RANGE="bytes=100-199")
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response["Accept-Ranges"], "bytes")
        self.assertEqual(response["Content-Range"], f"bytes 100-199/{size}")
        self.assertEqual(response["Content-Length"], "100")
        self.assertEqual(b"".join(response.streaming_content), expected)
        response.close()

        suffix = self.client.get("/media/landing-film/", HTTP_RANGE="bytes=-100")
        self.assertEqual(suffix.status_code, 206)
        self.assertEqual(suffix["Content-Range"], f"bytes {size - 100}-{size - 1}/{size}")
        self.assertEqual(len(b"".join(suffix.streaming_content)), 100)
        suffix.close()

        head = self.client.head("/media/landing-film/", HTTP_RANGE="bytes=100-199")
        self.assertEqual(head.status_code, 206)
        self.assertEqual(head["Content-Range"], f"bytes 100-199/{size}")
        self.assertEqual(head["Content-Length"], "100")
        self.assertEqual(head.content, b"")

        unsatisfiable = self.client.get("/media/landing-film/", HTTP_RANGE=f"bytes={size}-")
        self.assertEqual(unsatisfiable.status_code, 416)
        self.assertEqual(unsatisfiable["Content-Range"], f"bytes */{size}")

    def test_registration_requires_email_verification(self):
        response = self.client.post(
            "/register/",
            {
                "email": "New@Example.com",
                "first_name": "New",
                "password1": "Long-Test-Password-8842!safe",
                "password2": "Long-Test-Password-8842!safe",
            },
        )
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(email="new@example.com")
        self.assertFalse(user.is_active)
        self.assertEqual(len(mail.outbox), 1)
        link = mail.outbox[0].body.split("Confirm your email: ", 1)[1]
        path = urlparse(link).path
        verify = self.client.get(path)
        self.assertEqual(verify.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertIsNotNone(user.email_verified_at)

    def test_duplicate_registration_response_does_not_confirm_account_exists(self):
        User.objects.create_user("already@example.com", "Long-Test-Password-8843!safe")
        response = self.client.post("/register/", {"email": "already@example.com"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<h1 id="account-title">Check your email</h1>', html=True)
        self.assertNotContains(response, "already@example.com")

    def test_login_requires_verified_account_and_rotates_session(self):
        user = User.objects.create_user(
            "login@example.com", "Long-Test-Password-8844!safe", is_active=False
        )
        response = self.client.post(
            "/login/", {"email": user.email, "password": "Long-Test-Password-8844!safe"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        user.is_active = True
        user.save(update_fields=["is_active"])
        response = self.client.post(
            "/login/", {"email": user.email, "password": "Long-Test-Password-8844!safe"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session["_auth_user_id"], str(user.pk))

    def test_login_throttles_repeated_failures_and_logout_requires_post(self):
        cache.clear()
        user = User.objects.create_user("throttle@example.com", "Long-Test-Password-8845!safe")
        for _ in range(8):
            self.client.post("/login/", {"email": user.email, "password": "incorrect"})
        response = self.client.post("/login/", {"email": user.email, "password": "incorrect"})
        self.assertContains(response, "Too many attempts", status_code=200)
        self.client.force_login(user)
        self.assertEqual(self.client.get("/logout/").status_code, 405)
        response = self.client.post("/logout/")
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_rejects_external_redirect_targets(self):
        user = User.objects.create_user("safe@example.com", "Long-Test-Password-8852!safe")
        response = self.client.post(
            "/login/",
            {
                "email": user.email,
                "password": "Long-Test-Password-8852!safe",
                "next": "https://attacker.example/collect",
            },
        )
        self.assertEqual(response.url, "/app/")

    def test_password_reset_uses_expiring_django_reset_token(self):
        user = User.objects.create_user("reset@example.com", "Long-Test-Password-8846!safe")
        response = self.client.post("/password-reset/", {"email": user.email})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        link = mail.outbox[0].body.split()[-1]
        response = self.client.get(urlparse(link).path)
        self.assertEqual(response.status_code, 302)
        response = self.client.get(response.url)
        self.assertEqual(response.status_code, 200)
        self.client.post(
            response.request["PATH_INFO"],
            {
                "new_password1": "Long-Test-Password-8847!safe",
                "new_password2": "Long-Test-Password-8847!safe",
            },
        )
        user.refresh_from_db()
        self.assertTrue(user.check_password("Long-Test-Password-8847!safe"))

    def test_team_invitation_email_and_acceptance_flow(self):
        owner = User.objects.create_user("owner@example.com", "Long-Test-Password-8850!safe")
        business = create_business(owner=owner, name="Team Cafe", slug="team-cafe")
        self.client.force_login(owner)
        response = self.client.post(
            f"/app/{business.slug}/team/",
            {
                "email": "invitee@example.com",
                "permissions": ["orders.view"],
            },
        )
        self.assertEqual(response.status_code, 302)
        invitation = Invitation.objects.get(business=business, email="invitee@example.com")
        self.assertEqual(len(mail.outbox), 1)
        link = mail.outbox[0].body.split()[-1]
        path = urlparse(link).path
        self.client.logout()
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create account")
        self.client.get("/register/", {"next": path})
        response = self.client.post(
            "/register/",
            {
                "email": "invitee@example.com",
                "first_name": "Invitee",
                "password1": "Long-Test-Password-8851!safe",
                "password2": "Long-Test-Password-8851!safe",
                "next": path,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 2)
        verification_url = urlparse(mail.outbox[1].body.split()[-1])
        verification_path = verification_url.path + "?" + verification_url.query
        response = self.client.get(verification_path)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, path)
        invitee = User.objects.get(email="invitee@example.com")
        self.assertEqual(self.client.get(path).status_code, 200)
        response = self.client.post(path)
        self.assertEqual(response.status_code, 302)
        membership = Membership.objects.get(business=business, user=invitee)
        self.assertEqual(membership.permissions, ["orders.view"])
        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.accepted_at)


class StationTokenRotationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner@example.com", "Long-Test-Password-8850!safe")
        self.business = create_business(owner=self.owner, name="Coffee Bar", slug="coffee-bar")
        self.station = create_station(
            business=self.business,
            actor=self.owner,
            name="Bar Station",
            kind="bar",
            location_id=None,
            capabilities=["orders.view", "orders.status.update"],
        )
        _, code = issue_station_pairing_code(station=self.station, actor=self.owner)
        self.device, self.token = pair_station_device(code=code, label="iPad Bar")

    def test_token_rotation_succeeds_and_invalidates_old_token(self):
        # 1. Old token works
        res = self.client.get(
            f"/api/stations/{self.station.pk}/orders/",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )
        self.assertEqual(res.status_code, 200)

        # 2. Rotate token
        rot_res = self.client.post(
            f"/api/stations/{self.station.pk}/rotate-token/",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )
        self.assertEqual(rot_res.status_code, 200)
        new_token = rot_res.json()["device_token"]
        self.assertNotEqual(new_token, self.token)

        # 3. Old token now fails with 401
        res_old = self.client.get(
            f"/api/stations/{self.station.pk}/orders/",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )
        self.assertEqual(res_old.status_code, 401)

        # 4. New token works
        res_new = self.client.get(
            f"/api/stations/{self.station.pk}/orders/",
            HTTP_AUTHORIZATION=f"Bearer {new_token}",
        )
        self.assertEqual(res_new.status_code, 200)

        # 5. Audit event written
        self.assertTrue(
            self.business.audit_events.filter(action="station.device.token_rotated").exists()
        )

    def test_revoked_device_cannot_rotate_token(self):
        revoke_paired_device(
            business=self.business, device_id=self.device.pk, actor=self.owner
        )
        rot_res = self.client.post(
            f"/api/stations/{self.station.pk}/rotate-token/",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )
        self.assertEqual(rot_res.status_code, 401)


class InvitationRevocationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner@example.com", "Long-Test-Password-8850!safe")
        self.business = create_business(owner=self.owner, name="Roastery", slug="roastery")
        self.invitation, self.token = create_invitation(
            business=self.business,
            actor=self.owner,
            email="staff@example.com",
            permissions=["orders.view"],
        )

    def test_owner_can_revoke_invitation(self):
        self.client.force_login(self.owner)
        res = self.client.post(
            f"/app/{self.business.slug}/team/",
            {"action": "revoke_invitation", "invitation_id": str(self.invitation.pk)},
        )
        self.assertEqual(res.status_code, 302)
        self.invitation.refresh_from_db()
        self.assertTrue(self.invitation.expires_at <= timezone.now())

        # Cannot accept revoked invitation
        staff = User.objects.create_user("staff@example.com", "Long-Test-Password-8851!safe")
        self.client.force_login(staff)
        accept_res = self.client.post(
            f"/invitations/{self.business.slug}/{self.token}/accept/"
        )
        self.assertEqual(accept_res.status_code, 400)
        self.assertFalse(Membership.objects.filter(business=self.business, user=staff).exists())


class OperationalWorkflowsAndPrinterTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner@example.com", "Long-Test-Password-8850!safe")
        self.business = create_business(owner=self.owner, name="Boskaf", slug="boskaf")
        self.printer = create_printer(
            business=self.business,
            actor=self.owner,
            name="Kitchen ESC/POS",
            printer_type="network_escpos",
        )
        self.kitchen = create_station(
            business=self.business,
            actor=self.owner,
            name="Kitchen Station",
            kind="kitchen",
            location_id=None,
            handling_mode="both",
            printer_id=self.printer.pk,
            capabilities=["orders.view", "orders.status.update"],
        )
        self.food_item = Product.objects.create(
            business=self.business,
            name="Croissant",
            price=Decimal("35000.00"),
            station=self.kitchen,
        )
        _, code = issue_station_pairing_code(station=self.kitchen, actor=self.owner)
        self.device, self.token = pair_station_device(code=code, label="Kitchen Tablet")

    def test_printer_assignment_and_prep_ticket_routing(self):
        order = create_order(
            business=self.business,
            actor=self.owner,
            items=[(self.food_item.pk, 2)],
            order_type="dine_in",
            table_label="Table 4",
        )
        # Transition to preparing triggers route_order_to_stations
        transition_order(
            business=self.business,
            order_id=order.pk,
            status=Order.Status.PREPARING,
            actor=self.owner,
        )
        jobs = PrintJob.objects.filter(business=self.business, order=order)
        self.assertEqual(jobs.count(), 1)
        job = jobs.first()
        self.assertEqual(job.station, self.kitchen)
        self.assertEqual(job.printer, self.printer)
        self.assertEqual(job.job_type, "prep_ticket")
        self.assertIn("PREPARATION TICKET - NOT A RECEIPT", job.ticket_payload["header"])
        self.assertEqual(job.ticket_payload["table_label"], "Table 4")
        self.assertEqual(job.ticket_payload["items"][0]["name"], "Croissant")
        self.assertEqual(job.ticket_payload["items"][0]["quantity"], 2)

    def test_station_device_can_query_and_acknowledge_print_jobs(self):
        order = create_order(
            business=self.business,
            actor=self.owner,
            items=[(self.food_item.pk, 1)],
        )
        job = create_print_job(
            business=self.business,
            order=order,
            station=self.kitchen,
            printer=self.printer,
        )
        # Device polls print jobs
        res = self.client.get(
            f"/api/stations/{self.kitchen.pk}/print-jobs/",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )
        self.assertEqual(res.status_code, 200)
        jobs_data = res.json()["jobs"]
        self.assertEqual(len(jobs_data), 1)
        self.assertEqual(jobs_data[0]["id"], str(job.pk))

        # Device acknowledges printed
        ack_res = self.client.post(
            f"/api/stations/{self.kitchen.pk}/print-jobs/{job.pk}/ack/",
            json.dumps({"status": "printed"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )
        self.assertEqual(ack_res.status_code, 200)
        job.refresh_from_db()
        self.assertEqual(job.status, "printed")
        self.assertIsNotNone(job.printed_at)

    def test_reprint_ticket_creates_distinct_job(self):
        order = create_order(
            business=self.business,
            actor=self.owner,
            items=[(self.food_item.pk, 1)],
        )
        create_print_job(business=self.business, order=order, station=self.kitchen)
        reprint_res = self.client.post(
            f"/api/stations/{self.kitchen.pk}/orders/{order.pk}/reprint/",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )
        self.assertEqual(reprint_res.status_code, 200)
        jobs = PrintJob.objects.filter(order=order)
        self.assertEqual(jobs.count(), 2)
        reprint_job = jobs.order_by("-created_at").first()
        self.assertTrue(reprint_job.ticket_payload["is_reprint"])

    def test_station_screen_page_renders_for_authorized_users(self):
        self.client.force_login(self.owner)
        res = self.client.get(f"/app/{self.business.slug}/stations/{self.kitchen.pk}/screen/")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Kitchen Station")


@override_settings(PAYMENT_CREDENTIALS_ENCRYPTION_KEY=Fernet.generate_key().decode())
class PaymentIntegrationAndWebhookTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner@example.com", "Long-Test-Password-8850!safe")
        self.business = create_business(owner=self.owner, name="Roastery", slug="roastery")
        self.product = Product.objects.create(
            business=self.business, name="Cold Brew", price=Decimal("25000.00")
        )
        self.server_key = "SB-Mid-server-TESTKEY12345678"
        self.signing_key = "test-notification-secret"
        self.connection = save_payment_connection(
            business=self.business,
            actor=self.owner,
            provider="midtrans",
            environment="sandbox",
            merchant_id="G123456",
            credentials={"server_key": self.server_key, "signature_secret_key": self.signing_key},
        )

    def test_credential_format_alone_does_not_enable_payment(self):
        valid, msg = verify_payment_connection(
            business=self.business,
            connection_id=self.connection.pk,
            actor=self.owner,
        )
        self.assertFalse(valid)
        self.connection.refresh_from_db()
        self.assertIsNone(self.connection.verified_at)
        self.assertEqual(self.connection.verification_state, PaymentConnection.Verification.INCOMPLETE)

    @patch("urllib.request.urlopen")
    def test_initiate_midtrans_qris_and_after_paid_release(self, mock_urlopen):
        self.connection.verified_at = timezone.now()
        self.connection.verification_state = "verified"
        self.connection.approval_reference = "test-approval"
        self.connection.save(update_fields=["verified_at", "verification_state", "approval_reference"])
        order = create_order(
            business=self.business,
            actor=self.owner,
            items=[(self.product.pk, 1)],
        )
        self.assertEqual(order.status, Order.Status.NEW)

        # Mock Midtrans response
        mock_response = {
            "status_code": "201",
            "transaction_id": "mid-tx-998877",
            "actions": [{"name": "generate-qr-code", "url": "https://api.sandbox.midtrans.com/qr/1"}],
            "qr_string": "00020101021226...",
        }
        from unittest.mock import MagicMock
        resp_mock = MagicMock()
        resp_mock.read.return_value = json.dumps(mock_response).encode()
        resp_mock.__enter__.return_value = resp_mock
        mock_urlopen.return_value = resp_mock

        tx = initiate_qris_payment(
            business=self.business,
            order_id=order.pk,
            provider="midtrans",
            actor=self.owner,
        )
        self.assertEqual(tx.status, "pending")
        self.assertEqual(tx.qr_payload, "00020101021226...")
        self.assertEqual(tx.merchant_reference.startswith("ORD-"), True)

        # Simulate webhook notification from Midtrans
        order_id = tx.merchant_reference
        status_code = "200"
        gross_amount = str(int(order.total))
        import hashlib
        raw_sig = f"{order_id}{status_code}{gross_amount}{self.signing_key}"
        sig_key = hashlib.sha512(raw_sig.encode()).hexdigest()

        webhook_payload = {
            "order_id": order_id,
            "status_code": status_code,
            "gross_amount": gross_amount,
            "transaction_status": "settlement",
            "fraud_status": "accept",
            "transaction_id": "mid-tx-998877",
            "signature_key": sig_key,
        }
        body_bytes = json.dumps(webhook_payload).encode()
        resp_mock.read.return_value = json.dumps({**webhook_payload, "payment_type": "qris"}).encode()

        res = self.client.post(
            "/api/payments/webhooks/midtrans/",
            body_bytes,
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)

        # Order must be released: status transitioned to PREPARING
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PREPARING)
        tx.refresh_from_db()
        self.assertEqual(tx.status, "paid")
        self.assertIsNotNone(tx.paid_at)

        # Replay same webhook: idempotent 200
        replay_res = self.client.post(
            "/api/payments/webhooks/midtrans/",
            body_bytes,
            content_type="application/json",
        )
        self.assertEqual(replay_res.status_code, 200)

    def test_webhook_rejects_invalid_signature(self):
        order = create_order(
            business=self.business,
            actor=self.owner,
            items=[(self.product.pk, 1)],
        )
        tx = PaymentTransaction.objects.create(
            business=self.business,
            order=order,
            connection=self.connection,
            provider="midtrans",
            environment="sandbox",
            merchant_reference="ORD-TEST-TAMPERED",
            amount=order.total,
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        bad_payload = {
            "order_id": "ORD-TEST-TAMPERED",
            "status_code": "200",
            "gross_amount": "25000",
            "transaction_status": "settlement",
            "signature_key": "invalidsignaturehash",
        }
        res = self.client.post(
            "/api/payments/webhooks/midtrans/",
            json.dumps(bad_payload),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 401)
        tx.refresh_from_db()
        self.assertEqual(tx.status, "pending")

    @patch("urllib.request.urlopen")
    def test_bca_snap_qris_and_webhook_notification(self, mock_urlopen):
        client_id = "BCA-CLIENT-123"
        client_secret = "BCA-SECRET-KEY-XYZ"
        bca_conn = save_payment_connection(
            business=self.business,
            actor=self.owner,
            provider="bca_qris",
            environment="sandbox",
            merchant_id="BCA-MERCHANT-01",
            credentials={"client_id": client_id, "client_secret": client_secret},
        )
        bca_conn.verified_at = timezone.now()
        bca_conn.verification_state = "verified"
        bca_conn.approval_reference = "test-approval"
        bca_conn.save(update_fields=["verified_at", "verification_state", "approval_reference"])
        order = create_order(
            business=self.business,
            actor=self.owner,
            items=[(self.product.pk, 1)],
        )

        from unittest.mock import MagicMock
        resp_mock = MagicMock()
        mock_response = {
            "responseCode": "2002500",
            "responseMessage": "Successful",
            "referenceNo": "BCA-REF-998811",
            "qrContent": "00020101021226590014ID.LINKAJA.WWW0118...",
        }
        resp_mock.read.return_value = json.dumps(mock_response).encode()
        resp_mock.__enter__.return_value = resp_mock
        mock_urlopen.return_value = resp_mock

        tx = initiate_qris_payment(
            business=self.business,
            order_id=order.pk,
            provider="bca_qris",
            actor=self.owner,
        )
        self.assertEqual(tx.status, "pending")
        self.assertEqual(tx.provider_transaction_id, "BCA-REF-998811")

        # Simulate BCA SNAP notification webhook
        timestamp = timezone.now().strftime("%Y-%m-%dT%H:%M:%S+07:00")
        notify_payload = {
            "partnerReferenceNo": tx.merchant_reference,
            "referenceNo": "BCA-REF-998811",
            "latestTransactionStatus": "00",
            "amount": {"value": "25000.00", "currency": "IDR"},
        }
        minified_body = json.dumps(notify_payload, separators=(",", ":"))
        import hashlib
        import hmac
        body_hash = hashlib.sha256(minified_body.encode()).hexdigest().lower()
        string_to_sign = f"POST:/api/v1.0/qr/qr-mpm-notify:{body_hash}:{timestamp}"
        signature = hmac.new(client_secret.encode(), string_to_sign.encode(), hashlib.sha512).hexdigest()

        res = self.client.post(
            "/api/payments/webhooks/bca_qris/",
            minified_body.encode(),
            content_type="application/json",
            HTTP_X_SIGNATURE=signature,
            HTTP_X_TIMESTAMP=timestamp,
        )
        self.assertEqual(res.status_code, 200)

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PREPARING)
        tx.refresh_from_db()
        self.assertEqual(tx.status, "paid")


