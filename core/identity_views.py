import hashlib
import io
import secrets
import time

import pyotp
import qrcode
from authlib.integrations.base_client.errors import OAuthError
from cryptography.fernet import InvalidToken
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST, require_safe
from joserfc.errors import JoseError
from qrcode.image.svg import SvgPathImage
from requests.exceptions import RequestException

from .identity import (
    account_cipher,
    consume_factor,
    google_client,
    local_next,
    primary_login,
    throttle,
)
from .models import AuditEvent, Authenticator, GoogleIdentity, User


def recent_primary(request):
    password = request.POST.get("password", "")
    if request.user.has_usable_password():
        return bool(password and request.user.check_password(password))
    return time.time() - request.session.get("primary_at", 0) < 300


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def account_security(request):
    error = None
    recovery_codes = []
    factor = Authenticator.objects.filter(user=request.user).first()
    if request.method == "POST":
        try:
            if not throttle(request.user.pk, "security"):
                raise ValueError("Too many attempts. Wait ten minutes and try again.")
            action = request.POST.get("action")
            if action == "begin":
                if factor and factor.confirmed:
                    raise ValueError("Your authenticator is already enabled.")
                if not recent_primary(request):
                    raise ValueError("Confirm your current password, or sign in again with Google.")
                secret = pyotp.random_base32()
                factor, _ = Authenticator.objects.update_or_create(
                    user=request.user,
                    defaults={
                        "secret_ciphertext": account_cipher().encrypt(secret.encode()).decode(),
                        "confirmed": False,
                        "last_counter": -1,
                    },
                )
                request.session["mfa_setup_at"] = time.time()
            elif action == "confirm":
                if (
                    not factor
                    or factor.confirmed
                    or time.time() - request.session.get("mfa_setup_at", 0) > 600
                ):
                    raise ValueError("Start authenticator setup again.")
                with transaction.atomic():
                    factor = consume_factor(request.user, request.POST.get("code", ""))
                    if factor.confirmed:
                        raise ValueError("Authenticator setup was already completed.")
                    recovery_codes = [secrets.token_hex(10) for _ in range(10)]
                    factor.confirmed = True
                    factor.recovery_digests = [
                        hashlib.sha256(code.encode()).hexdigest() for code in recovery_codes
                    ]
                    factor.save(update_fields=["confirmed", "recovery_digests"])
                    AuditEvent.objects.create(actor=request.user, action="account.mfa_enabled")
                request.session.cycle_key()
                request.session["mfa_version"] = str(factor.version)
                request.session.pop("mfa_setup_at", None)
            elif action == "disable":
                if not recent_primary(request):
                    raise ValueError("Confirm your current password, or sign in again with Google.")
                consume_factor(request.user, request.POST.get("code", ""))
                Authenticator.objects.filter(user=request.user).delete()
                request.session.pop("mfa_version", None)
                AuditEvent.objects.create(actor=request.user, action="account.mfa_disabled")
                messages.success(
                    request,
                    "Authenticator removed. Set up a new authenticator "
                    "if required for your account.",
                )
                return redirect("account_security")
            else:
                raise ValueError("Choose a supported security action.")
        except (ValueError, InvalidToken, Authenticator.DoesNotExist):
            error = "We could not verify that action. Check your password/code and try again."
    setup_secret = None
    if (
        factor
        and not factor.confirmed
        and time.time() - request.session.get("mfa_setup_at", 0) < 600
    ):
        try:
            setup_secret = account_cipher().decrypt(factor.secret_ciphertext.encode()).decode()
        except (ValueError, InvalidToken):
            error = "Authenticator setup is unavailable. Contact the platform administrator."
    return render(
        request,
        "core/account_security.html",
        {
            "factor": factor,
            "setup_secret": setup_secret,
            "error": error,
            "recovery_codes": recovery_codes,
            "google_connected": GoogleIdentity.objects.filter(user=request.user).exists(),
            "google_available": bool(
                settings.GOOGLE_CLIENT_ID
                and settings.GOOGLE_CLIENT_SECRET
                and settings.GOOGLE_REDIRECT_URI
            ),
        },
    )


