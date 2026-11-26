from unittest.mock import Mock, patch

from django.core.mail import EmailMessage
from django.test import TestCase, override_settings

from .email_backend import ResendEmailBackend
from .models import OutboxEvent, Product
from .services import dispatch_outbox_event
from . import test_catalog_restore


@override_settings(
    SUPABASE_MEDIA_URL="https://catalog-fixture.example.invalid", SUPABASE_MEDIA_BUCKET="private-menus",
    SUPABASE_SERVICE_ROLE_KEY="synthetic-key", RESEND_API_KEY="synthetic-email-key",
)
class StagingAdapterTests(TestCase):
    setUp = test_catalog_restore.CatalogRestoreTests.setUp
    @patch("core.media_storage.requests.post")
    def test_normalized_asset_queues_private_copy_and_retries_safely(self, post):
        post.side_effect = [Mock(status_code=503), Mock(status_code=200)]
        response = test_catalog_restore.publish_ordering_design(
            self.client, self.settings, {"logo": test_catalog_restore.photo("red")},
        )
        self.assertRedirects(response, self.settings)
        logo = self.guest.get(self.menu + "ordering-logo/")
        self.assertEqual(logo.status_code, 200)
        event = OutboxEvent.objects.get(event_type="media.archive")
        self.assertFalse(dispatch_outbox_event(event.pk))
        event.refresh_from_db()
        self.assertIsNone(event.published_at)
        from django.utils import timezone

        OutboxEvent.objects.filter(pk=event.pk).update(next_attempt_at=timezone.now())
        self.assertTrue(dispatch_outbox_event(event.pk))
        self.assertFalse(dispatch_outbox_event(event.pk))
        url = post.call_args.args[0]
        request = post.call_args.kwargs
        self.assertIn(f"/storage/v1/object/private-menus/{event.business_id.hex}/ordering_logo/", url)
        self.assertTrue(url.endswith(".webp"))
        self.assertEqual(request["data"], logo.content)
        self.assertEqual(request["headers"]["Content-Type"], "image/webp")
        self.assertFalse(request["allow_redirects"])

    @patch("core.media_storage.requests.post")
    def test_product_uuid_is_serialized_and_archived(self, post):
        post.return_value = Mock(status_code=200)
        product = Product.objects.create(business=self.business, name="Espresso", price="18000")
        url = self.catalog + f"{product.pk}/image/"
        response = self.client.post(url, {"image": test_catalog_restore.photo("green"), "alt_text": "Espresso"})
        self.assertEqual(response.status_code, 200)
        job = OutboxEvent.objects.get(event_type="media.archive")
        self.assertEqual(job.payload["pk"], str(product.pk))
        self.assertTrue(dispatch_outbox_event(job.pk))
        self.assertIn("/product/", post.call_args.args[0])

    @patch("core.email_backend.requests.post")
    def test_resend_https_sends_and_rejects_delivery_errors(self, post):
        accepted = Mock(status_code=200)
        accepted.json.return_value = {"id": "local-fixture"}
        post.side_effect = [accepted, Mock(status_code=429)]
        email = EmailMessage("Verify your account", "Synthetic email", "sender@example.invalid", ["guest@example.invalid"])
        backend = ResendEmailBackend()
        self.assertEqual(backend.send_messages([email]), 1)
        with self.assertRaisesRegex(OSError, "did not accept"):
            backend.send_messages([email])
        self.assertEqual(post.call_args.args, ("https://api.resend.com/emails",))
        self.assertFalse(post.call_args.kwargs["allow_redirects"])
