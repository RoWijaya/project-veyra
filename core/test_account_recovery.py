from urllib.parse import urlparse

from django.core import mail
from django.core.cache import cache
from django.test import TestCase

from .models import User


class VerificationRecoveryTests(TestCase):
    def setUp(self):
        cache.clear()

    def register(self):
        self.client.post(
            "/register/",
            {
                "email": "recover@example.com",
                "first_name": "Recover",
                "password1": "Long-Test-Password-8842!safe",
                "password2": "Long-Test-Password-8842!safe",
            },
        )
        return urlparse(mail.outbox[-1].body.split("Confirm your email: ", 1)[1]).path

    def test_resend_recovers_unverified_account_without_disclosing_existence(self):
        self.register()
        response = self.client.post("/verification/resend/", {"email": "recover@example.com"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 2)
        unknown = self.client.post("/verification/resend/", {"email": "unknown@example.com"})
        self.assertEqual(response.content, unknown.content)
        self.assertEqual(len(mail.outbox), 2)
        path = urlparse(mail.outbox[-1].body.split("Confirm your email: ", 1)[1]).path
        self.assertEqual(self.client.get(path).status_code, 200)
        login = self.client.post(
            "/login/",
            {
                "email": "recover@example.com",
                "password": "Long-Test-Password-8842!safe",
            },
        )
        self.assertEqual(login.status_code, 302)

    def test_used_verification_link_cannot_reactivate_disabled_account(self):
        path = self.register()
        self.assertEqual(self.client.get(path).status_code, 200)
        user = User.objects.get(email="recover@example.com")
        user.is_active = False
        user.save(update_fields=["is_active"])
        self.assertEqual(self.client.get(path).status_code, 400)
        self.client.post("/verification/resend/", {"email": user.email})
        self.assertEqual(len(mail.outbox), 1)
        response = self.client.post(
            "/login/",
            {
                "email": user.email,
                "password": "Long-Test-Password-8842!safe",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_resend_is_throttled_and_csrf_protected(self):
        from django.test import Client

        self.register()
        for _ in range(5):
            self.client.post("/verification/resend/", {"email": "recover@example.com"})
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(
            Client(enforce_csrf_checks=True)
            .post(
                "/verification/resend/",
                {"email": "recover@example.com"},
            )
            .status_code,
            403,
        )

    def test_resend_keeps_local_return_path_and_rejects_external_redirect(self):
        self.register()
        response = self.client.post(
            "/verification/resend/",
            {
                "email": "recover@example.com",
                "next": "/app/",
            },
        )
        self.assertEqual(response.status_code, 200)
        link = urlparse(mail.outbox[-1].body.split("Confirm your email: ", 1)[1])
        self.assertEqual(link.query, "next=%2Fapp%2F")
        cache.clear()
        self.client.post(
            "/verification/resend/",
            {
                "email": "recover@example.com",
                "next": "https://attacker.invalid/",
            },
        )
        link = urlparse(mail.outbox[-1].body.split("Confirm your email: ", 1)[1])
        self.assertEqual(link.query, "")

class PasswordResetPresentationTests(TestCase):
    def test_unusable_link_offers_recovery_without_password_fields(self):
        response = self.client.get("/password-reset/MQ/invalid/")
        self.assertContains(response, "This reset link is no longer available")
        self.assertContains(response, 'href="/password-reset/"')
        self.assertNotContains(response, 'name="password"')
        self.assertNotContains(response, 'name="new_password1"')

    def test_completion_directs_users_to_sign_in_with_the_new_password(self):
        response = self.client.get("/password-reset/complete/")
        self.assertContains(response, "Your password has been updated")
        self.assertContains(response, 'href="/login/"')
        self.assertNotContains(response, "we sent a password reset link")
