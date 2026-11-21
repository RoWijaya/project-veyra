from django.test import TestCase
from django.urls import reverse


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
