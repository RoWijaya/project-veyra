import uuid

from django.test import TestCase

from .models import Location, Membership, Printer, Product, Station, User
from .services import create_business, create_order, record_order_payment


class ReceiptWorkflowTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("register@example.invalid")
        self.business = create_business(owner=self.owner, name="Receipt Shop", slug="receipt-shop")
        self.location = Location.objects.create(business=self.business, name="Counter")
        self.product = Product.objects.create(business=self.business, name="Filter coffee", price="25000")
        self.order = create_order(
            business=self.business, actor=self.owner, items=[(self.product.pk, 2)],
            location_id=self.location.pk,
        )
        self.url = f"/app/receipt-shop/orders/{self.order.pk}/receipt/"
        self.client.force_login(self.owner)

    def pay(self):
        record_order_payment(
            business=self.business, order_id=self.order.pk, method="cash", actor=self.owner,
            idempotency_key=uuid.uuid4(), cash_received="60000",
        )

    def test_paid_receipt_uses_sale_snapshots_and_cash_change(self):
        self.assertEqual(self.client.get(self.url, {"format": "json"}).status_code, 404)
        self.pay()
        self.product.name = "New name"
        self.product.price = "35000"
        self.product.save()
        response = self.client.get(self.url, {"format": "json"})
        self.assertEqual(response.status_code, 200)
        data = response.json()["receipt"]
        self.assertEqual(data["items"][0]["name"], "Filter coffee")
        self.assertEqual(data["items"][0]["unit_price"], "25000.00")
        self.assertEqual(data["subtotal"], "50000.00")
        self.assertEqual(data["change_due"], "10000.00")
        self.assertIn("no-store", response["Cache-Control"])

    def test_queue_replay_does_not_duplicate_jobs_and_reprint_requires_acknowledgment(self):
        self.pay()
        printer = Printer.objects.create(business=self.business, location=self.location, name="Front")
        register = Station.objects.create(
            business=self.business, location=self.location, printer=printer, name="Till",
            kind="register", capabilities=["orders.view"],
        )
        data = {"station": register.pk, "idempotency_key": uuid.uuid4(), "action": "print"}
        first = self.client.post(self.url + "?format=json", data)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.json()["status"], "pending")
        replay = self.client.post(self.url + "?format=json", data)
        self.assertEqual(replay.json()["job"], first.json()["job"])
        reprint = dict(data, action="reprint", idempotency_key=uuid.uuid4())
        self.assertEqual(self.client.post(self.url + "?format=json", reprint).status_code, 409)
        history = self.client.get(self.url, {"format":"json"}).json()["history"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["status"], "pending")

    def test_denies_unrelated_business_and_read_only_dispatch(self):
        self.pay()
        other = User.objects.create_user("other-register@example.invalid")
        other_business = create_business(owner=other, name="Other Shop", slug="other-shop")
        self.client.force_login(other)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        url = self.url.replace(self.business.slug, other_business.slug)
        self.assertEqual(self.client.get(url).status_code, 404)
        Membership.objects.create(user=other, business=self.business, permissions=["orders.view"])
        self.assertEqual(self.client.get(self.url, {"format": "json"}).status_code, 200)
        self.assertEqual(self.client.post(self.url, {}).status_code, 403)

    def test_receipt_preview_supports_thermal_widths_and_history_navigation(self):
        self.pay()
        page = self.client.get(self.url)
        self.assertContains(page, "Print with browser")
        self.assertContains(page, "Rp 50000")
        self.assertNotContains(page, "receipt-58.css")
        page = self.client.get(self.url, {"width": "58"})
        self.assertContains(page, "receipt-58.css")
        history = self.client.get("/app/receipt-shop/orders/")
        self.assertContains(history, self.url)
        self.assertContains(self.client.get("/app/receipt-shop/pos/"), self.url)

    def test_invalid_station_input_and_foreign_printer_are_rejected(self):
        self.pay()
        for station in ("broken", str(uuid.uuid4())):
            self.assertEqual(self.client.post(self.url + "?format=json", {
                "station": station, "action": "print", "idempotency_key": uuid.uuid4(),
            }).status_code, 409)
        other = create_business(owner=self.owner, name="Other register", slug="other-register")
        foreign_printer = Printer.objects.create(business=other, name="Private")
        station = Station.objects.create(
            business=self.business, location=self.location, printer=foreign_printer,
            name="Invalid printer", capabilities=["orders.view"],
        )
        self.assertEqual(self.client.post(self.url + "?format=json", {
            "station": station.pk, "action": "print", "idempotency_key": uuid.uuid4(),
        }).status_code, 409)

    def test_failed_print_can_retry_and_acknowledged_copy_can_reprint(self):
        from .services import issue_station_pairing_code, pair_station_device
        self.pay()
        printer = Printer.objects.create(business=self.business, location=self.location, name="Front")
        station = Station.objects.create(
            business=self.business, location=self.location, printer=printer, name="Till",
            kind="register", capabilities=["orders.view"],
        )
        _, code = issue_station_pairing_code(station=station, actor=self.owner)
        _, token = pair_station_device(code=code, label="Receipt tester")
        auth = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
        job = self.client.post(self.url + "?format=json", {
            "station": station.pk, "action": "print", "idempotency_key": uuid.uuid4(),
        }).json()["job"]
        device_path = f"/api/stations/{station.pk}/print-jobs/"
        payload = self.client.get(device_path, **auth).json()["jobs"][0]
        self.assertEqual(payload["ticket_payload"]["total"], "50000.00")
        failed = self.client.post(
            f"{device_path}{job}/ack/", {"status":"failed", "error":"Out of paper"},
            content_type="application/json", **auth,
        )
        self.assertEqual(failed.status_code, 200)
        retry = self.client.post(self.url + "?format=json", {
            "station": station.pk, "action": "retry", "idempotency_key": uuid.uuid4(),
        })
        self.assertEqual(retry.status_code, 201)
        retry_job = retry.json()["job"]
        self.assertNotEqual(job, retry_job)
        acknowledged = self.client.post(
            f"{device_path}{retry_job}/ack/", {"status":"printed"},
            content_type="application/json", **auth,
        )
        self.assertEqual(acknowledged.status_code, 200)
        regress = self.client.post(
            f"{device_path}{retry_job}/ack/", {"status":"printing"},
            content_type="application/json", **auth,
        )
        self.assertEqual(regress.status_code, 400)
        reprint = self.client.post(self.url + "?format=json", {
            "station": station.pk, "action": "reprint", "idempotency_key": uuid.uuid4(),
        })
        self.assertEqual(reprint.status_code, 201)
        self.assertEqual(len(self.client.get(self.url, {"format":"json"}).json()["history"]), 3)

