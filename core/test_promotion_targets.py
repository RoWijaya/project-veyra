
from uuid import uuid4

from django.test import Client, TestCase
from django.urls import reverse
from django.utils.html import escape

from .models import Category, Membership, Product, Promotion, User
from .services import create_business


class PromotionTargetsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("promotion-target-owner@example.invalid")
        cls.business = create_business(owner=cls.owner, name="Target Cafe", slug="target-cafe")
        cls.category = Category.objects.create(business=cls.business, name="Coffee & tea")
        cls.second_category = Category.objects.create(business=cls.business, name="Pastries")
        cls.product = Product.objects.create(business=cls.business, name="House Espresso", price=20000)
        cls.second_product = Product.objects.create(business=cls.business, name="Iced Latte", price=26000)
        cls.url = reverse("promotions", args=[cls.business.slug])
        cls.data = {
            "name": "Afternoon offer", "discount_type": Promotion.DiscountType.PERCENT,
            "value": "10", "priority": "0",
            "products": [str(cls.product.pk), str(cls.second_product.pk)],
            "categories": [str(cls.category.pk), str(cls.second_category.pk)],
        }

    def setUp(self):
        self.client.force_login(self.owner)

    def assert_option(self, response, item, *, selected=False):
        state = " selected" if selected else ""
        self.assertContains(
            response, f'<option value="{item.pk}"{state}>{escape(item.name)}</option>', html=True,
        )

    def test_new_and_edit_fields_render_names_and_restore_multiple_ids(self):
        initial = self.client.get(self.url)
        for item in (self.product, self.second_product, self.category, self.second_category):
            self.assert_option(initial, item)
        self.assertNotContains(initial, "Product object (")
        self.assertNotContains(initial, "Category object (")

        self.assertRedirects(self.client.post(self.url, self.data), self.url)
        promotion = self.business.promotions.get()
        edit_url = reverse("promotion_edit", args=[self.business.slug, promotion.pk])
        changed = self.client.get(edit_url)
        for item in (self.product, self.second_product, self.category, self.second_category):
            self.assert_option(changed, item, selected=True)

        invalid = self.client.post(edit_url, {**self.data, "value": "150"})
        self.assertContains(invalid, "Percentage discounts cannot exceed 100%.")
        self.assert_option(invalid, self.second_product, selected=True)
        self.assert_option(invalid, self.second_category, selected=True)

        self.assertRedirects(self.client.post(edit_url, {
            **self.data, "products": [str(self.second_product.pk)], "categories": [],
        }), self.url)
        reopened = self.client.get(edit_url)
        self.assert_option(reopened, self.product)
        self.assert_option(reopened, self.second_product, selected=True)
        self.assert_option(reopened, self.category)
        self.assert_option(reopened, self.second_category)

    def test_existing_inactive_product_stays_selected_until_removed(self):
        self.assertRedirects(self.client.post(self.url, self.data), self.url)
        Product.objects.filter(pk=self.product.pk).update(active=False)
        promotion = self.business.promotions.get()
        edit_url = reverse("promotion_edit", args=[self.business.slug, promotion.pk])
        self.assertNotContains(self.client.get(self.url), self.product.name)
        self.assert_option(self.client.get(edit_url), self.product, selected=True)
        self.assertRedirects(self.client.post(edit_url, self.data), self.url)
        self.assert_option(self.client.get(edit_url), self.product, selected=True)

    def test_manipulated_targets_permissions_and_csrf(self):
        other_user = User.objects.create_user("promotion-target-other@example.invalid")
        other = create_business(owner=other_user, name="Other Cafe", slug="other-target-cafe")
        foreign_product = Product.objects.create(business=other, name="Foreign coffee", price=5000)
        foreign_category = Category.objects.create(business=other, name="Foreign category")
        initial = self.client.get(self.url)
        self.assertNotContains(initial, foreign_product.name)
        self.assertNotContains(initial, foreign_category.name)

        for product, category in (
            (str(foreign_product.pk), str(foreign_category.pk)),
            (str(uuid4()), "not-a-category"),
            ("not-a-product", str(2 ** 31 - 1)),
        ):
            with self.subTest(product=product, category=category):
                invalid = self.client.post(self.url, {
                    **self.data, "products": [str(self.product.pk), product],
                    "categories": [str(self.category.pk), category],
                })
                self.assertContains(invalid, "Choose available products.")
                self.assertContains(invalid, "Choose available categories.")
                self.assert_option(invalid, self.product, selected=True)
                self.assert_option(invalid, self.category, selected=True)
        self.assertFalse(self.business.promotions.exists())

        Membership.objects.create(business=self.business, user=other_user, permissions=["catalog.view"])
        self.client.force_login(other_user)
        self.assertEqual(self.client.post(self.url, self.data).status_code, 403)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.owner)
        self.assertEqual(csrf.post(self.url, self.data).status_code, 403)
