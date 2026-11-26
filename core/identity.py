import hashlib
import secrets
import time

import pyotp
from authlib.integrations.django_client import OAuth
from channels.db import database_sync_to_async
from cryptography.fernet import Fernet, MultiFernet
from django.conf import settings
from django.contrib.auth import login
from django.core.cache import cache
from django.db import transaction
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme

from .models import AuditEvent, Authenticator, Business, User


def google_client():
    oauth = OAuth()
    return oauth.register(
        "google",
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={
            "scope": "openid email profile",
            "code_challenge_method": "S256",
            "timeout": 10,
        },
    )


def account_cipher():
    key = settings.ACCOUNT_SECURITY_ENCRYPTION_KEY
    if not key:
        raise ValueError(
            "Authenticator encryption is not configured. Contact the platform administrator."
        )
    return MultiFernet([Fernet(value.encode()) for value in [key, *settings.ACCOUNT_SECURITY_ENCRYPTION_FALLBACK_KEYS]])


def local_next(request, value):
    return (
        value
        if url_has_allowed_host_and_scheme(
            value, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        )
        else "/app/"
    )


def primary_login(request, user, next_url="/app/"):
    request.session.pop("mfa_version", None)
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    request.session["primary_at"] = time.time()
    request.session["mfa_next"] = local_next(request, next_url)
    if Authenticator.objects.filter(user=user, confirmed=True).exists():
        return redirect("mfa_challenge")
    if privileged_required(user):
        return redirect("account_security")
    return redirect(local_next(request, next_url))


def privileged_required(user):
    return settings.MFA_REQUIRE_PRIVILEGED and (
        user.is_staff or user.is_superuser or Business.objects.filter(owner=user).exists()
    )


def throttle(user_id, purpose):
    key = f"identity:{purpose}:{user_id}"
    cache.add(key, 0, 600)
    return cache.incr(key) <= 8


@transaction.atomic
def consume_factor(user, code):
    factor = Authenticator.objects.select_for_update().get(user=user)
    code = code.strip()
    digest = hashlib.sha256(code.encode()).hexdigest()
    for saved in factor.recovery_digests:
        if factor.confirmed and secrets.compare_digest(saved, digest):
            factor.recovery_digests.remove(saved)
            factor.save(update_fields=["recovery_digests"])
            AuditEvent.objects.create(actor=user, action="account.recovery_code_used")
            return factor
    if len(code) != 6 or not code.isascii() or not code.isdigit():
        raise ValueError(
            "Invalid or already used code. Try a new authenticator code or recovery code."
        )
    totp = pyotp.TOTP(account_cipher().decrypt(factor.secret_ciphertext.encode()).decode())
    current = int(time.time()) // 30
    for counter in (current - 1, current, current + 1):
        if counter > factor.last_counter and secrets.compare_digest(totp.at(counter * 30), code):
            factor.last_counter = counter
            factor.save(update_fields=["last_counter"])
            return factor
    raise ValueError("Invalid or already used code. Try a new authenticator code or recovery code.")


class MFAEnforcementMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            factor = Authenticator.objects.filter(user=request.user, confirmed=True).first()
            if factor and request.session.get("mfa_version") != str(factor.version):
                if request.path not in {"/account/mfa/", "/logout/"}:
                    request.session["mfa_next"] = local_next(request, request.get_full_path())
                    return redirect("mfa_challenge")
            elif not factor and privileged_required(request.user):
                if request.path not in {"/account/security/", "/account/mfa/qr.svg", "/logout/"}:
                    return redirect("account_security")
        return self.get_response(request)


class MFAWebsocketMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        user = scope.get("user")
        if user and user.is_authenticated:

            @database_sync_to_async
            def allowed():
                current_user = User.objects.filter(pk=user.pk, is_active=True).first()
                if current_user is None:
                    return False
                current_session = scope["session"].load()
                if str(current_user.pk) != current_session.get(
                    "_auth_user_id"
                ) or not secrets.compare_digest(
                    current_session.get("_auth_user_hash", ""), current_user.get_session_auth_hash()
                ):
                    return False
                factor = Authenticator.objects.filter(user=user, confirmed=True).first()
                if factor:
                    return current_session.get("mfa_version") == str(factor.version)
                return not privileged_required(current_user)

            scope["veyra_session_authorized"] = allowed
            if not await allowed():
                await send({"type": "websocket.close", "code": 4403})
                return
            closed = False

            async def protected_send(message):
                nonlocal closed
                if closed:
                    return
                if message["type"] == "websocket.send" and not await allowed():
                    closed = True
                    await send({"type": "websocket.close", "code": 4403})
                    return
                await send(message)

            return await self.app(scope, receive, protected_send)
        return await self.app(scope, receive, send)
