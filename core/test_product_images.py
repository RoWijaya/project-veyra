import io
from datetime import timedelta
from unittest.mock import patch

from django.core import signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.utils import timezone
from django.core.management import call_command
from PIL import Image

from .models import DiningTable, Location, Membership, Product, User
from .services import create_business


class ProductImageWorkflowTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("image-owner@example.invalid")
        self.business = create_business(owner=self.owner, name="Image cafe", slug="image-cafe")
        self.product = Product.objects.create(business=self.business, name="Espresso", price=18000)
        location = Location.objects.create(business=self.business, name="East")
        self.table = DiningTable.objects.create(business=self.business, location=location, name="A3")
        token = signing.dumps([str(self.table.pk), str(self.table.qr_version)], salt="veyra-table-qr")
        self.menu = f"/order/table/{token}/menu.json"
        self.guest_url = f"/order/table/{token}/products/{self.product.pk}/image/"
        self.staff_url = f"/app/image-cafe/catalog/{self.product.pk}/image/"
        self.client.force_login(self.owner)

    def upload(self, color):
        stream = io.BytesIO()
        Image.new("RGB", (1500, 750), color).save(stream, "PNG")
        return self.client.post(self.staff_url, {
            "image": SimpleUploadedFile("photo.png", stream.getvalue(), "image/png"),
            "alt_text": "Espresso cup",
        })

    def test_upload_preview_replace_and_remove_in_same_menu(self):
        self.assertEqual(self.upload("red").status_code, 200)
        guest = Client()
        listing = guest.get(self.menu).json()["products"][0]
        self.assertEqual(listing["image_url"], self.guest_url)
        self.assertEqual(listing["image_alt"], "Espresso cup")
        preview = guest.get(self.guest_url)
        self.assertEqual(preview["Content-Type"], "image/webp")
        self.assertIn("no-store", preview["Cache-Control"])
        with Image.open(io.BytesIO(preview.content)) as decoded:
            self.assertEqual(decoded.size, (1024, 512))
        self.assertEqual(self.upload("blue").status_code, 200)
        self.assertNotEqual(self.client.get(self.staff_url).content, preview.content)
        self.assertEqual(self.client.post(self.staff_url, {"action": "remove"}).status_code, 200)
        self.assertEqual(guest.get(self.guest_url).status_code, 404)
        self.assertIsNone(guest.get(self.menu).json()["products"][0]["image_url"])

    def test_image_validation_csrf_permissions_and_guest_revocation(self):
        self.upload("red")
        malformed = SimpleUploadedFile("photo.png", b"<svg><script>x</script></svg>", "image/png")
        self.assertEqual(self.client.post(self.staff_url, {
            "image": malformed, "alt_text": "Cup",
        }).status_code, 400)
        self.assertEqual(self.client.get(self.staff_url).status_code, 200)
        other = User.objects.create_user("image-outsider@example.invalid")
        self.client.force_login(other)
        self.assertEqual(self.client.get(self.staff_url).status_code, 404)
        Membership.objects.create(business=self.business, user=other, permissions=["catalog.view"])
        self.assertEqual(self.client.get(self.staff_url).status_code, 200)
        self.assertEqual(self.upload("blue").status_code, 403)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.owner)
        self.assertEqual(csrf.post(self.staff_url, {"action": "remove"}).status_code, 403)
        self.table.active = False
        self.table.save(update_fields=["active"])
        self.assertEqual(Client().get(self.guest_url).status_code, 404)

    def test_banner_schedule_preview_repricing_and_worker_boundary(self):
        self.upload("red")
        now = timezone.now()
        fields = {
            "headline": "Espresso hour", "description": "Fresh espresso",
            "alt_text": "Espresso", "cta_text": "View espresso",
            "product": self.product.pk, "priority": 1, "active": "on",
            "starts_at": (now - timedelta(minutes=1)).isoformat(),
            "ends_at": (now + timedelta(minutes=1)).isoformat(),
            "locations": [self.table.location_id],
            "image": SimpleUploadedFile("espresso.webp", self.client.get(self.staff_url).content),
        }
        response = self.client.post("/app/image-cafe/banners/", fields)
        self.assertEqual(response.status_code, 302, response.content)
        guest = Client()
        listed = guest.get(self.menu).json()["banners"]
        self.assertEqual(listed[0]["headline"], "Espresso hour")
        self.assertEqual(guest.get(listed[0]["image_url"]).status_code, 200)
        call_command("process_outbox", once=True, stdout=io.StringIO())
        with patch("core.media_services.timezone.now", return_value=now + timedelta(minutes=2)):
            call_command("process_outbox", once=True, stdout=io.StringIO())
            self.assertEqual(guest.get(self.menu).json()["banners"], [])
            self.assertEqual(guest.get(listed[0]["image_url"]).status_code, 404)
