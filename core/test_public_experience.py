from cryptography.fernet import Fernet
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import Membership, PaymentConnection, Printer, Product, User
from .services import create_business


class AccountRecoveryPresentationTests(TestCase):
    def test_unknown_invitation_offers_a_safe_recovery_path(self):
        response = self.client.get(reverse("accept_invitation", args=["unknown-business", "invalid"]))
        self.assertContains(response, "Ask the business owner to send a new invitation.", status_code=404)
        self.assertContains(response, 'href="/login/"', status_code=404)
        self.assertIn("no-store", response["Cache-Control"])

    def test_home_links_to_the_product_and_its_next_workflow(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, 'href="#product"')
        self.assertContains(response, 'id="product"')
        self.assertContains(response, 'href="#operations"')
        self.assertContains(response, 'id="operations"')
        self.assertContains(response, 'href="/register/"')
        self.assertContains(response, 'href="#preparation">Prepare and hand off')
        self.assertContains(response, 'id="preparation" data-story-chapter')
        self.assertContains(response, 'href="#inventory-tour">Track stock')
        self.assertContains(response, 'id="inventory-tour" data-story-chapter')


class SharedComponentPresentationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("component-owner@example.invalid")
        cls.business = create_business(owner=cls.owner, name="Component Café", slug="component-cafe")

    def test_management_choices_have_names_and_submit_as_checkbox_values(self):
        self.client.force_login(self.owner)
        Printer.objects.create(business=self.business, name="Bar counter printer")
        Product.objects.create(business=self.business, name="Iced latte", price=24000)
        team = self.client.get(reverse("team", args=[self.business.slug]))
        self.assertContains(team, 'type="checkbox" name="permissions" value="catalog.view"')
        station = self.client.get(reverse("stations", args=[self.business.slug]))
        self.assertContains(station, 'type="checkbox" name="capabilities" value="orders.view"')
        self.assertContains(station, "Bar counter printer")
        self.assertContains(station, 'for="id_printer_name"')
        self.assertContains(station, 'id="id_name"', count=1)
        self.assertContains(station, 'id="id_location"', count=1)
        self.assertNotContains(station, "Printer object")
        recipes = self.client.get(reverse("recipes", args=[self.business.slug]))
        self.assertContains(recipes, "Iced latte")
        self.assertNotContains(recipes, "Product object")

    @override_settings(DEBUG=False, ALLOWED_HOSTS=["testserver"])
    def test_blocked_and_missing_pages_offer_safe_recovery(self):
        self.assertContains(self.client.get("/page-that-does-not-exist/"), "Page not found", status_code=404)
        staff = User.objects.create_user("component-collaborator@example.invalid")
        Membership.objects.create(business=self.business, user=staff, permissions=["catalog.view"])
        self.client.force_login(staff)
        response = self.client.post(reverse("categories", args=[self.business.slug]), {"name": "Denied"})
        self.assertContains(response, "Access restricted", status_code=403)
        self.assertContains(response, 'href="/app/"', status_code=403)
        browser = Client(enforce_csrf_checks=True)
        browser.force_login(self.owner)
        response = browser.post(reverse("categories", args=[self.business.slug]), {"name": "Missing token"})
        self.assertContains(response, "This form could not be submitted", status_code=403)

    @override_settings(PAYMENT_CREDENTIALS_ENCRYPTION_KEY=Fernet.generate_key().decode())
    def test_payment_form_uses_provider_fields_and_never_redisplays_a_secret(self):
        self.client.force_login(self.owner)
        url = reverse("payment_settings", args=[self.business.slug])
        form_page = self.client.get(url)
        self.assertContains(form_page, "Midtrans server key")
        self.assertContains(form_page, "BCA client secret")
        self.assertNotContains(form_page, 'name="credential_bundle"')
        secret = "synthetic-test-key-841"
        fields = {
            "action": "save", "provider": "midtrans", "environment": "sandbox", "server_key": secret,
        }
        invalid = self.client.post(url, fields)
        self.assertContains(invalid, "This field is required.")
        self.assertNotContains(invalid, secret)
        saved = self.client.post(url, {**fields, "merchant_id": "component-test"})
        self.assertRedirects(saved, url)
        self.assertNotContains(self.client.get(url), secret)
        connection = PaymentConnection.objects.get(business=self.business, provider="midtrans")
        self.assertEqual(connection.merchant_id, "component-test")
        self.assertNotIn(secret, connection.credentials_ciphertext)
