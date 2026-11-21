import json
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from .billing import GRACE_PERIOD, billing_usage, legal_plan, select_plan, selectable_plans, set_cancellation
from .billing_gateway import (
    BillingGatewayError, gateway_available, hosted_checkout, notification_attempt, reconcile_payment, reserve_checkout,
)
from .models import BillingAttempt, BillingInvoice, Business, BusinessSubscription

FEATURES = {
    "catalog": "Product catalog", "pos": "Point of sale", "qr_ordering": "QR ordering",
    "inventory": "Inventory and purchasing", "promotions": "Promotions and vouchers",
    "analytics": "Sales reports", "payments": "Merchant payments", "stations": "Stations and devices",
    "team": "Team access", "*": "All operational features",
}


def private_page(request, template, context, *, status=200):
    response = render(request, template, context, status=status)
    response["Cache-Control"] = "private, no-store"
    return response


def payment_return(request, business, invoice_id):
    path = reverse("billing_invoice", args=[business.slug, invoice_id])
    origin = settings.VEYRA_BILLING_PUBLIC_ORIGIN
    if settings.DEBUG and not origin:
        return request.build_absolute_uri(path)
    target = urlsplit(origin)
    if target.scheme != "https" or not target.hostname or target.username or target.password or target.path or target.query or target.fragment:
        raise BillingGatewayError("Platform billing needs a secure public return address.")
    return origin + path


@login_required
@require_http_methods(["GET", "POST"])
def subscription_billing(request, slug):
    business = get_object_or_404(Business, slug=slug, owner=request.user)
    subscription = BusinessSubscription.objects.select_related("plan", "pending_plan").filter(business=business).first()
    if subscription is None:
        return private_page(request, "core/billing.html", {"business": business})
    now = timezone.now()
    error = ""
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "select":
                invoice = select_plan(business, request.user, request.POST.get("plan", "")[:100])
                if invoice:
                    return redirect("billing_invoice", slug=slug, invoice_id=invoice.pk)
                messages.success(request, "Plan choice saved for the next billing period.")
            elif action in ("cancel", "resume"):
                set_cancellation(business, request.user, cancel=action == "cancel")
                messages.success(request, "Cancellation scheduled." if action == "cancel" else "Renewal resumed.")
            else:
                raise ValueError("Choose a billing action.")
        except PermissionError:
            raise Http404 from None
        except ValueError as exc:
            error = str(exc)
        else:
            return redirect("subscription_billing", slug=slug)

    term = subscription.terms
    limits = term.get("entitlements", subscription.plan.entitlements).get("limits", {})
    plans = []
    for plan in selectable_plans():
        if legal_plan(plan):
            plans.append({
                "plan": plan, "features": [FEATURES.get(feature, feature) for feature in plan.entitlements.get("features", [])],
                "limits": plan.entitlements.get("limits", {}),
            })
    plan_code = request.GET.get("plan", "")[:100]
    restricted = subscription.status == BusinessSubscription.Status.SUSPENDED
    confirm_plan = next((item["plan"] for item in plans if item["plan"].code == plan_code and not restricted), None)
    can_schedule = bool(subscription.status == "active" and subscription.current_period_end and subscription.current_period_end > now)
    usage = [(key.capitalize(), amount, limits.get(key)) for key, amount in billing_usage(business).items()]
    past_boundary = bool(subscription.current_period_end and subscription.current_period_end <= now)
    label = subscription.get_status_display()
    if subscription.status == "trial" and subscription.trial_ends_at and subscription.trial_ends_at <= now:
        label = "Expired"
    elif subscription.status == "active" and past_boundary:
        label = "Canceled" if subscription.cancel_at_period_end else "Past due"
    if label == "Past due" and subscription.current_period_end and subscription.current_period_end + GRACE_PERIOD <= now:
        label = "Expired"
    grace_until = subscription.current_period_end + GRACE_PERIOD if label == "Past due" and subscription.current_period_end else None
    return private_page(request, "core/billing.html", {
        "business": business, "subscription": subscription, "term": term,
        "label": label, "grace_until": grace_until, "usage": usage, "plans": plans, "confirm_plan": confirm_plan,
        "can_schedule": can_schedule, "restricted": restricted, "error": error,
        "invoices": Paginator(subscription.invoices.all(), 25).get_page(request.GET.get("page")),
    }, status=400 if error else 200)


@login_required
@require_http_methods(["GET", "POST"])
def billing_invoice(request, slug, invoice_id):
    business = get_object_or_404(Business, slug=slug, owner=request.user)
    invoice = get_object_or_404(BillingInvoice.objects.select_related("subscription"), subscription__business=business, pk=invoice_id)
    error = ""
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "checkout":
                if not gateway_available():
                    raise BillingGatewayError("Platform checkout is unavailable.")
                return_url = payment_return(request, business, invoice_id)
                attempt = reserve_checkout(business, request.user, invoice_id)
                return redirect(hosted_checkout(attempt.pk, return_url))
            if action != "check":
                raise BillingGatewayError("Choose a payment action.")
            attempt = invoice.attempts.filter(status=BillingAttempt.Status.PENDING).first()
            if attempt:
                reconcile_payment(attempt.pk)
            return redirect("billing_invoice", slug=slug, invoice_id=invoice_id)
        except PermissionError:
            raise Http404 from None
        except ValueError as exc:
            error = str(exc)
            invoice.refresh_from_db()
    return private_page(request, "core/billing_invoice.html", {
        "business": business, "invoice": invoice, "attempt": invoice.attempts.first(), "error": error,
        "payable": invoice.status == BillingInvoice.Status.OPEN and invoice.period_end > timezone.now()
                   and invoice.subscription.status != BusinessSubscription.Status.SUSPENDED,
        "gateway": gateway_available(),
    }, status=400 if error else 200)


@csrf_exempt
@require_POST
def billing_notification(request):
    try:
        if len(request.body) > 16000:
            return HttpResponse(status=413)
        attempt_id = notification_attempt(json.loads(request.body))
    except PermissionError:
        return HttpResponse(status=403)
    except (ValueError, UnicodeDecodeError):
        return HttpResponse(status=400)
    try:
        reconcile_payment(attempt_id, from_webhook=True)
    except BillingAttempt.DoesNotExist:
        return HttpResponse(status=404)
    except BillingGatewayError:
        return HttpResponse(status=503)
    return JsonResponse({"status": "accepted"})
