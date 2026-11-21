import io

from django.core import signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from PIL import Image

from .models import Category, DiningTable, Location, Membership, Promotion, User
from .services import create_business


def photo(color="red", content_type="image/png"):
    stream = io.BytesIO()
    Image.new("RGB", (320, 180), color).save(stream, "PNG")
    return SimpleUploadedFile("sample.png", stream.getvalue(), content_type)


class CatalogRestoreTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("catalog-restore-owner@example.invalid")
        self.business = create_business(owner=self.owner, name="Cedar Café", slug="cedar-cafe")
        self.location = Location.objects.create(business=self.business, name="Central")
        self.table = DiningTable.objects.create(business=self.business, location=self.location, name="T1")
        self.token = signing.dumps([str(self.table.pk), str(self.table.qr_version)], salt="veyra-table-qr")
        self.menu = f"/order/table/{self.token}/"
        self.catalog = "/app/cedar-cafe/catalog/"
        self.categories = f"{self.catalog}categories/"
        self.settings = "/app/cedar-cafe/settings/ordering-page/"
        self.client.force_login(self.owner)
        self.guest = Client()

    def product_data(self, **overrides):
        return {"name": "Hazelnut cake", "price": "28000", "active": "on", **overrides}

    def test_create_a_categorized_product_and_display_it_on_the_signed_menu(self):
        pastry = Category.objects.create(business=self.business, name="Pastry")
        response = self.client.post(self.catalog, self.product_data(category=pastry.pk))
        self.assertRedirects(response, self.catalog)
        self.assertContains(self.guest.get(self.menu), "Hazelnut cake")
        self.assertContains(self.guest.get(self.menu), f'data-menu-category="{pastry.pk}"')

    def test_manage_search_order_and_safely_remove_categories(self):
        def create(name, sort):
            response = self.client.post(self.categories, {
                "name": name, "description": "For the menu", "sort_order": sort, "active": "on",
            })
            self.assertRedirects(response, self.categories)
            return Category.objects.get(business=self.business, name=name)
        tea = create("Tea", 5)
        pastry = create("Pastry", 10)
        self.client.post(self.catalog, self.product_data(category=pastry.pk))
        self.client.post(self.catalog, self.product_data(name="Jasmine tea", category=tea.pk))
        self.assertContains(self.client.get(f"{self.categories}?q=pastry"), "For the menu")
        menu = self.guest.get(self.menu).content.decode()
        self.assertLess(menu.index(f'data-menu-category="{tea.pk}"'), menu.index(f'data-menu-category="{pastry.pk}"'))
        edit = f"{self.categories}{pastry.pk}/"
        denied = self.client.post(edit, {"action": "delete"})
        self.assertContains(denied, "Move its products", status_code=400)
        self.assertRedirects(self.client.post(edit, {
            "name": "Bakery", "description": "House baked", "sort_order": 5,
        }), self.categories)
        self.assertContains(self.guest.get(self.menu), "Hazelnut cake")
        self.assertNotContains(self.guest.get(self.menu), f'data-menu-category="{pastry.pk}"')
        product = self.business.products.get(name="Hazelnut cake")
        self.assertRedirects(self.client.post(f"{self.catalog}{product.pk}/edit/", self.product_data(category=tea.pk)), self.catalog)
        self.assertRedirects(self.client.post(edit, {"action": "delete"}), self.categories)
        self.assertNotContains(self.client.get(self.categories), "House baked")
        self.assertContains(self.guest.get(self.menu), f'data-category="{tea.pk}"')

    def test_reject_category_ids_and_writes_outside_catalog_permission(self):
        other_owner = User.objects.create_user("catalog-other@example.invalid")
        other = create_business(owner=other_owner, name="Other shop", slug="other-shop")
        foreign = Category.objects.create(business=other, name="Private category")
        response = self.client.post(self.catalog, self.product_data(category=foreign.pk))
        self.assertContains(response, "Select a valid choice", status_code=400)
        self.assertEqual(self.client.post(f"{self.categories}{foreign.pk}/", {"action": "delete"}).status_code, 404)
        self.assertNotContains(self.client.get(self.categories), "Private category")
        Membership.objects.create(business=self.business, user=other_owner, permissions=["catalog.view"])
        self.client.force_login(other_owner)
        self.assertEqual(self.client.post(self.categories, {"name": "Denied", "sort_order": 0}).status_code, 403)
        self.assertEqual(self.client.post(self.catalog, self.product_data(image=photo())).status_code, 403)

    def test_product_editor_uploads_replaces_retains_and_removes_the_guest_image(self):
        self.assertRedirects(self.client.post(self.catalog, self.product_data(
            description="Roasted hazelnuts and chocolate", image=photo(), image_alt="A slice of cake",
        )), self.catalog)
        product = self.business.products.get(name="Hazelnut cake")
        edit = f"{self.catalog}{product.pk}/edit/"
        guest_image = f"{self.menu}products/{product.pk}/image/"
        guest_menu = self.guest.get(self.menu)
        self.assertContains(guest_menu, "Roasted hazelnuts and chocolate")
        self.assertContains(guest_menu, guest_image)
        self.assertContains(guest_menu, 'alt="A slice of cake"')
        original = self.guest.get(guest_image)
        self.assertEqual(original["Content-Type"], "image/webp")
        self.assertContains(self.client.get(edit), "Current image")
        self.assertContains(self.client.get("/app/cedar-cafe/pos/"), f"/catalog/{product.pk}/image/")
        self.assertRedirects(self.client.post(edit, self.product_data(name="House hazelnut cake")), self.catalog)
        self.assertEqual(self.guest.get(guest_image).content, original.content)
        self.assertRedirects(self.client.post(edit, self.product_data(image=photo("blue"), image_alt="Cake with icing")), self.catalog)
        self.assertNotEqual(self.guest.get(guest_image).content, original.content)
        self.assertRedirects(self.client.post(edit, self.product_data(remove_image="on")), self.catalog)
        self.assertEqual(self.guest.get(guest_image).status_code, 404)
        self.assertNotContains(self.guest.get(self.menu), guest_image)
        self.assertContains(self.guest.get(self.menu), "Hazelnut cake")

    def test_reject_invalid_and_oversized_images_without_creating_products(self):
        invalid = SimpleUploadedFile("fake.png", b"<svg>invalid</svg>", "image/png")
        too_large = SimpleUploadedFile("large.png", b"0" * (5 * 1024 * 1024 + 1), "image/png")
        for upload, message in ((invalid, "The image could not be read"), (too_large, "up to 5 MB")):
            with self.subTest(upload=upload.name):
                response = self.client.post(self.catalog, self.product_data(image=upload))
                self.assertContains(response, message, status_code=400)
        self.assertNotContains(self.guest.get(self.menu), "Hazelnut cake")

    def test_product_and_image_routes_reject_another_tenants_product(self):
        other_owner = User.objects.create_user("product-foreign@example.invalid")
        other = create_business(owner=other_owner, name="West café", slug="west-cafe")
        other_category = Category.objects.create(business=other, name="Private category")
        self.client.force_login(other_owner)
        self.client.post("/app/west-cafe/catalog/", self.product_data(category=other_category.pk, image=photo()))
        product = other.products.get()
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(f"{self.catalog}{product.pk}/edit/", self.product_data(image=photo())).status_code, 404)
        self.assertEqual(self.guest.get(f"{self.menu}products/{product.pk}/image/").status_code, 404)

    def test_ordering_page_banner_and_branding_persist_and_can_be_replaced_or_removed(self):
        upload = {"display_name": "Cedar Kitchen", "introduction": "Baked each morning", "banner_alt": "Our bakery"}
        self.assertContains(self.client.get("/app/cedar-cafe/settings/"), "Ordering page")
        self.assertContains(self.client.get(self.settings), "Preview guest menu")
        self.assertRedirects(self.client.post(self.settings, {**upload, "image": photo()}), self.settings)
        banner_url = f"{self.menu}ordering-banner/"
        self.assertContains(self.guest.get(self.menu), "Cedar Kitchen")
        self.assertContains(self.guest.get(self.menu), "Baked each morning")
        self.assertContains(self.guest.get(self.menu), banner_url)
        original = self.guest.get(banner_url)
        self.assertEqual(original["Content-Type"], "image/webp")
        self.assertIn("no-store", original["Cache-Control"])
        self.assertContains(self.client.get(self.settings), "Current banner")
        self.assertRedirects(self.client.post(self.settings, {**upload, "image": photo("blue")}), self.settings)
        self.assertNotEqual(self.guest.get(banner_url).content, original.content)
        self.assertRedirects(self.client.post(self.settings, {**upload, "remove_image": "on"}), self.settings)
        self.assertEqual(self.guest.get(banner_url).status_code, 404)
        guest_menu = self.guest.get(self.menu)
        self.assertNotContains(guest_menu, banner_url)
        self.assertContains(guest_menu, "Cedar Kitchen")

    def test_ordering_page_blocks_foreign_tenants_catalog_staff_and_invalid_media(self):
        foreign_owner = User.objects.create_user("ordering-foreign@example.invalid")
        foreign = create_business(owner=foreign_owner, name="Far café", slug="far-cafe")
        self.assertEqual(self.client.post("/app/far-cafe/settings/ordering-page/", {"display_name": "Forged"}).status_code, 404)
        self.assertContains(self.client.post(self.settings, {
            "display_name": "Spoofed", "image": SimpleUploadedFile("fake.webp", b"<svg/>", "image/webp"),
        }), "The image could not be read", status_code=400)
        self.assertNotContains(self.guest.get(self.menu), "Spoofed")
        self.assertRedirects(self.client.post(self.settings, {
            "display_name": "Our storefront", "image": photo(),
        }), self.settings)
        foreign_location = Location.objects.create(business=foreign, name="Other location")
        foreign_table = DiningTable.objects.create(business=foreign, location=foreign_location, name="Other table")
        foreign_token = signing.dumps([str(foreign_table.pk), str(foreign_table.qr_version)], salt="veyra-table-qr")
        self.assertNotContains(self.guest.get(f"/order/table/{foreign_token}/"), "Our storefront")
        self.assertEqual(self.guest.get(f"/order/table/{foreign_token}/ordering-banner/").status_code, 404)
        staff = User.objects.create_user("ordering-staff@example.invalid")
        Membership.objects.create(business=self.business, user=staff, permissions=["catalog.view", "catalog.manage"])
        self.client.force_login(staff)
        self.assertEqual(self.client.get(self.settings).status_code, 403)
        self.assertEqual(self.client.post(self.settings, {"display_name": "Forbidden", "remove_image": "on"}).status_code, 403)
        self.assertEqual(self.client.get(f"{self.settings}banner/").status_code, 403)
        self.assertEqual(self.guest.get(f"{self.menu}ordering-banner/").status_code, 200)

    def test_category_in_use_by_a_promotion_cannot_be_deleted(self):
        pastry = Category.objects.create(business=self.business, name="Pastry")
        promotion = Promotion.objects.create(
            business=self.business, name="Pastry special", discount_type=Promotion.DiscountType.PERCENT, value=20,
        )
        promotion.categories.add(pastry)
        response = self.client.post(f"{self.categories}{pastry.pk}/", {"action": "delete"})
        self.assertContains(response, "Remove this category from its promotions", status_code=400)
        self.assertContains(self.client.get(self.categories), "Pastry")

    def test_ordering_page_and_category_mutations_require_csrf(self):
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.owner)
        self.assertEqual(csrf.post(self.settings, {"display_name": "Bad request"}).status_code, 403)
        self.assertEqual(csrf.post(self.categories, {"name": "Bad request", "sort_order": 0}).status_code, 403)
