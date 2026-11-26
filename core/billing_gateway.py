from datetime import timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit
from uuid import UUID

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .billing import apply_paid_invoice, billing_event, lock_subscription
from .models import BillingAttempt, BillingInvoice, BusinessSubscription
from .observability import logger
from .payments.midtrans import valid_notification_signature

SNAP_LIFETIME_HOURS = 24
SNAP_LIFETIME = timedelta(hours=SNAP_LIFETIME_HOURS)
RECHECK_INTERVAL = timedelta(minutes=3)


class BillingGatewayError(ValueError):
    pass


def platform_api_available():
    return bool(settings.VEYRA_BILLING_MIDTRANS_SERVER_KEY.strip()
                and settings.VEYRA_BILLING_MIDTRANS_ENV in {"sandbox", "production"})


def configured_time(value):
    try:
        parsed = parse_datetime(value)
    except ValueError:
        return None
    return parsed if parsed and timezone.is_aware(parsed) else None


def gateway_available():
    approval = settings.VEYRA_BILLING_MIDTRANS_APPROVAL_REFERENCE
    approved = configured_time(settings.VEYRA_BILLING_MIDTRANS_APPROVED_AT)
    secret = settings.VEYRA_BILLING_MIDTRANS_SIGNATURE_SECRET_KEY.strip()
    api_key = settings.VEYRA_BILLING_MIDTRANS_SERVER_KEY.strip()
    try:
        origin = urlsplit(settings.VEYRA_BILLING_PUBLIC_ORIGIN)
    except ValueError:
        return False
    return bool(
        platform_api_available() and secret and secret != api_key
        and approval and len(approval) <= 120 and approval.isascii() and approval.isprintable()
        and approved and approved <= timezone.now()
        and origin.scheme == "https" and origin.hostname and not origin.username
        and not origin.password and not (origin.path or origin.query or origin.fragment)
    )


def provider_host(service):
    environment = settings.VEYRA_BILLING_MIDTRANS_ENV
    return f"{service}.sandbox.midtrans.com" if environment == "sandbox" else f"{service}.midtrans.com"


def midtrans_request(method, url, **options):
    if not platform_api_available() or (method != "GET" and not gateway_available()):
        raise BillingGatewayError("Platform checkout is unavailable.")
    try:
        response = requests.request(
            method, url, auth=(settings.VEYRA_BILLING_MIDTRANS_SERVER_KEY, ""),
            timeout=(3, 6), allow_redirects=False, **options,
        )
        result = response.json()
        if method == "GET" and (response.status_code == 404 or result.get("status_code") == "404"):
            return None
        if not 200 <= response.status_code < 300 or not isinstance(result, dict):
            raise BillingGatewayError("The payment provider could not process the request.")
        return result
    except (requests.RequestException, AttributeError, ValueError) as error:
        if isinstance(error, BillingGatewayError):
            raise
        raise BillingGatewayError("Payment status is unavailable. Try again shortly.") from None


@transaction.atomic
def reserve_checkout(business, actor, invoice_id, *, now=None):
    now = now or timezone.now()
    subscription = lock_subscription(business.pk, actor)
    invoice = subscription.invoices.get(pk=invoice_id)
    if subscription.status == BusinessSubscription.Status.SUSPENDED:
        raise BillingGatewayError("Contact platform support to restore billing access.")
    if invoice.status != BillingInvoice.Status.OPEN or invoice.period_end <= now:
        raise BillingGatewayError("This invoice is closed for payment.")
    if not gateway_available():
        raise BillingGatewayError("Platform checkout is unavailable.")
    pending = invoice.attempts.filter(status=BillingAttempt.Status.PENDING).first()
    if pending:
        return pending
    return invoice.attempts.create(retry_after=now + RECHECK_INTERVAL)


def hosted_checkout(attempt_id, return_url):
    error = None
    with transaction.atomic():
        attempt = BillingAttempt.objects.select_for_update(of=("self",)).select_related("invoice").get(pk=attempt_id)
        if attempt.status != BillingAttempt.Status.PENDING or attempt.invoice.period_end <= timezone.now():
            raise BillingGatewayError("Review the latest invoice status before continuing.")
        anchor = attempt.checkout_requested_at or attempt.created_at
        if anchor + SNAP_LIFETIME <= timezone.now():
            raise BillingGatewayError("This payment session is waiting for provider reconciliation.")
        if attempt.checkout_url:
            return attempt.checkout_url
        # Even a timed-out request may have opened a hosted session.
        attempt.checkout_requested_at = timezone.now()
        attempt.save(update_fields=["checkout_requested_at"])
        try:
            url = request_hosted_url(attempt, return_url)
        except BillingGatewayError as exc:
            error = exc
        else:
            attempt.checkout_url = url
            attempt.save(update_fields=["checkout_url"])
            return url
    raise error


def request_hosted_url(attempt, return_url):
    result = midtrans_request("POST", f"https://{provider_host('app')}/snap/v1/transactions", json={
        "transaction_details": {"order_id": attempt.provider_reference, "gross_amount": int(attempt.invoice.total)},
        "callbacks": {"finish": return_url},
        "page_expiry": {"duration": SNAP_LIFETIME_HOURS, "unit": "hours"},
    })
    url = result.get("redirect_url")
    if not isinstance(url, str) or len(url) > 500:
        raise BillingGatewayError("The provider did not return a checkout link.")
    try:
        target = urlsplit(url)
    except ValueError:
        raise BillingGatewayError("The provider returned an invalid checkout link.") from None
    if target.scheme != "https" or target.netloc != provider_host("app") or not target.path.startswith("/snap/"):
        raise BillingGatewayError("The provider returned an invalid checkout link.")
    return url


