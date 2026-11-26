import asyncio
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import Mock
from decimal import Decimal
from unittest.mock import patch

import requests
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.core.management import call_command
from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from .models import BillingAttempt, BillingInvoice, Invitation, Membership, OutboxEvent, SubscriptionPlan, User
from .services import business_entitlements, create_business, dispatch_outbox_event

class BillingFixture:
    def setUp(self):
        self.owner = User.objects.create_user("billing-owner@example.invalid")
        self.business = create_business(owner=self.owner, name="Billing Shop", slug="billing-shop")
        self.plan = SubscriptionPlan.objects.create(code="standard-paid", name="Standard", monthly_price="125000",
            entitlements={"features": ["pos", "catalog"], "limits": {"locations": 2}})
        self.url = "/app/billing-shop/settings/billing/"
        self.client.force_login(self.owner)

    def invoice_url(self, plan=None):
        response = self.client.post(self.url, {"action": "select", "plan": (plan or self.plan).code})
        self.assertEqual(response.status_code, 302)
        return response.url


class BillingTests(BillingFixture, TestCase):
    def test_owner_only_plan_and_invoice_workflow_preserves_server_price(self):
        trial = self.client.get(self.url)
        self.assertContains(trial, "Veyra Starter Trial")
        preview = self.client.get(self.url, {"plan": self.plan.code})
        self.assertContains(preview, "Standard")
        result = self.client.post(self.url, {"action": "select", "plan": self.plan.code,
                                            "price": "1", "status": "active", "features": ["*"]})
        self.assertEqual(result.status_code, 302)
        invoice = self.client.get(result.url)
        self.assertContains(invoice, "125,000")
        self.assertContains(invoice, "Awaiting payment")
        self.assertEqual(self.business.subscription.status, "trial")
        self.plan.monthly_price = Decimal("250000")
        self.plan.save()
        self.assertContains(self.client.get(result.url), "125,000")
        member = User.objects.create_user("billing-member@example.invalid")
        Membership.objects.create(business=self.business, user=member, permissions=["employees.manage"])
        self.client.force_login(member)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.get(result.url).status_code, 404)
        create_business(owner=member, name="Other Store", slug="other-billing-store")
        self.assertEqual(self.client.get(result.url.replace("billing-shop", "other-billing-store")).status_code, 404)

    def test_trial_expiration_restricts_operations_without_hiding_billing(self):
        subscription = self.business.subscription
        self.assertEqual(subscription.trial_ends_at - subscription.trial_started_at, timedelta(days=14))
        with patch("django.utils.timezone.now", return_value=subscription.trial_ends_at):
            self.client.cookies.clear()
            self.client.force_login(self.owner)
            call_command("process_billing", once=True)
            call_command("process_billing", once=True)
            self.assertContains(self.client.get(self.url), "Expired")
            self.assertFalse(business_entitlements(self.business)["active"])
            self.assertContains(self.client.get(self.url, {"plan": self.plan.code}), "Create invoice")
        subscription.refresh_from_db()
        self.assertEqual(subscription.events.filter(event_type="subscription.expired").count(), 1)
        self.assertEqual(subscription.trial_ends_at - subscription.trial_started_at, timedelta(days=14))

    def test_starter_trial_eligibility_preserves_existing_trial_terms(self):
        starter = self.business.subscription.plan
        starter.active = False
        starter.entitlements = {"features": [], "limits": {"locations": 1}}
        starter.save(update_fields=["active", "entitlements"])
        newer = create_business(owner=self.owner, name="No Trial", slug="no-new-trial")
        self.assertEqual(newer.subscription.status, "expired")
        self.assertIsNone(newer.subscription.trial_ends_at)
        self.assertEqual(newer.subscription.events.get().event_type, "trial.unavailable")
        self.assertFalse(business_entitlements(newer)["active"])
        self.assertContains(self.client.get("/app/no-new-trial/settings/billing/"), "Expired")
        self.assertIn("pos", business_entitlements(self.business)["features"])
        self.assertEqual(self.business.subscription.terms["trial_days"], 14)

    def test_existing_invoice_stays_available_without_provider_credentials(self):
        invoice_url = self.invoice_url()
        self.assertContains(self.client.get(invoice_url), "Platform checkout is currently unavailable.")
        self.assertContains(self.client.post(invoice_url, {"action": "checkout"}), "checkout is unavailable", status_code=400)
        self.assertFalse(BillingAttempt.objects.exists())
        self.plan.active = False
        self.plan.save()
        self.assertContains(self.client.post(self.url, {"action": "select", "plan": self.plan.code}),
                            "That plan is unavailable", status_code=400)
        self.assertContains(self.client.get(invoice_url), "125,000")

    def test_owner_settings_and_platform_admin_permissions(self):
        no_csrf = Client(enforce_csrf_checks=True)
        no_csrf.force_login(self.owner)
        self.assertEqual(no_csrf.post(self.url, {"action":"cancel"}).status_code, 403)
        self.assertNotEqual(self.client.get("/admin/core/subscriptionplan/").status_code, 200)
        self.assertContains(self.client.post(self.url, {"action":"cancel"}), "paid period", status_code=400)
        self.assertContains(self.client.get(self.url), "Veyra Starter Trial")
        invoice_url = self.invoice_url()
        platform = Client()
        platform.force_login(User.objects.create_superuser("billing-platform@example.invalid", "test-only-platform"))
        plans = "/admin/core/subscriptionplan/"
        plan_data = {
            "name": "New Plan", "code": "new-plan", "monthly_price": "75000", "currency": "IDR",
            "trial_days": "0", "entitlements": '{"features":["pos"],"limits":{"locations":1}}', "active": "on",
        }
        self.assertEqual(platform.post(plans + "add/", plan_data).status_code, 302)
        self.assertContains(self.client.get(self.url), "Review New Plan")
        offer = SubscriptionPlan.objects.get(code="new-plan")
        plan_data.pop("active")
        plan_data["code"] = "new-slug"
        self.assertEqual(platform.post(f"{plans}{offer.pk}/change/", plan_data).status_code, 302)
        offer.refresh_from_db()
        self.assertFalse(offer.active)
        self.assertEqual(offer.code, "new-plan")
        self.assertNotContains(self.client.get(self.url), "Review New Plan")

        subscriptions = "/admin/core/businesssubscription/"
        selected = {"_selected_action": str(self.business.subscription.pk), "index": "0"}
        self.assertEqual(platform.post(subscriptions, {**selected, "action": "suspend_access"}).status_code, 302)
        restricted = self.client.get(self.url, {"plan": self.plan.code})
        self.assertContains(restricted, "Restricted")
        self.assertContains(restricted, "Contact platform support")
        self.assertNotContains(restricted, "Create invoice")
        invoice = self.client.get(invoice_url)
        self.assertContains(invoice, "125,000")
        self.assertContains(invoice, "Contact platform support to restore checkout")
        self.assertFalse(business_entitlements(self.business)["active"])
        self.assertEqual(platform.post(subscriptions, {**selected, "action": "restore_access"}).status_code, 302)
        self.assertTrue(business_entitlements(self.business)["active"])
        self.assertEqual(self.business.subscription.events.filter(event_type__startswith="platform.").count(), 2)


