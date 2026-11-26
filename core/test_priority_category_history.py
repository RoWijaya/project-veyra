from datetime import timedelta
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Category, Order, OrderLine
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