def notification_attempt(payload):
    if not platform_api_available() or not isinstance(payload, dict):
        raise PermissionError
    secret = settings.VEYRA_BILLING_MIDTRANS_SIGNATURE_SECRET_KEY.strip()
    api_key = settings.VEYRA_BILLING_MIDTRANS_SERVER_KEY.strip()
    if secret == api_key:
        raise PermissionError
    if not secret and settings.VEYRA_BILLING_MIDTRANS_ALLOW_LEGACY_SIGNATURE:
        secret = api_key
    current = valid_notification_signature(payload, secret)
    cutover = configured_time(settings.VEYRA_BILLING_MIDTRANS_ROTATED_AT)
    expires = configured_time(settings.VEYRA_BILLING_MIDTRANS_PREVIOUS_SIGNATURE_UNTIL)
    now = timezone.now()
    previous = bool(
        secret and cutover and expires and cutover <= now < expires <= cutover + timedelta(days=30)
        and valid_notification_signature(payload, settings.VEYRA_BILLING_MIDTRANS_PREVIOUS_SIGNATURE_SECRET_KEY.strip())
    )
    if not current and not previous:
        logger.warning("Rejected platform payment signature", extra={"event": "billing.signature_rejected"})
        raise PermissionError
    reference = payload["order_id"]
    if not reference.startswith("VB-") or len(reference) != 35:
        raise ValueError("Unknown invoice payment.")
    attempt_id = UUID(hex=reference[3:])
    if not current and not BillingAttempt.objects.filter(pk=attempt_id, created_at__lt=cutover).exists():
        raise PermissionError
    return attempt_id


def verified_provider_status(attempt):
    return midtrans_request("GET", f"https://{provider_host('api')}/v2/{attempt.provider_reference}/status")


@transaction.atomic
def record_provider_status(attempt_id, result, *, now=None):
    now = now or timezone.now()
    attempt = BillingAttempt.objects.select_related("invoice__subscription").get(pk=attempt_id)
    subscription = lock_subscription(attempt.invoice.subscription.business_id)
    attempt = BillingAttempt.objects.select_for_update(of=("self",)).select_related("invoice__plan").get(pk=attempt_id)
    invoice = attempt.invoice
    if attempt.status != BillingAttempt.Status.PENDING:
        return
    if result is None:
        anchor = attempt.checkout_requested_at or attempt.created_at
        if anchor + SNAP_LIFETIME + RECHECK_INTERVAL > now:
            return
        code, transaction_id = "checkout_expired", ""
    else:
        try:
            amount = Decimal(str(result["gross_amount"]))
            valid = amount.is_finite() and amount == invoice.total
        except (KeyError, InvalidOperation):
            valid = False
        if (not valid or result.get("order_id") != attempt.provider_reference
                or result.get("currency", "IDR") != invoice.currency):
            raise BillingGatewayError("The provider response did not match the invoice.")
        transaction_id = result.get("transaction_id", "")
        if not isinstance(transaction_id, str) or len(transaction_id) > 90:
            raise BillingGatewayError("Invalid payment reference.")
        code = result.get("transaction_status")
        fraud = result.get("fraud_status")
        paid = code == "settlement" and fraud in (None, "accept") or code == "capture" and fraud == "accept"
        if paid:
            if not transaction_id:
                raise BillingGatewayError("The provider returned no payment reference.")
            if BillingAttempt.objects.exclude(pk=attempt.pk).filter(provider_transaction=transaction_id).exists():
                raise BillingGatewayError("The payment is already associated with another invoice.")
            attempt.provider_transaction = transaction_id
            attempt.status = BillingAttempt.Status.PAID
            attempt.save(update_fields=["status", "provider_transaction"])
            if invoice.status == BillingInvoice.Status.OPEN:
                apply_paid_invoice(subscription, invoice, now)
            else:
                billing_event(subscription, "payment.review_required", invoice=str(invoice.pk))
            return
        if code not in ("deny", "cancel", "expire", "failure") and fraud != "deny":
            return
        if fraud == "deny":
            code = "denied"
    attempt.failure_code = code
    attempt.status = BillingAttempt.Status.FAILED
    attempt.save(update_fields=["failure_code", "status"])
    billing_event(subscription, "payment.failed", invoice=str(invoice.pk), reason=code)


def reconcile_payment(attempt_id, *, from_webhook=False):
    attempt = BillingAttempt.objects.get(pk=attempt_id)
    if attempt.status != BillingAttempt.Status.PENDING:
        return
    try:
        result = verified_provider_status(attempt)
        anchor = attempt.checkout_requested_at or attempt.created_at
        if result is None and from_webhook and anchor + SNAP_LIFETIME + RECHECK_INTERVAL > timezone.now():
            raise BillingGatewayError("Payment notification is waiting for provider status.")
        record_provider_status(attempt_id, result)
    finally:
        BillingAttempt.objects.filter(pk=attempt_id, status=BillingAttempt.Status.PENDING).update(
            retry_after=timezone.now() + RECHECK_INTERVAL,
        )
