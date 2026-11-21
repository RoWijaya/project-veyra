import asyncio
import json
import time
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pyotp
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from cryptography.fernet import Fernet
from django.core.cache import cache
from django.test import Client, TestCase, TransactionTestCase, override_settings
from joserfc import jwt
from joserfc.jwk import RSAKey
from requests import Response

from .models import Authenticator, User
from .services import create_business


@override_settings(ACCOUNT_SECURITY_ENCRYPTION_KEY=Fernet.generate_key().decode())
class MFAWorkflowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("mfa@example.com", "Long-Password-For-MFA-8842!")
        self.client.force_login(self.user)

    def test_enrollment_login_challenge_and_single_use_recovery(self):
        response = self.client.post(
            "/account/security/",
            {
                "action": "begin",
                "password": "Long-Password-For-MFA-8842!",
            },
        )
        self.assertEqual(response.status_code, 200)
        secret = response.context["setup_secret"]
        response = self.client.post(
            "/account/security/",
            {
                "action": "confirm",
                "code": pyotp.TOTP(secret).now(),
            },
        )
        self.assertContains(response, "Save your recovery codes")
        recovery = response.context["recovery_codes"][0]
        self.client.post("/logout/")
        login = self.client.post(
            "/login/",
            {
                "email": self.user.email,
                "password": "Long-Password-For-MFA-8842!",
            },
        )
        self.assertRedirects(login, "/account/mfa/", fetch_redirect_response=False)
        self.assertRedirects(
            self.client.get("/app/"), "/account/mfa/", fetch_redirect_response=False
        )
        self.assertEqual(self.client.post("/account/mfa/", {"code": recovery}).status_code, 302)
        self.assertEqual(self.client.get("/app/").status_code, 200)
        self.client.post("/logout/")
        self.client.post(
            "/login/", {"email": self.user.email, "password": "Long-Password-For-MFA-8842!"}
        )
        self.assertContains(
            self.client.post("/account/mfa/", {"code": recovery}), "Invalid or already used code"
        )

    @override_settings(MFA_REQUIRE_PRIVILEGED=True)
    def test_privileged_user_must_enroll_and_security_mutations_require_csrf(self):
        from django.test import Client

        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.assertRedirects(
            self.client.get("/admin/"), "/account/security/", fetch_redirect_response=False
        )
        self.assertEqual(self.client.get("/account/security/").status_code, 200)
        browser = Client(enforce_csrf_checks=True)
        browser.force_login(self.user)
        self.assertEqual(browser.post("/account/security/", {"action": "begin"}).status_code, 403)
        self.assertEqual(browser.post("/auth/google/start/").status_code, 302)

    def test_enrollment_code_cannot_be_replayed_at_login(self):
        response = self.client.post(
            "/account/security/", {"action": "begin", "password": "Long-Password-For-MFA-8842!"}
        )
        code = pyotp.TOTP(response.context["setup_secret"]).now()
        self.client.post("/account/security/", {"action": "confirm", "code": code})
        self.client.post("/logout/")
        self.client.post(
            "/login/", {"email": self.user.email, "password": "Long-Password-For-MFA-8842!"}
        )
        self.assertContains(
            self.client.post("/account/mfa/", {"code": code}), "Invalid or already used code"
        )


