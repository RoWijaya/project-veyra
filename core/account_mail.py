import base64
import hashlib
import smtplib
import uuid
from datetime import timedelta
from urllib.parse import urlencode

import django
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core import signing
from django.core.mail import EmailMessage
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlsafe_base64_encode

from .email_backend import PermanentEmailError
from .models import AuditEvent, User, VerificationDelivery
from .observability import logger

VERIFICATION_TTL = 60 * 60 * 24
MAX_ATTEMPTS = 8
DELIVERY_LEASE = timedelta(minutes=2)


def send_account_email(subject, body, recipient, *, delivery_id=None):
    headers = {"X-Veyra-Delivery-ID": str(delivery_id)} if delivery_id else None
    message = EmailMessage(subject, body, settings.DEFAULT_FROM_EMAIL, [recipient], headers=headers)
    if django.VERSION >= (6, 1):
        return message.send(using="default")
    return message.send()


def redirect_cipher():
    keys = [settings.SECRET_KEY, *settings.SECRET_KEY_FALLBACKS]
    return MultiFernet([
        Fernet(base64.urlsafe_b64encode(hashlib.sha256(f"veyra.verification.next:{key}".encode()).digest()))
        for key in keys
    ])


@transaction.atomic
def queue_verification(*, user_id, origin, next_url, resend=False):
    pending = User.objects.select_for_update().filter(
        pk=user_id, is_active=False, email_verified_at__isnull=True,
    ).exists()
    if not pending:
        return None
    delivery = VerificationDelivery.objects.filter(user_id=user_id).first()
    if delivery and delivery.state in (VerificationDelivery.State.PENDING, VerificationDelivery.State.SENDING):
        return None
    now = timezone.now()
    delivery, _ = VerificationDelivery.objects.update_or_create(user_id=user_id, defaults={
        "origin": origin,
        "next_url": redirect_cipher().encrypt(next_url.encode()).decode() if next_url else "",
        "state": VerificationDelivery.State.PENDING,
        "generation": uuid.uuid4(),
        "accept_legacy_token": False,
        "claim_token": None,
        "requested_at": now,
        "next_attempt_at": now,
        "attempts": 0,
        "result": "",
    })
    if resend:
        AuditEvent.objects.create(actor_id=user_id, action="account.verification_resent")
    return delivery.pk


def in_memory_mail():
    if django.VERSION >= (6, 1):
        backend = settings.MAILERS["default"]["BACKEND"]
    else:
        backend = settings.EMAIL_BACKEND
    return backend == "django.core.mail.backends.locmem.EmailBackend"


def deliver_in_memory(pk):
    # Django's in-memory mailbox is process-local. SMTP and Resend always run in the worker.
    if pk and in_memory_mail():
        deliver_verification(pk)


def due_deliveries():
    return VerificationDelivery.objects.filter(
        state__in=(VerificationDelivery.State.PENDING, VerificationDelivery.State.SENDING),
        next_attempt_at__lte=timezone.now(),
    )


class VerificationSigner(signing.TimestampSigner):
    def __init__(self, requested_at):
        super().__init__(salt="veyra-email-verification")
        self.requested_at = requested_at

    def timestamp(self):
        return signing.b62_encode(int(self.requested_at.timestamp()))


def verification_link(delivery):
    token = VerificationSigner(delivery.requested_at).sign_object([delivery.user_id, str(delivery.generation)])
    uid = urlsafe_base64_encode(str(delivery.user_id).encode())
    path = reverse("verify_email", kwargs={"uidb64": uid, "token": token})
    if delivery.next_url:
        path += "?" + urlencode({"next": redirect_cipher().decrypt(delivery.next_url.encode()).decode()})
    return delivery.origin + path


def permanent_failure(error):
    if isinstance(error, (PermanentEmailError, InvalidToken)):
        return True
    if isinstance(error, smtplib.SMTPResponseException):
        return 500 <= error.smtp_code < 600
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        return bool(error.recipients) and all(500 <= response[0] < 600 for response in error.recipients.values())
    return False


def deliver_verification(pk):
    states = VerificationDelivery.State
    with transaction.atomic():
        job = due_deliveries().select_for_update(of=("self",)).select_related("user").filter(pk=pk).first()
        if not job:
            return
        now = timezone.now()
        if job.user.is_active or job.user.email_verified_at:
            job.state, job.result = states.CLOSED, "account_verified"
            job.save(update_fields=["state", "result"])
            return
        if job.attempts >= MAX_ATTEMPTS or now >= job.requested_at + timedelta(seconds=VERIFICATION_TTL):
            job.state, job.result = states.FAILED, "delivery_window_closed"
            job.save(update_fields=["state", "result"])
            return
        job.attempts += 1
        job.state = states.SENDING
        job.claim_token = uuid.uuid4()
        job.next_attempt_at = now + DELIVERY_LEASE
        job.save(update_fields=["attempts", "state", "claim_token", "next_attempt_at"])

    try:
        sent = send_account_email(
            "Verify your Veyra account", f"Confirm your email: {verification_link(job)}",
            job.user.email, delivery_id=job.generation,
        )
        if sent != 1:
            raise OSError("Email delivery not accepted")
    except (OSError, ValueError, InvalidToken) as error:
        terminal = permanent_failure(error) or job.attempts >= MAX_ATTEMPTS
        state = states.FAILED if terminal else states.PENDING
        result = "permanent_rejection" if permanent_failure(error) else "delivery_unavailable"
        delay = min(60 * 2 ** (job.attempts - 1), 3600)
        logger.warning("Verification mail could not be delivered", extra={
            "event": "account_mail.failed" if terminal else "account_mail.retry_scheduled",
            "attempts": job.attempts,
        }, exc_info=True)
    else:
        state, result, delay = states.SENT, "accepted", 0
    VerificationDelivery.objects.filter(pk=job.pk, claim_token=job.claim_token).update(
        state=state, result=result, claim_token=None,
        next_attempt_at=timezone.now() + timedelta(seconds=delay),
    )
