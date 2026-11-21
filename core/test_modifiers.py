from uuid import uuid4

from django.core import signing
from django.test import Client, TestCase
from django.urls import reverse

from .models import DiningTable, Membership, Product, User
from .services import create_business, create_location, issue_station_pairing_code


class ProductModifierTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("modifier-owner@example.invalid")
        self.business = create_business(owner=self.owner, name="Modifier Shop", slug="modifier-shop")
        self.location = self.business.locations.get()
        self.product = Product.objects.create(business=self.business, name="Sandwich", price="20000")
        self.staff = Client()
        self.staff.force_login(self.owner)
        self.management = "/app/modifier-shop/catalog/modifiers/"
        table = DiningTable.objects.create(business=self.business, location=self.location, name="T1")
        token = signing.dumps([str(table.pk), str(table.qr_version)], salt="veyra-table-qr")
        self.cart = reverse("your_order", args=[token])
        self.pos = reverse("point_of_sale", args=[self.business.slug])
        self.fields = {
            "name": "Bread", "active": "on", "products": [str(self.product.pk)],
            "locations": [str(self.location.pk)], "min_selections": "1", "max_selections": "1",
            "sort_order": "0", "options-TOTAL_FORMS": "2", "options-INITIAL_FORMS": "0",
            "options-0-name": "Rye", "options-0-surcharge": "3500",
            "options-0-active": "on", "options-0-sort_order": "0",
            "options-1-name": "Sourdough", "options-1-surcharge": "5000",
            "options-1-active": "on", "options-1-sort_order": "1",
        }

    def configure(self):
        response = self.staff.post(self.management, self.fields)
        self.assertEqual(response.status_code, 302)
        editor = self.staff.get(response.url)
        options = editor.context["option_forms"].initial_forms
        return response.url, [str(option.instance.pk) for option in options]

    def test_required_options_cart_edit_and_historical_receipt(self):
        editor_url, (rye, sourdough) = self.configure()
        self.assertContains(self.client.get(self.cart.removesuffix("cart/")), "Rye")
        initial = {"action": "add", "revision": "0", "product": self.product.pk}
        self.assertEqual(self.client.post(self.cart, initial).status_code, 400)
        self.assertEqual(self.client.post(self.cart, {**initial, "modifiers": [rye, sourdough]}).status_code, 400)
        self.assertEqual(self.client.post(self.cart, {**initial, "modifiers": [rye]}).status_code, 302)
        self.assertEqual(self.client.post(self.cart, {**initial, "revision": "1", "modifiers": [sourdough]}).status_code, 302)
        page = self.client.get(self.cart)
        self.assertContains(page, "Rp 48500")
        lines = page.context["lines"]
        self.assertEqual(len(lines), 2)
        sourdough_line = next(line for line in lines if line["modifiers"][0]["name"] == "Sourdough")
        self.assertEqual(self.client.post(self.cart, {
            "action": "customize", "revision": "2", "line": sourdough_line["key"], "modifiers": [rye],
        }).status_code, 302)
        review = self.client.get(self.cart)
        self.assertContains(review, "Rp 47000")
        self.assertEqual(len(review.context["lines"]), 1)
        checkout = self.client.post(self.cart, {
            "action": "checkout", "revision": "3", "quote": review.context["cart_quote"],
        })
        self.assertEqual(checkout.status_code, 302)
        tracking = self.client.get(checkout.url)
        self.assertContains(tracking, "Rye")
        self.assertContains(tracking, "Rp 47000")
        order = tracking.context["order"]
        payment = reverse("record_payment", args=[self.business.slug, order.pk])
        self.staff.post(payment, {"method": "cash", "cash_received": "50000", "idempotency_key": uuid4()})

        edited = dict(self.fields, **{
            "options-INITIAL_FORMS": "2", "options-0-id": rye, "options-1-id": sourdough,
            "options-0-name": "New rye", "options-0-surcharge": "8000",
        })
        self.assertEqual(self.staff.post(editor_url, edited).status_code, 302)
        receipt = reverse("order_receipt", args=[self.business.slug, order.pk])
        self.assertContains(self.staff.get(receipt), "Rye")
        self.assertNotContains(self.staff.get(receipt), "New rye")
        self.assertEqual(self.staff.get(receipt, {"format": "json"}).json()["receipt"]["items"][0]["modifiers"],
                         [{"group": "Bread", "name": "Rye", "surcharge": "3500.00"}])

    def test_pos_enforces_location_and_server_price(self):
        _, (rye, _) = self.configure()
        branch = create_location(business=self.business, actor=self.owner, name="Other branch")
        payload = {"idempotency_key": uuid4(), "order_type": "takeaway", "location": self.location.pk,
                   f"quantity_{self.product.pk}": "2", "total": "1"}
        self.assertContains(self.staff.post(self.pos, payload), "Bread", status_code=400)
        selected = {**payload, f"modifiers_{self.product.pk}": [rye]}
        self.assertEqual(self.staff.post(self.pos, {**selected, "location": branch.pk}).status_code, 400)
        response = self.staff.post(self.pos, selected, follow=True)
        self.assertContains(response, "Rp 47000")

    def test_manager_permissions_and_foreign_product_validation(self):
        viewer = User.objects.create_user("catalog-viewer@example.invalid")
        Membership.objects.create(business=self.business, user=viewer, permissions=["catalog.view"])
        self.client.force_login(viewer)
        self.assertEqual(self.client.post(self.management, self.fields).status_code, 403)
        foreign_owner = User.objects.create_user("modifier-other@example.invalid")
        other = create_business(owner=foreign_owner, name="Other Shop", slug="modifier-other")
        foreign = Product.objects.create(business=other, name="Other sandwich", price="25000")
        response = self.staff.post(self.management, {**self.fields, "products": [str(foreign.pk)]})
        self.assertContains(response, "Select a valid choice", status_code=400)

    def test_open_cart_rejects_a_stale_modifier_quote(self):
        editor, (rye, sourdough) = self.configure()
        self.client.get(self.cart)
        self.client.post(self.cart, {
            "action": "add", "revision": "0", "product": self.product.pk, "modifiers": [rye],
        })
        before = self.client.get(self.cart)
        self.assertContains(before, "Rp 23500")
        changed = {**self.fields, "options-INITIAL_FORMS": "2",
                   "options-0-id": rye, "options-1-id": sourdough, "options-0-surcharge": "4200"}
        self.assertEqual(self.staff.post(editor, changed).status_code, 302)
        payload = {"action": "checkout", "revision": "1", "quote": before.context["cart_quote"]}
        stale = self.client.post(self.cart, payload)
        self.assertContains(stale, "Review the updated total", status_code=400)
        self.assertContains(stale, "Rp 24200", status_code=400)
        self.assertEqual(self.client.post(self.cart, {**payload, "quote": stale.context["cart_quote"]}).status_code, 302)

    def test_pos_supports_two_configurations_of_the_same_product(self):
        editor, (rye, sourdough) = self.configure()
        group_id = editor.rstrip("/").rsplit("/", 1)[1]
        key = str(self.product.pk)
        page = self.staff.post(self.pos, {
            "idempotency_key": uuid4(), "order_type": "takeaway", "location": self.location.pk,
            f"quantity_{key}": "1", f"modifiers_{key}_{group_id}": rye,
            f"quantity_{key}__1": "1", f"modifiers_{key}__1_{group_id}": sourdough,
        }, follow=True)
        self.assertContains(page, "Rp 48500")
        order = page.context["orders"][0]
        station = self.business.stations.create(location=self.location, name="Kitchen", kind="kitchen")
        queue = self.staff.get(reverse("station_screen", args=[self.business.slug, station.pk]))
        self.assertContains(queue, "Bread · Rye")
        self.assertContains(queue, "Bread · Sourdough")
        self.assertContains(queue, str(order.pk))

    def test_multiple_choice_groups_validate_limits_and_reject_other_products_options(self):
        self.fields.update(name="Toppings", min_selections="0", max_selections="2")
        _, (first, second) = self.configure()
        unrelated = Product.objects.create(business=self.business, name="Plain roll", price="9000")
        self.client.get(self.cart)
        form = {"action": "add", "revision": "0", "product": self.product.pk}
        self.assertEqual(self.client.post(self.cart, {**form, "product": unrelated.pk, "modifiers": [first]}).status_code, 400)
        self.assertEqual(self.client.post(self.cart, {**form, "modifiers": [first, first]}).status_code, 400)
        self.assertEqual(self.client.post(self.cart, {**form, "modifiers": [first, second]}).status_code, 302)
        self.assertContains(self.client.get(self.cart), "Rp 28500")

    def test_paired_station_reads_options_and_orders_with_the_same_rules(self):
        _, (rye, _) = self.configure()
        station = self.business.stations.create(
            location=self.location, name="Counter", kind="register",
            capabilities=["catalog.view", "orders.view", "orders.create"],
        )
        _, code = issue_station_pairing_code(station=station, actor=self.owner)
        pairing = self.client.post(reverse("station_pair"), {
            "code": code, "label": "Station test",
        }, content_type="application/json")
        self.assertEqual(pairing.status_code, 201)
        device = Client(headers={"Authorization": "Bearer " + pairing.json()["device_token"]})
        catalog = device.get(reverse("station_catalog", args=[station.pk])).json()["products"]
        self.assertEqual(catalog[0]["modifier_groups"][0]["options"][0],
                         {"id": int(rye), "name": "Rye", "surcharge": "3500.00"})
        address = reverse("station_create_order", args=[station.pk])
        order = {"idempotency_key": str(uuid4()), "items": [
            {"product_id": str(self.product.pk), "quantity": 1},
        ]}
        self.assertEqual(device.post(address, order, content_type="application/json").status_code, 400)
        order["items"][0]["modifiers"] = [int(rye)]
        placed = device.post(address, order, content_type="application/json")
        self.assertEqual(placed.status_code, 201)
        self.assertEqual(placed.json()["total"], "23500.00")
        response = device.get(reverse("station_orders", args=[station.pk]))
        self.assertEqual(response.json()["orders"][0]["items"][0]["modifiers"][0]["name"], "Rye")
