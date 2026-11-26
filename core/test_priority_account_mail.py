from datetime import timedelta
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

import requests
from django.core.cache import cache
from django.core.management import call_command
from django.conf import settings
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from .models import User, VerificationDelivery


@override_settings(
    MAILERS={"default": {"BACKEND": "core.email_backend.ResendEmailBackend"}},
    RESEND_API_KEY="synthetic-mail-key",
)
class DurableAccountMailTests(TestCase):
    def setUp(self):
        cache.clear()
        self.password = "A-long-synthetic-password-193!"
        self.email = "pending@example.invalid"

    def register(self):
        return self.client.post("/register/", {
            "email": self.email, "first_name": "Pending", "password1": self.password, "password2": self.password,
        })

    def test_signup_validation_and_session_cookie_do_not_disclose_duplicates(self):
        existing = self.register()
        fields = {"email": self.email, "first_name": "Pending", "password1": self.password, "password2": self.password}
        duplicate = Client().post("/register/", fields)
        self.assertEqual(existing.content, duplicate.content)
        self.assertIn(settings.SESSION_COOKIE_NAME, duplicate.cookies)
        invalid = {**fields, "password2": "Different-password-42!"}
        known = Client().post("/register/", invalid)
        unknown = Client().post("/register/", {**invalid, "email": "unused@example.invalid"})
        self.assertEqual(known.context["form"].errors, unknown.context["form"].errors)
        known = Client().post("/register/", {"email": self.email})
        unknown = Client().post("/register/", {"email": "unused@example.invalid"})
        self.assertEqual(known.content, unknown.content)
        self.assertContains(known, "Check your email")
        self.assertEqual(User.objects.filter(email=self.email).count(), 1)

    @patch("core.email_backend.requests.post")
    def test_signup_survives_timeout_and_worker_restart_then_activates_once(self, transport):
        response = self.register()
        self.assertContains(response, "temporary delivery failures")
        transport.assert_not_called()
        self.assertEqual(self.register().content, response.content)
        self.assertEqual(User.objects.filter(email=self.email).count(), 1)
        transport.side_effect = requests.Timeout
        call_command("process_account_mail", once=True)
        first_attempt = transport.call_args.kwargs
        job = VerificationDelivery.objects.get(user__email=self.email)
        self.assertEqual(job.state, VerificationDelivery.State.PENDING)
        self.assertEqual(job.attempts, 1)
        self.assertFalse(job.user.is_active)
        call_command("process_account_mail", once=True)
        transport.assert_called_once()

        VerificationDelivery.objects.filter(pk=job.pk).update(
            state=VerificationDelivery.State.SENDING, next_attempt_at=timezone.now() - timedelta(seconds=1),
        )
        transport.side_effect = None
        transport.return_value = Mock(status_code=200, json=Mock(return_value={"id": "synthetic-email"}))
        call_command("process_account_mail", once=True)
        retry = transport.call_args.kwargs
        self.assertEqual(retry["headers"]["Idempotency-Key"], first_attempt["headers"]["Idempotency-Key"])
        self.assertEqual(retry["json"]["text"], first_attempt["json"]["text"])
        job.refresh_from_db()
        self.assertEqual(job.state, VerificationDelivery.State.SENT)
        link = urlsplit(retry["json"]["text"].removeprefix("Confirm your email: ")).path
        with patch("django.core.signing.time.time", return_value=(job.requested_at + timedelta(days=2)).timestamp()):
            self.assertEqual(self.client.get(link).status_code, 400)
        self.assertEqual(self.client.get(link).status_code, 200)
        self.assertTrue(User.objects.get(email=self.email).is_active)
        self.assertEqual(self.client.get(link).status_code, 400)

    @patch("core.email_backend.requests.post")
    def test_permanent_rejection_stays_recoverable_through_throttled_resend(self, transport):
        self.register()
        transport.return_value = Mock(status_code=422)
        call_command("process_account_mail", once=True)
        first_key = transport.call_args.kwargs["headers"]["Idempotency-Key"]
        job = VerificationDelivery.objects.get(user__email=self.email)
        self.assertEqual(job.state, VerificationDelivery.State.FAILED)

        known = self.client.post("/verification/resend/", {"email": self.email})
        unknown = self.client.post("/verification/resend/", {"email": "unknown@example.invalid"})
        self.assertEqual(known.content, unknown.content)
        self.client.post("/verification/resend/", {"email": self.email})
        transport.return_value = Mock(status_code=200, json=Mock(return_value={"id": "synthetic-email"}))
        call_command("process_account_mail", once=True)
        self.assertEqual(transport.call_count, 2)
        self.assertNotEqual(first_key, transport.call_args.kwargs["headers"]["Idempotency-Key"])
        job.refresh_from_db()
        self.assertEqual(job.state, VerificationDelivery.State.SENT)
