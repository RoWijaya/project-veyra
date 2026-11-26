import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.core import signing
from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse

from .models import (
    Category,
    DiningTable,
    Location,
    Membership,
    ModifierGroup,
    ModifierOption,
    OrderingPage,
    Product,
    User,
)
from .services import create_business
from .storefront import pickup_token
from .test_catalog_restore import photo


class OrderingBuilderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("builder-owner@example.invalid")
        cls.business = create_business(owner=cls.owner, name="Cedar Cafe", slug="cedar-builder")
        cls.location = Location.objects.create(business=cls.business, name="Main counter")
        table = DiningTable.objects.create(
            business=cls.business, location=cls.location, name="Table 1"
        )
        token = signing.dumps([str(table.pk), str(table.qr_version)], salt="veyra-table-qr")
        cls.menu = reverse("table_order", args=[token])

    def setUp(self):
        self.client.force_login(self.owner)
        self.guest = Client()
        self.editor = reverse("ordering_page_settings", args=[self.business.slug])

    def test_draft_is_private_until_explicit_publication(self):
        self.assertRedirects(
            self.client.post(
                self.editor, {"revision": 0, "action": "save", "display_name": "Draft cafe"}
            ),
            self.editor,
        )
        self.assertNotContains(self.guest.get(self.menu), "Draft cafe")
        self.assertContains(self.client.get(self.editor), "Draft cafe")
        self.assertContains(self.client.get(f"{self.editor}preview/"), "Draft cafe")
        self.assertRedirects(
            self.client.post(self.editor, {"revision": 1, "action": "publish"}), self.editor
        )
        self.assertContains(self.guest.get(self.menu), "Draft cafe")

    def change(self, action, **fields):
        revision = self.client.get(self.editor).context["form"]["revision"].value()
        return self.client.post(self.editor, {"action": action, "revision": revision, **fields})

    def save_and_publish(self, **fields):
        self.assertRedirects(self.change("save", **fields), self.editor)
        self.assertRedirects(self.change("publish"), self.editor)

    def test_preview_post_validates_without_persisting_or_changing_the_cart(self):
        product = Product.objects.create(business=self.business, name="House coffee", price=18000)
        preview_url = f"{self.editor}preview/"
        for key, value in (
            ("layout", "sidebar"),
            (
                "background",
                "#",
            ),
            ("focal_x", 105),
            ("website", "ftp://example.invalid"),
            ("phone", "123456"),
        ):
            response = self.client.post(preview_url, {"revision": 0, key: value})
            self.assertContains(response, "Preview uses the saved draft", status_code=400)
        preview = self.client.post(
            preview_url,
            {
                "revision": 0,
                "display_name": "Preview only",
                "preview_request": "42",
                "layout": "editorial",
                "logo": photo(),
                "show_images": True,
                "show_descriptions": True,
            },
        )
        self.assertContains(preview, "Preview only")
        self.assertContains(preview, 'data-preview-request="42"')
        self.assertContains(preview, product.name)
        self.assertContains(preview, 'data-menu-layout="editorial"')
        self.assertContains(preview, "data:image/webp;base64,")
        self.assertNotContains(preview, "data-cart-add")
        self.assertEqual(preview["X-Frame-Options"], "SAMEORIGIN")
        self.assertIn("frame-ancestors 'self'", preview["Content-Security-Policy"])
        self.assertNotContains(self.client.get(self.editor), "Preview only")
        self.assertNotContains(self.guest.get(self.menu), "Preview only")

    def test_palette_is_shared_across_preview_table_takeaway_and_restored_publication(self):
        self.location.takeaway_version = uuid.uuid4()
        self.location.save(update_fields=["takeaway_version"])
        pickup = reverse("pickup_menu", args=[pickup_token(self.location)])
        palette = {
            "accent": "#105140",
            "background": "#e5effa",
            "text": "#133454",
            "secondary": "#405770",
        }
        preview_url = f"{self.editor}preview/"
        preview = self.client.post(preview_url, {"revision": 0, **palette})
        for name, color in palette.items():
            self.assertContains(preview, f"--store-{name}: {color.upper()};")
        self.assertNotContains(self.guest.get(self.menu), "#405770")
        self.save_and_publish(**palette)
        preview = self.client.get(preview_url)
        self.assertIn("no-store", preview["Cache-Control"])
        for route in (self.menu, pickup):
            page = self.guest.get(route)
            self.assertIn("no-store", page["Cache-Control"])
            self.assertNotContains(page, "<style nonce=")
            stylesheet = self.theme(page)
            self.assertIn("max-age", stylesheet["Cache-Control"])
            for name, color in palette.items():
                value = f"--store-{name}: {color.upper()};"
                self.assertContains(preview, value)
                self.assertContains(stylesheet, value)
        fragment = self.guest.get(self.menu, HTTP_X_STOREFRONT_REFRESH="1")
        self.assertContains(fragment, 'id="store-design-source"')
        self.assertContains(fragment, "--store-secondary: #405770;")
        self.save_and_publish(accent="#592E46")
        self.assertRedirects(self.change("restore", version=1), self.editor)
        self.assertContains(self.client.get(preview_url), "--store-accent: #105140;")
        self.assertContains(self.theme(self.guest.get(pickup)), "--store-accent: #592E46;")
        self.assertRedirects(self.change("publish"), self.editor)
        self.assertContains(self.theme(self.guest.get(pickup)), "--store-accent: #105140;")
        self.assertContains(self.client.get(self.editor), 'for="id_preview_location"')

        other_owner = User.objects.create_user("other-theme-owner@example.invalid")
        other = create_business(owner=other_owner, name="Different theme", slug="different-theme")
        other_location = other.locations.get()
        other_location.takeaway_version = uuid.uuid4()
        other_location.save(update_fields=["takeaway_version"])
        other_menu = reverse("pickup_menu", args=[pickup_token(other_location)])
        other_editor = reverse("ordering_page_settings", args=[other.slug])
        other_client = Client()
        other_client.force_login(other_owner)
        self.assertEqual(other_client.post(other_editor, {
            "revision": 0, "action": "save", "accent": "#592E46",
        }).status_code, 302)
        self.assertEqual(other_client.post(other_editor, {"revision": 1, "action": "publish"}).status_code, 302)
        self.assertEqual(self.client.post(other_editor, {
            "revision": 2, "action": "restore", "version": 1,
        }).status_code, 404)
        self.assertEqual(self.client.get(f"{other_editor}preview/").status_code, 404)
        self.assertContains(self.theme(self.guest.get(other_menu)), "--store-accent: #592E46;")
        self.assertContains(self.theme(self.guest.get(pickup)), "--store-accent: #105140;")

    def theme(self, page):
        url = page.context["ordering_theme_url"]
        self.assertContains(page, f'href="{url}"')
        response = self.guest.get(url)
        self.assertEqual(response["Content-Type"], "text/css; charset=utf-8")
        return response

    def test_public_reads_activated_snapshot_even_when_legacy_projection_differs(self):
        self.save_and_publish(
            display_name="Published cafe", accent="#185677", logo=photo("red"), image=photo("blue"),
            layout="editorial", tagline="Published welcome",
        )
        original = self.guest.get(self.menu)
        old_stylesheet = original.context["ordering_theme_url"]
        logo_path = self.menu + "ordering-logo/"
        cover_path = self.menu + "ordering-banner/"
        original_logo = self.guest.get(logo_path).content
        original_cover = self.guest.get(cover_path).content
        OrderingPage.objects.filter(business=self.business).update(
            appearance={}, display_name="Outdated copy", logo_content=None, banner_content=None,
        )
        self.assertRedirects(
            self.change("save", display_name="Private draft", accent="#592E46"), self.editor
        )
        self.assertContains(self.client.get(f"{self.editor}preview/"), "Private draft")
        live = Client().get(self.menu)
        self.assertContains(live, "Published cafe")
        self.assertContains(live, "Published welcome")
        self.assertContains(live, 'data-menu-layout="editorial"')
        self.assertNotContains(live, "Outdated copy")
        self.assertNotContains(live, "Private draft")
        self.assertEqual(live.context["ordering_theme_url"], old_stylesheet)
        self.assertContains(self.theme(live), "--store-accent: #185677;")
        self.assertEqual(self.guest.get(logo_path).content, original_logo)
        self.assertEqual(self.guest.get(cover_path).content, original_cover)

        self.assertRedirects(self.change("publish"), self.editor)
        latest = Client().get(self.menu)
        self.assertContains(latest, "Private draft")
        self.assertNotEqual(latest.context["ordering_theme_url"], old_stylesheet)
        self.assertContains(self.theme(latest), "--store-accent: #592E46;")
        stale = self.guest.get(old_stylesheet)
        self.assertIn("no-store", stale["Cache-Control"])
        self.assertContains(stale, "--store-accent: #592E46;")
        self.assertRedirects(self.change("unpublish"), self.editor)
        public = Client().get(self.menu)
        self.assertNotContains(public, "Private draft")
        self.assertContains(self.theme(public), "--store-accent: #392A22;")
        for path in (logo_path, cover_path):
            self.assertEqual(self.guest.get(path).status_code, 404)

    def test_failed_save_keeps_values_and_reports_that_upload_needs_reselection(self):
        self.save_and_publish(display_name="Live cafe")
        self.assertRedirects(self.change("save", display_name="Saved draft"), self.editor)
        invalid = self.change("save", display_name="Unsaved edit", background="#FFFFFF",
                              secondary="#FFFFFF", logo=photo("blue"))
        self.assertContains(invalid, "Unsaved changes", status_code=400)
        self.assertContains(invalid, "Unsaved edit", status_code=400)
        self.assertContains(invalid, "Choose this image again", status_code=400)
        self.assertNotContains(invalid, "Publish draft", status_code=400)
        self.assertContains(self.client.get(f"{self.editor}preview/"), "Saved draft")
        self.assertContains(self.guest.get(self.menu), "Live cafe")

    def test_replacing_and_removing_both_images_preserves_live_media_and_snapshot(self):
        logo = f"{self.editor}logo/"
        cover = f"{self.editor}banner/"
        routes = ("ordering-logo/", "ordering-banner/")
        self.save_and_publish(logo=photo("green"), image=photo("black"))
        original = [self.guest.get(self.menu + path).content for path in routes]
        self.assertRedirects(self.change("save", logo=photo("red"), image=photo("blue")), self.editor)
        replacement = [self.client.get(logo).content, self.client.get(cover).content]
        self.assertNotEqual(replacement[0], original[0])
        self.assertNotEqual(replacement[1], original[1])
        self.assertEqual([self.guest.get(self.menu + path).content for path in routes], original)
        self.assertRedirects(self.change("publish"), self.editor)
        invalid_preview = self.client.post(f"{self.editor}preview/", {
            "revision": 4, "secondary": "#FFFFFF", "background": "#FFFFFF",
        })
        self.assertContains(invalid_preview, "Secondary text.", status_code=400)
        self.assertRedirects(self.change("save", remove_logo="on", remove_image="on"), self.editor)
        self.assertRedirects(self.change("publish"), self.editor)
        for path in routes:
            self.assertEqual(self.guest.get(self.menu + path).status_code, 404)
        self.assertRedirects(self.change("restore", version=2), self.editor)
        self.assertEqual([self.client.get(logo).content, self.client.get(cover).content], replacement)

    def test_publish_unpublish_and_restore_preserve_the_qr_and_both_assets(self):
        logo_url, banner_url = self.menu + "ordering-logo/", self.menu + "ordering-banner/"
        self.save_and_publish(
            display_name="First published", logo=photo("blue"), image=photo("red")
        )
        logo, banner = self.guest.get(logo_url).content, self.guest.get(banner_url).content
        self.assertRedirects(
            self.change("save", display_name="Second published", remove_logo="on"), self.editor
        )
        self.assertEqual(self.guest.get(logo_url).content, logo)
        self.assertEqual(self.client.get(f"{self.editor}logo/").status_code, 404)
        self.assertRedirects(self.change("publish"), self.editor)
        self.assertContains(self.guest.get(self.menu), "Second published")
        self.assertEqual(self.guest.get(logo_url).status_code, 404)
        self.assertEqual(self.guest.get(banner_url).content, banner)
        self.assertRedirects(self.change("unpublish"), self.editor)
        self.assertContains(self.guest.get(self.menu), self.business.name)
        self.assertNotContains(self.guest.get(self.menu), "Second published")
        self.assertEqual(self.guest.get(banner_url).status_code, 404)
        self.assertRedirects(self.change("restore", version=1), self.editor)
        self.assertContains(self.client.get(f"{self.editor}preview/"), "First published")
        self.assertNotContains(self.guest.get(self.menu), "First published")
        self.assertRedirects(self.change("publish"), self.editor)
        self.assertEqual(self.guest.get(logo_url).content, logo)
        self.assertEqual(self.guest.get(banner_url).content, banner)

    def test_editor_preview_and_mutations_follow_business_permissions(self):
        staff = User.objects.create_user("builder-staff@example.invalid")
        membership = Membership.objects.create(
            business=self.business, user=staff, permissions=["ordering_page.view"]
        )
        another_owner = User.objects.create_user("builder-other@example.invalid")
        another_business = create_business(
            owner=another_owner, name="Another bakery", slug="another-builder"
        )
        foreign_editor = reverse("ordering_page_settings", args=[another_business.slug])
        self.assertRedirects(
            self.change("save", display_name="Private draft", logo=photo()), self.editor
        )
        self.assertEqual(self.guest.get(f"{self.editor}preview/").status_code, 302)
        self.assertEqual(self.guest.get(f"{self.editor}logo/").status_code, 302)
        self.client.force_login(staff)
        self.assertContains(self.client.get(f"{self.editor}preview/"), "Private draft")
        self.assertEqual(
            self.client.post(f"{self.editor}preview/", {"revision": 1}).status_code, 403
        )
        self.assertEqual(self.change("save", display_name="Read only").status_code, 403)
        membership.permissions = ["ordering_page.edit"]
        membership.save(update_fields=["permissions"])
        self.assertRedirects(self.change("save", display_name="Staff draft"), self.editor)
        for action in ("publish", "unpublish", "restore"):
            self.assertEqual(self.change(action, version=0).status_code, 403)
        for suffix in ("", "preview/", "logo/", "banner/"):
            self.assertEqual(self.client.get(f"{foreign_editor}{suffix}").status_code, 404)
        self.assertEqual(
            self.client.post(foreign_editor, {"revision": 0, "action": "publish"}).status_code, 404
        )
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.owner)
        self.assertEqual(csrf.post(f"{self.editor}preview/", {"revision": 2}).status_code, 403)

    def test_invalid_contrast_and_foreign_references_stop_draft_save(self):
        other = create_business(
            owner=User.objects.create_user("foreign-builder@example.invalid"),
            name="Other bakery",
            slug="foreign-builder",
        )
        category = Category.objects.create(business=other, name="Other pastries")
        foreign_product = Product.objects.create(
            business=other, category=category, name="Foreign pie", price=100
        )
        for fields in (
            {"accent": "#FFFFFF", "background": "#FFFFFF"},
            {"secondary": "not a six-digit color"},
            {"category_order": str(category.pk)},
            {"featured_products": str(foreign_product.pk)},
            {"product_order": "not-an-id"},
            {"website": "https://user:pass@example.invalid/"},
        ):
            response = self.change("save", display_name="Invalid draft", **fields)
            self.assertContains(response, "Check the highlighted settings.", status_code=400)
            self.assertNotContains(self.guest.get(self.menu), "Invalid draft")
        self.assertNotContains(self.client.get(f"{self.editor}preview/"), "Invalid draft")

    def test_published_layout_and_visibility_preserve_authoritative_checkout(self):
        pastry = Category.objects.create(business=self.business, name="Pastry")
        coffee = Category.objects.create(business=self.business, name="Coffee")
        croissant = Product.objects.create(
            business=self.business, category=pastry, name="Butter croissant", price=18000
        )
        espresso = Product.objects.create(
            business=self.business, category=coffee, name="House espresso", price=20000
        )
        hidden = Product.objects.create(
            business=self.business, category=coffee, name="Secret coffee", price=7000
        )
        Product.objects.create(
            business=self.business, name="Inactive item", price=400, active=False
        )
        self.location.takeaway_version = uuid.uuid4()
        self.location.save(update_fields=["takeaway_version"])
        takeaway = reverse("pickup_menu", args=[pickup_token(self.location)])
        self.save_and_publish(
            layout="compact",
            product_order=f"{croissant.pk},{espresso.pk}",
            category_order=f"{pastry.pk},{coffee.pk}",
            hidden_products=str(hidden.pk),
            featured_products=str(espresso.pk),
        )
        live = self.guest.get(self.menu)
        self.assertContains(live, 'data-menu-layout="compact"')
        self.assertNotContains(live, '<style nonce=')
        self.assertContains(live, '<link rel="stylesheet" id="store-design"')
        fragment = self.guest.get(self.menu, HTTP_X_STOREFRONT_REFRESH="1")
        self.assertContains(fragment, '<template id="store-design-source">')
        self.assertNotContains(fragment, '<style nonce=')
        self.assertNotIn("'nonce-", fragment["Content-Security-Policy"])
        self.assertContains(live, "Featured")
        self.assertLess(
            live.content.index(b"House espresso"), live.content.index(b"Butter croissant")
        )
        self.assertNotContains(live, "Secret coffee")
        self.assertNotContains(live, "Inactive item")
        self.assertContains(self.guest.get(takeaway), 'data-menu-layout="compact"')
        legacy_menu = self.guest.get(self.menu + "menu.json")
        self.assertNotIn(str(hidden.pk), [item["id"] for item in legacy_menu.json()["products"]])
        legacy_post = self.guest.post(self.menu, {f"quantity_{hidden.pk}": 1, "tracking_token": uuid.uuid4().hex})
        self.assertContains(legacy_post, 'data-menu-layout="compact"')
        self.assertContains(legacy_post, "We could not place that order.")
        self.assertNotContains(legacy_post, "Secret coffee")
        cart_url = self.menu + "cart/"
        self.assertEqual(
            self.guest.post(
                cart_url,
                {"action": "add", "revision": 0, "product": str(hidden.pk)},
                HTTP_ACCEPT="application/json",
            ).status_code,
            400,
        )
        added = self.guest.post(
            cart_url,
            {
                "action": "add",
                "revision": 0,
                "product": str(espresso.pk),
                "quantity": 1,
                "price": "1",
            },
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(added.json(), {"count": 1, "revision": 1})
        cart = self.guest.get(cart_url)
        self.assertContains(cart, "Rp 20000")
        checkout = self.guest.post(
            cart_url,
            {
                "revision": 1,
                "action": "checkout",
                "order_type": "dine_in",
                "payment_method": "counter",
                "quote": cart.context["cart_quote"],
            },
        )
        self.assertEqual(checkout.status_code, 302)
        self.assertContains(self.guest.get(checkout.url), "House espresso")

    def test_preview_preserves_location_scoped_modifiers_and_can_render_without_public_link(self):
        self.location.active = False
        self.location.save(update_fields=["active"])
        front = Location.objects.create(business=self.business, name="Front counter")
        back = Location.objects.create(business=self.business, name="Back counter")
        product = Product.objects.create(business=self.business, name="Tea", price=9000)
        group = ModifierGroup.objects.create(business=self.business, name="Pickup extras")
        group.products.add(product)
        group.locations.add(back)
        ModifierOption.objects.create(group=group, name="Extra honey", surcharge=2000)
        preview = self.editor + "preview/"
        self.assertContains(self.client.get(self.editor), "You can preview the design now.")
        self.assertNotContains(self.client.get(preview, {"location": front.pk}), "Extra honey")
        self.assertContains(self.client.get(preview, {"location": back.pk}), "Extra honey")

    def test_private_restaurant_details_only_appear_after_opt_in(self):
        self.save_and_publish(
            address="12 Coffee Lane",
            opening_note="Weekdays 8–4",
            phone="+62 555 3210",
            website="https://coffee.example.invalid",
        )
        live = self.guest.get(self.menu)
        self.assertNotContains(live, "12 Coffee Lane")
        self.assertNotContains(live, "coffee.example.invalid")
        self.save_and_publish(
            show_information=True,
            address="12 Coffee Lane",
            opening_note="Weekdays 8–4",
            phone="+62 555 3210",
            website="https://coffee.example.invalid",
        )
        self.assertContains(self.guest.get(self.menu), "12 Coffee Lane")
        self.assertContains(self.guest.get(self.menu), 'href="tel:+625553210"')
        self.assertContains(self.guest.get(self.menu), 'href="https://coffee.example.invalid"')


class OrderingBuilderConcurrencyTests(TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_saves_accept_one_revision(self):
        owner = User.objects.create_user("builder-concurrent@example.invalid")
        business = create_business(owner=owner, name="Concurrent Cafe", slug="concurrent-builder")
        editor = reverse("ordering_page_settings", args=[business.slug])
        clients = [Client(), Client()]
        for client in clients:
            client.force_login(owner)
        barrier = Barrier(2)

        def save(client, name):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                response = client.post(
                    editor, {"revision": 0, "action": "save", "display_name": name}
                )
                return response.status_code, name
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            jobs = [
                executor.submit(save, client, f"Draft {index}")
                for index, client in enumerate(clients)
            ]
            results = [job.result(timeout=20) for job in jobs]
        self.assertEqual(sorted(status for status, _ in results), [302, 409])
        winner = next(name for status, name in results if status == 302)
        self.assertContains(clients[0].get(editor + "preview/"), winner)
