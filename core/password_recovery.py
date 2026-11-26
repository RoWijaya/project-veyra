"""Queue password reset email without an account-dependent response path."""

import hashlib
import uuid
from datetime import timedelta
from urllib.parse import urlsplit

from cryptography.fernet import InvalidToken
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.views import PasswordResetView
from django.core.cache import cache
from django.db import transaction
from django.http import HttpResponseRedirect
from django.template.loader import render_to_string
from django.utils import timezone
from redis.exceptions import RedisError

from .account_mail import DELIVERY_LEASE, MAX_ATTEMPTS, VERIFICATION_TTL, in_memory_mail, permanent_failure
from .account_mail import redirect_cipher, send_account_email
from .models import PasswordRecoveryDelivery
from .observability import logger


class QueuedPasswordResetView(PasswordResetView):
    def form_valid(self, form):
        email = form.cleaned_data["email"]
        ip = hashlib.sha256(self.request.META.get("REMOTE_ADDR", "unknown").encode()).hexdigest()
        recipient = hashlib.sha256(email.casefold().encode()).hexdigest()
        allowed = False
        try:
            cache.add(f"password-reset-ip:{ip}", 0, 600)
            allowed = cache.incr(f"password-reset-ip:{ip}") <= 8 and cache.add(
                f"password-reset-recipient:{recipient}", True, 600,
            )
        except RedisError:
            logger.warning("Recovery throttle unavailable", extra={"event": "account_recovery.throttle_unavailable"})
        if allowed:
            delivery = PasswordRecoveryDelivery.objects.create(
                recipient_ciphertext=redirect_cipher().encrypt(email.encode()).decode(),
                origin=self.request.build_absolute_uri("/").rstrip("/"),
            )
            if in_memory_mail():
                deliver_password_recovery(delivery.pk)
        return HttpResponseRedirect(self.get_success_url())


class DeliveryPasswordResetForm(PasswordResetForm):
    def __init__(self, email, delivery_id):
        super().__init__({"email": email})
        self.delivery_id = delivery_id

    def send_mail(self, subject_template_name, email_template_name, context, from_email, to_email, html_email_template_name=None):
        subject = "".join(render_to_string(subject_template_name, context).splitlines())
        body = render_to_string(email_template_name, context)
        if send_account_email(subject, body, to_email, delivery_id=self.delivery_id) != 1:
            raise OSError("Password recovery email was not accepted")


def due_password_recovery():
    return PasswordRecoveryDelivery.objects.filter(finished_at__isnull=True, next_attempt_at__lte=timezone.now())


def deliver_password_recovery(delivery_id):
    with transaction.atomic():
        delivery = due_password_recovery().select_for_update().filter(pk=delivery_id).first()
        if delivery is None:
            return
        now = timezone.now()
        if delivery.attempts >= MAX_ATTEMPTS or delivery.requested_at + timedelta(seconds=VERIFICATION_TTL) <= now:
            delivery.finished_at = now
            delivery.failed = True
            delivery.recipient_ciphertext = ""
            delivery.claim_token = None
            delivery.save(update_fields=["finished_at", "failed", "recipient_ciphertext", "claim_token"])
            return
        delivery.attempts += 1
        delivery.claim_token = uuid.uuid4()
        delivery.next_attempt_at = now + DELIVERY_LEASE
        delivery.save(update_fields=["attempts", "claim_token", "next_attempt_at"])

    claimed = PasswordRecoveryDelivery.objects.filter(pk=delivery.pk, claim_token=delivery.claim_token)
    try:
        email = redirect_cipher().decrypt(delivery.recipient_ciphertext.encode()).decode()
        form = DeliveryPasswordResetForm(email, delivery.pk)
        if not form.is_valid():
            raise InvalidToken
        origin = urlsplit(delivery.origin)
        form.save(domain_override=origin.netloc, use_https=origin.scheme == "https",
                  email_template_name="core/password_reset_email.html")
    except (OSError, ValueError, InvalidToken) as error:
        if not permanent_failure(error) and delivery.attempts < MAX_ATTEMPTS:
            delay = min(60 * 2 ** (delivery.attempts - 1), 3600)
            claimed.update(next_attempt_at=timezone.now() + timedelta(seconds=delay), claim_token=None)
            logger.warning("Recovery delivery delayed", extra={"event": "account_recovery.retry_scheduled"})
            return
        claimed.update(finished_at=timezone.now(), failed=True, recipient_ciphertext="", claim_token=None)
        logger.warning("Recovery delivery failed", extra={"event": "account_recovery.failed"})
    else:
        claimed.update(finished_at=timezone.now(), recipient_ciphertext="", claim_token=None)