def response(data, status=200):
    return Mock(status_code=status, json=Mock(return_value=data))


@override_settings(
    VEYRA_BILLING_MIDTRANS_SERVER_KEY="test-platform-key-only",
    VEYRA_BILLING_MIDTRANS_SIGNATURE_SECRET_KEY="test-platform-signature",
    VEYRA_BILLING_MIDTRANS_ENV="sandbox",
    VEYRA_BILLING_PUBLIC_ORIGIN="https://billing.example.invalid",
    VEYRA_BILLING_MIDTRANS_APPROVAL_REFERENCE="synthetic-approval",
    VEYRA_BILLING_MIDTRANS_APPROVED_AT="2026-01-01T00:00:00Z",
)
class BillingGatewayTests(BillingFixture, TestCase):
    @patch("core.billing_gateway.requests.request")
    @override_settings(VEYRA_BILLING_MIDTRANS_APPROVAL_REFERENCE="")
    def test_platform_checkout_requires_its_own_approval(self, transport):
        invoice_url = self.invoice_url()
        self.assertContains(
            self.client.post(invoice_url, {"action": "checkout"}),
            "Platform checkout is unavailable", status_code=400,
        )
        transport.assert_not_called()
        self.assertFalse(BillingAttempt.objects.exists())

    def checkout(self, invoice_url, transport):
        transport.return_value = response({"redirect_url":"https://app.sandbox.midtrans.com/snap/v4/qa"})
        payment = self.client.post(invoice_url, {"action": "checkout", "gross_amount": "5"})
        self.assertEqual(payment.status_code, 302)
        self.assertEqual(payment.url, "https://app.sandbox.midtrans.com/snap/v4/qa")
        request = transport.call_args
        amount = int(BillingInvoice.objects.get(pk=invoice_url.rstrip("/").split("/")[-1]).total)
        self.assertEqual(request.kwargs["json"]["transaction_details"]["gross_amount"], amount)
        self.assertEqual(request.kwargs["auth"], ("test-platform-key-only", ""))
        self.assertEqual(request.kwargs["json"]["page_expiry"], {"duration": 24, "unit": "hours"})
        return request.kwargs["json"]["transaction_details"]["order_id"], str(amount)

    def notify(self, reference, amount, transport, status="settlement", *, provider_amount=None, signing_key="test-platform-signature"):
        payload = {"order_id": reference, "gross_amount": amount, "status_code": "200"}
        payload["signature_key"] = hashlib.sha512(f"{reference}200{amount}{signing_key}".encode()).hexdigest()
        transport.return_value = response({
            **payload, "gross_amount": provider_amount or amount, "currency": "IDR", "transaction_status": status,
            "fraud_status": "accept", "transaction_id": "tx-" + reference,
        })
        return self.client.post("/api/billing/notifications/midtrans/", payload, content_type="application/json")

    @patch("core.billing_gateway.requests.request")
    def test_verified_payment_failure_retry_replay_and_activation(self, transport):
        invoice_url = self.invoice_url()
        reference, amount = self.checkout(invoice_url, transport)
        self.assertEqual(self.client.post(invoice_url, {"action": "checkout"}).status_code, 302)
        self.assertEqual(transport.call_count, 1)
        forged = self.client.post("/api/billing/notifications/midtrans/",
            {"order_id": reference, "status_code": "200", "gross_amount": amount, "signature_key": "fake"},
            content_type="application/json")
        self.assertEqual(forged.status_code, 403)
        self.assertEqual(self.notify(reference, amount, transport, provider_amount="1").status_code, 503)
        self.assertContains(self.client.get(self.url), "Trial")
        self.assertEqual(self.notify(reference, amount, transport, status="deny").status_code, 200)
        self.assertContains(self.client.get(invoice_url), "Retry payment")
        retry_reference, amount = self.checkout(invoice_url, transport)
        self.assertNotEqual(reference, retry_reference)
        self.assertEqual(self.notify(retry_reference, amount, transport).status_code, 200)
        self.assertEqual(self.notify(retry_reference, amount, transport, status="pending").status_code, 200)
        self.assertContains(self.client.get(invoice_url), "Paid")
        sub = self.business.subscription
        sub.refresh_from_db()
        self.assertEqual(sub.status, "active")
        self.assertEqual(sub.events.filter(event_type="invoice.paid").count(), 1)
        self.assertEqual(business_entitlements(self.business)["limits"], {"locations": 2})

    @patch("core.billing_gateway.requests.request")
    def test_platform_rotation_window_is_limited_to_prior_attempts(self, transport):
        reference, amount = self.checkout(self.invoice_url(), transport)
        cutover = timezone.now()
        with override_settings(
            VEYRA_BILLING_MIDTRANS_PREVIOUS_SIGNATURE_SECRET_KEY="previous-platform-signature",
            VEYRA_BILLING_MIDTRANS_ROTATED_AT=cutover.isoformat(),
            VEYRA_BILLING_MIDTRANS_PREVIOUS_SIGNATURE_UNTIL=(cutover + timedelta(days=3)).isoformat(),
        ):
            self.assertEqual(self.notify(reference, amount, transport, signing_key="previous-platform-signature").status_code, 200)
            BillingAttempt.objects.update(created_at=cutover + timedelta(seconds=1))
            self.assertEqual(self.notify(reference, amount, transport, signing_key="previous-platform-signature").status_code, 403)
            self.assertEqual(self.notify(reference, amount, transport).status_code, 200)

    @patch("core.billing_gateway.requests.request")
    def test_timeout_reuses_provider_payment_reference(self, transport):
        invoice_url = self.invoice_url()
        transport.side_effect = requests.ConnectionError
        self.assertContains(self.client.post(invoice_url, {"action": "checkout"}), "status is unavailable", status_code=400)
        ref = transport.call_args.kwargs["json"]["transaction_details"]["order_id"]
        transport.side_effect = None
        recovery_ref, _ = self.checkout(invoice_url, transport)
        self.assertEqual(ref, recovery_ref)
        self.assertEqual(BillingAttempt.objects.count(), 1)

    @patch("core.billing_gateway.requests.request")
    def test_renewal_downgrade_grace_recovery_and_cancellation(self, transport):
        invoice_url = self.invoice_url()
        ref, amount = self.checkout(invoice_url, transport)
        self.assertEqual(self.notify(ref, amount, transport).status_code, 200)
        lower = SubscriptionPlan.objects.create(code="starter-paid", name="Starter", monthly_price="50000",
            entitlements={"features":["*"],"limits":{"locations":0, "employees":0, "workspaces":0}})
        self.assertEqual(self.client.post(self.url, {"action":"select","plan":lower.code}).status_code, 302)
        sub = self.business.subscription
        sub.refresh_from_db()
        self.assertEqual(sub.pending_plan, lower)
        self.assertEqual(sub.plan, self.plan)
        boundary = sub.current_period_end
        with patch("django.utils.timezone.now", return_value=boundary):
            self.client.cookies.clear()
            self.client.force_login(self.owner)
            call_command("process_billing", once=True)
            call_command("process_billing", once=True)
            renewal = sub.invoices.get(status=BillingInvoice.Status.OPEN)
            self.assertEqual(renewal.period_start, boundary)
            self.assertEqual(renewal.total, Decimal("50000"))
            self.assertContains(self.client.get(self.url), "Past due")
            self.assertTrue(business_entitlements(self.business)["active"])
        expiry = boundary + timedelta(days=7)
        with patch("django.utils.timezone.now", return_value=expiry):
            self.client.cookies.clear()
            self.client.force_login(self.owner)
            call_command("process_billing", once=True)
            self.assertFalse(business_entitlements(self.business)["active"])
            self.assertContains(self.client.get(self.url), "Expired")
            renew_url = f"{self.url}invoices/{renewal.pk}/"
            ref, amount = self.checkout(renew_url, transport)
            self.assertEqual(self.notify(ref, amount, transport).status_code, 200)
            self.assertTrue(business_entitlements(self.business)["active"])
            self.assertEqual(self.business.locations.count(), 1)
            self.assertContains(self.client.post("/app/billing-shop/locations/", {"name":"Extra"}, follow=True), "locations limit")
            invited = User.objects.create_user("billing-intake@example.invalid")
            Invitation.objects.create(business=self.business, invited_by=self.owner, email=invited.email,
                permissions=[], expires_at=expiry + timedelta(days=1),
                token_digest=hashlib.sha256(b"billing-limited-invite").hexdigest())
            applicant = Client()
            applicant.force_login(invited)
            self.assertContains(applicant.post("/invitations/billing-shop/billing-limited-invite/accept/"),
                                "employees limit", status_code=403)
            self.assertContains(self.client.post("/app/billing-shop/stations/", {
                "action":"create", "name":"Preparation", "kind":"kitchen", "handling_mode":"both",
            }), "workspaces limit")
            self.assertEqual(self.client.post(self.url, {"action":"cancel"}).status_code, 302)
            self.assertEqual(self.client.post(self.url, {"action":"resume"}).status_code, 302)
            self.assertEqual(self.client.post(self.url, {"action":"cancel"}).status_code, 302)
        sub.refresh_from_db()
        self.assertEqual(sub.plan, lower)
        with patch("django.utils.timezone.now", return_value=sub.current_period_end):
            self.client.cookies.clear()
            self.client.force_login(self.owner)
            call_command("process_billing", once=True)
            self.assertContains(self.client.get(self.url), "Canceled")
            self.assertFalse(business_entitlements(self.business)["active"])
            self.assertEqual(self.client.post(self.url, {"action":"select","plan":self.plan.code}).status_code, 302)
        self.assertEqual(sub.invoices.filter(status="paid").count(), 2)


