from datetime import timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.core import signing
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Category, Order, OrderLine
from .refunds import reserve_refund
from .test_audit_remediation import ShopFixture


class HistoricalCategoryTests(ShopFixture, TestCase):
    def test_moves_and_deleted_categories_preserve_paid_order_history(self):
        original = Category.objects.create(business=self.business, name="Original category")
        later = Category.objects.create(business=self.business, name="Later category")
        self.product.category = original
        self.product.sku = "Original SKU"
        self.product.save(update_fields=["category", "sku"])
        first = self.order()
        self.tender(first)
        self.product.category = later
        self.product.sku = "New SKU"
        self.product.save(update_fields=["category", "sku"])
        second = self.order()
        self.tender(second)
        original_id = original.pk
        original.name = "Renamed category"
        original.save(update_fields=["name"])
        original.delete()

        report = reverse("reports", args=[self.business.slug])
        view = self.client.get(report)
        self.assertContains(view, "Original category")
        self.assertContains(view, "Later category")
        self.assertNotContains(view, "Renamed category")
        self.assertEqual(view.context["paid_total"], first.total + second.total)
        filtered = self.client.get(report, {"category": original_id})
        self.assertEqual([row["category_label"] for row in filtered.context["category_performance"]], ["Original category"])
        self.assertEqual(first.lines.get().sku_snapshot, "Original SKU")

        Order.objects.filter(pk=first.pk).update(created_at=timezone.now() - timedelta(days=4))
        today = timezone.localdate(timezone=ZoneInfo(self.business.timezone_name))
        narrowed = self.client.get(report, {"start": today.isoformat(), "end": today.isoformat()})
        self.assertEqual([row["category_label"] for row in narrowed.context["category_performance"]], ["Later category"])

    def test_legacy_classification_is_unknown_and_uncategorized_is_preserved(self):
        legacy = self.order()
        self.tender(legacy)
        OrderLine.objects.filter(order=legacy).update(category_snapshot_name=None)
        recent = self.order()
        self.tender(recent)
        report = reverse("reports", args=[self.business.slug])
        response = self.client.get(report)
        self.assertEqual({row["category_label"] for row in response.context["category_performance"]},
                         {"Unknown historical category", "Uncategorized"})
        self.assertEqual(response.context["paid_total"], legacy.total + recent.total)


    def test_pos_and_guest_sales_keep_sale_time_categories_and_refund_cohort(self):
        old = self.business.categories.create(name="Guest category")
        new = self.business.categories.create(name="Register category")
        self.product.category = old
        self.product.save(update_fields=["category"])
        table = self.business.tables.create(location=self.location, name="Report table")
        token = signing.dumps([str(table.pk), str(table.qr_version)], salt="veyra-table-qr")
        cart_url = reverse("your_order", args=[token])
        guest = Client()
        guest.get(cart_url)
        self.assertEqual(guest.post(cart_url, {
            "action": "add", "product": self.product.pk, "quantity": "1", "revision": "0",
        }).status_code, 302)
        cart = guest.get(cart_url)
        result = guest.post(cart_url, {
            "action": "checkout", "revision": "1", "quote": cart.context["cart_quote"],
            "payment_method": "counter",
        })
        self.assertEqual(result.status_code, 302)
        guest_sale = self.business.orders.get()
        self.tender(guest_sale)
        reserve_refund(
            business=self.business, actor=self.owner, order_id=guest_sale.pk,
            amount=Decimal("5000"), reason="Fixture adjustment", idempotency_key=uuid4(),
        )

        self.product.category = new
        self.product.save(update_fields=["category"])
        self.assertEqual(self.client.post(reverse("point_of_sale", args=[self.business.slug]), {
            "location": self.location.pk, "order_type": "takeaway",
            "idempotency_key": uuid4(), f"quantity_{self.product.pk}": 1,
        }).status_code, 302)
        register_sale = self.business.orders.exclude(pk=guest_sale.pk).get()
        self.tender(register_sale, method="card")
        self.order()
        cancelled = self.order()
        self.advance(cancelled, Order.Status.CANCELLED)

        result = self.client.get(reverse("reports", args=[self.business.slug]), {"location": self.location.pk}).context
        self.assertEqual(result["paid_orders"], 2)
        self.assertEqual(result["paid_total"], Decimal("50000"))
        self.assertEqual(result["refunded_total"], Decimal("5000"))
        self.assertEqual(result["retained_total"], Decimal("45000"))
        self.assertEqual({row["category_label"]: row["revenue"] for row in result["category_performance"]},
                         {"Guest category": Decimal("25000"), "Register category": Decimal("25000")})