@override_settings(
    GOOGLE_CLIENT_ID="test-client",
    GOOGLE_CLIENT_SECRET="test-secret",
    GOOGLE_REDIRECT_URI="http://testserver/auth/google/callback/",
)
class GoogleOIDCWorkflowTests(TestCase):
    def setUp(self):
        self.key = RSAKey.generate_key(2048, parameters={"kid": "test-key"})
        self.nonce = ""
        self.changes = {}
        self.token_request = None
        self.bad_signature = False
        self.http = patch("requests.sessions.Session.send", side_effect=self.provider)
        self.http.start()
        self.addCleanup(self.http.stop)

    def provider(self, request, **kwargs):
        response = Response()
        response.status_code = 200
        response.request = request
        if ".well-known" in request.url:
            payload = {
                "issuer": "https://accounts.google.com",
                "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
                "token_endpoint": "https://oauth2.googleapis.com/token",
                "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
                "id_token_signing_alg_values_supported": ["RS256"],
            }
        elif request.url.endswith("/certs"):
            payload = {"keys": [self.key.as_dict(private=False)]}
        elif request.url.endswith("/token"):
            self.token_request = request
            claims = {
                "iss": "https://accounts.google.com",
                "sub": "google-subject-1",
                "aud": "test-client",
                "iat": int(time.time()),
                "exp": int(time.time()) + 300,
                "nonce": self.nonce,
                "email": "google@example.com",
                "email_verified": True,
                **self.changes,
            }
            signing_key = RSAKey.generate_key(2048) if self.bad_signature else self.key
            payload = {
                "access_token": "external-test-token",
                "token_type": "Bearer",
                "id_token": jwt.encode({"alg": "RS256", "kid": "test-key"}, claims, signing_key),
            }
        else:
            raise AssertionError("Unexpected external request")
        response._content = json.dumps(payload).encode()
        response.headers["Content-Type"] = "application/json"
        return response

    def start(self, **data):
        response = self.client.post("/auth/google/start/", data)
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlparse(response.url).query)
        self.nonce = query["nonce"][0]
        self.assertEqual(query["code_challenge_method"], ["S256"])
        return query["state"][0]

    def callback(self, state):
        return self.client.get("/auth/google/callback/", {"state": state, "code": "test-code"})

    def test_signed_oidc_login_pkce_and_state_replay(self):
        state = self.start()
        self.assertEqual(self.callback(state).status_code, 302)
        self.assertEqual(self.client.get("/app/").status_code, 200)
        body = self.token_request.body
        self.assertIn("code_verifier=", body.decode() if isinstance(body, bytes) else body)
        self.assertEqual(self.callback(state).status_code, 400)

    def test_matching_email_requires_explicit_authenticated_link(self):
        user = User.objects.create_user("google@example.com", "Password-Secure-8842!")
        self.assertEqual(self.callback(self.start()).status_code, 400)
        self.client.force_login(user)
        state = self.start(intent="link", password="Password-Secure-8842!")
        self.assertEqual(self.callback(state).status_code, 302)
        self.client.post("/logout/")
        self.assertEqual(self.callback(self.start()).status_code, 302)
        self.assertContains(self.client.get("/account/security/"), "Connected")

    def test_invalid_identity_claims_fail_closed(self):
        for changes in (
            {"nonce": "wrong"},
            {"aud": "wrong"},
            {"iss": "https://evil.invalid"},
            {"exp": int(time.time()) - 600},
            {"email_verified": False},
        ):
            with self.subTest(changes=changes):
                self.changes = changes
                self.assertEqual(self.callback(self.start()).status_code, 400)
                self.assertNotIn("_auth_user_id", self.client.session)

    def test_wrong_state_and_invalid_signature_are_rejected(self):
        self.start()
        self.assertEqual(self.callback("wrong-state").status_code, 400)
        self.bad_signature = True
        self.assertEqual(self.callback(self.start()).status_code, 400)


@override_settings(ACCOUNT_SECURITY_ENCRYPTION_KEY=Fernet.generate_key().decode())
class MFARealtimeTests(TransactionTestCase):
    def test_partial_login_cannot_open_order_stream_until_second_factor(self):
        from django.conf import settings

        from config.asgi import application

        user = User.objects.create_user("mfa-stream@example.com", "Long-Password-For-MFA-8842!")
        business = create_business(owner=user, name="MFA stream", slug="mfa-stream")
        secret = pyotp.random_base32()
        Authenticator.objects.create(
            user=user,
            confirmed=True,
            secret_ciphertext=Fernet(settings.ACCOUNT_SECURITY_ENCRYPTION_KEY.encode())
            .encrypt(secret.encode())
            .decode(),
        )
        browser = Client()
        browser.post("/login/", {"email": user.email, "password": "Long-Password-For-MFA-8842!"})

        async def scenario():
            def connection():
                session_key = browser.cookies[settings.SESSION_COOKIE_NAME].value
                cookie = f"{settings.SESSION_COOKIE_NAME}={session_key}".encode()
                return WebsocketCommunicator(
                    application,
                    f"/ws/business/{business.slug}/orders/",
                    headers=[(b"cookie", cookie), (b"origin", b"http://localhost")],
                )

            partial = connection()
            accepted, _ = await partial.connect()
            self.assertFalse(accepted)
            await partial.disconnect()
            response = await sync_to_async(browser.post, thread_sensitive=True)(
                "/account/mfa/", {"code": pyotp.TOTP(secret).now()}
            )
            self.assertEqual(response.status_code, 302)
            verified = connection()
            accepted, _ = await verified.connect()
            self.assertTrue(accepted)
            await sync_to_async(browser.post, thread_sensitive=True)("/logout/")
            from channels.layers import get_channel_layer

            await get_channel_layer().group_send(f"business_{business.pk.hex}", {
                "type": "order.update", "event_id": "mfa-revocation-check",
                "event": "order.updated", "order_id": "test-order", "status": "new",
            })
            self.assertEqual((await verified.receive_output(timeout=3))["code"], 4403)
            await verified.disconnect()

        asyncio.run(scenario())