class BillingConcurrencyTests(BillingFixture, TransactionTestCase):
    def test_concurrent_plan_requests_create_one_invoice(self):
        browsers = [Client(), Client()]
        for browser in browsers:
            browser.force_login(self.owner)
        barrier = Barrier(2)

        def select(browser):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return browser.post(self.url, {"action":"select", "plan":self.plan.code}).url
            finally:
                close_old_connections()

        with ThreadPoolExecutor(2) as pool:
            requests = [pool.submit(select, browser) for browser in browsers]
            self.assertEqual(len({request.result(timeout=20) for request in requests}), 1)
        self.assertEqual(BillingInvoice.objects.count(), 1)

    def test_owner_receives_billing_event_and_member_cannot_subscribe(self):
        from config.asgi import application

        member = User.objects.create_user("billing-stream-member@example.invalid")
        Membership.objects.create(business=self.business, user=member, permissions=["employees.manage"])
        blocked = Client()
        blocked.force_login(member)

        def connect_for(browser):
            conn = WebsocketCommunicator(application, f"/ws/business/{self.business.slug}/billing/")
            token = browser.cookies[settings.SESSION_COOKIE_NAME].value
            conn.scope["headers"] = [(b"host", b"localhost"), (b"origin", b"http://localhost"),
                                     (b"cookie", f"{settings.SESSION_COOKIE_NAME}={token}".encode())]
            return conn

        async def exercise():
            owner = connect_for(self.client)
            outsider = connect_for(blocked)
            self.assertTrue((await owner.connect())[0])
            self.assertFalse((await outsider.connect())[0])
            posted = await sync_to_async(self.client.post)(self.url, {"action":"select", "plan":self.plan.code})
            self.assertEqual(posted.status_code, 302)
            event = await OutboxEvent.objects.filter(business=self.business, event_type="billing.update").alatest("created_at")
            self.assertTrue(await sync_to_async(dispatch_outbox_event)(event.pk))
            self.assertEqual(await owner.receive_json_from(timeout=3), {"event":"billing.update"})
            await owner.disconnect()
            await outsider.disconnect()

        asyncio.run(exercise())