@login_required
@never_cache
@require_safe
def authenticator_qr(request):
    factor = Authenticator.objects.filter(user=request.user, confirmed=False).first()
    if not factor or time.time() - request.session.get("mfa_setup_at", 0) > 600:
        return HttpResponse(status=404)
    secret = account_cipher().decrypt(factor.secret_ciphertext.encode()).decode()
    uri = pyotp.TOTP(secret).provisioning_uri(request.user.email, issuer_name="Veyra")
    output = io.BytesIO()
    qrcode.make(uri, image_factory=SvgPathImage).save(output)
    return HttpResponse(output.getvalue(), content_type="image/svg+xml")


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def mfa_challenge(request):
    if time.time() - request.session.get("primary_at", 0) > 600:
        logout(request)
        return redirect("login")
    error = None
    if request.method == "POST":
        try:
            if not throttle(request.user.pk, "challenge"):
                raise ValueError("Too many attempts. Wait ten minutes.")
            factor = consume_factor(request.user, request.POST.get("code", ""))
            if not factor.confirmed:
                raise ValueError("Authenticator is not enabled.")
            request.session.cycle_key()
            request.session["mfa_version"] = str(factor.version)
            AuditEvent.objects.create(actor=request.user, action="account.mfa_verified")
            return redirect(local_next(request, request.session.pop("mfa_next", "/app/")))
        except (ValueError, InvalidToken, Authenticator.DoesNotExist):
            error = "Invalid or already used code. Try a new authenticator code or recovery code."
    return render(request, "core/mfa_challenge.html", {"error": error})


@never_cache
@require_POST
def google_start(request):
    if not (
        settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET and settings.GOOGLE_REDIRECT_URI
    ):
        return HttpResponse("Google sign-in is not configured. Use email and password.", status=503)
    intent = request.POST.get("intent", "login")
    if intent == "link" and (not request.user.is_authenticated or not recent_primary(request)):
        return HttpResponse(
            "Sign in and confirm your current password before connecting Google.", status=403
        )
    if intent not in {"login", "link"}:
        return HttpResponse(status=400)
    request.session["google_intent"] = {
        "mode": intent,
        "user_id": request.user.pk if intent == "link" else None,
        "next": local_next(request, request.POST.get("next", "/app/")),
        "started": time.time(),
    }
    try:
        return google_client().authorize_redirect(
            request, settings.GOOGLE_REDIRECT_URI, prompt="select_account"
        )
    except (OAuthError, RequestException, ValueError):
        return HttpResponse(
            "Google is temporarily unavailable. Try again or use email sign-in.", status=503
        )


@never_cache
@require_safe
def google_callback(request):
    intent = request.session.pop("google_intent", None)
    try:
        if not intent or time.time() - intent["started"] > 600:
            raise ValueError("Expired authorization")
        token = google_client().authorize_access_token(request, leeway=30)
        claims = token.get("userinfo", {})
        subject, email = claims.get("sub"), claims.get("email", "").strip().lower()
        if (
            not isinstance(subject, str)
            or not subject
            or len(subject) > 255
            or not email
            or claims.get("email_verified") is not True
        ):
            raise ValueError("Unverified identity")
        with transaction.atomic():
            identity = GoogleIdentity.objects.select_related("user").filter(subject=subject).first()
            if intent["mode"] == "link":
                if not request.user.is_authenticated or request.user.pk != intent["user_id"]:
                    raise ValueError("Linking session changed")
                if identity and identity.user_id != request.user.pk:
                    raise ValueError("Identity belongs to another account")
                linked, _ = GoogleIdentity.objects.get_or_create(
                    user=request.user, defaults={"subject": subject, "email": email}
                )
                if linked.subject != subject:
                    raise ValueError("A different Google identity is already connected")
                AuditEvent.objects.create(actor=request.user, action="account.google_linked")
                return redirect("account_security")
            if identity:
                user = identity.user
                if not user.is_active:
                    raise ValueError("Account disabled")
            else:
                if User.objects.filter(email=email).exists():
                    raise ValueError("Sign in to the existing account before connecting Google")
                user = User.objects.create_user(
                    email, password=None, email_verified_at=timezone.now()
                )
                GoogleIdentity.objects.create(user=user, subject=subject, email=email)
            AuditEvent.objects.create(actor=user, action="account.google_sign_in")
        return primary_login(request, user, intent["next"])
    except (OAuthError, JoseError, RequestException, ValueError, IntegrityError, KeyError):
        return render(
            request,
            "core/auth.html",
            {
                "title": "Welcome back",
                "error": "Google sign-in could not be completed. Sign in with email "
                "to connect an existing account, or try Google again.",
            },
            status=400,
        )
