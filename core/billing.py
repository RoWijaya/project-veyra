from calendar import monthrange
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from .models import BillingAttempt, BillingInvoice, Business, BusinessSubscription, OutboxEvent, SubscriptionEvent

GRACE_PERIOD = timedelta(days=7)
STATE = BusinessSubscription.Status


def subscription_access(subscription, now):
    if subscription.status == STATE.GRANDFATHERED:
        return True
    if subscription.status == STATE.TRIAL:
        return bool(subscription.trial_ends_at and now < subscription.trial_ends_at)
    if subscription.status not in (STATE.ACTIVE, STATE.PAST_DUE):
        return False
    end = subscription.current_period_end
    if end is None:
        return subscription.status == STATE.ACTIVE
    if subscription.cancel_at_period_end:
        return now < end
    return now < end + GRACE_PERIOD


def selectable_plans():
    from .models import SubscriptionPlan

    return SubscriptionPlan.objects.filter(active=True, monthly_price__gt=0, currency="IDR").order_by("monthly_price", "code")


def legal_plan(plan):
    return plan.currency == "IDR" and plan.monthly_price > 0 and plan.monthly_price == plan.monthly_price.to_integral_value()


def plan_terms(plan, *, interval="month"):
    return {
        "code": plan.code, "name": plan.name, "monthly_price": str(plan.monthly_price),
        "currency": plan.currency, "interval": interval, "entitlements": plan.entitlements,
    }


def next_period(start, timezone_name):
    local = start.astimezone(ZoneInfo(timezone_name))
    year, month = (local.year + 1, 1) if local.month == 12 else (local.year, local.month + 1)
    return local.replace(year=year, month=month, day=min(local.day, monthrange(year, month)[1]))


def billing_event(subscription, action, *, actor=None, previous_status=None, previous_plan=None, **metadata):
    SubscriptionEvent.objects.create(
        subscription=subscription, actor=actor, event_type=action,
        from_status=previous_status or subscription.status, to_status=subscription.status,
        from_plan=previous_plan or subscription.plan.code, to_plan=subscription.plan.code, metadata=metadata,
    )
    OutboxEvent.objects.create(
        business=subscription.business, event_type="billing.update",
        payload={}, next_attempt_at=timezone.now(),
    )


def lock_subscription(business_id, actor=None):
    business = Business.objects.select_for_update().get(pk=business_id)
    if actor is not None and business.owner_id != actor.pk:
        raise PermissionError("Only the business owner can manage billing.")
    return BusinessSubscription.objects.select_for_update(of=("self",)).select_related(
        "business", "plan", "pending_plan"
    ).get(business=business)


def issue_invoice(subscription, plan, start, *, actor=None):
    if not legal_plan(plan):
        raise ValueError("Choose a published monthly IDR plan.")
    invoice = subscription.invoices.create(
        plan=plan, terms=plan_terms(plan), total=plan.monthly_price, currency=plan.currency,
        period_start=start, period_end=next_period(start, subscription.business.timezone_name),
        due_at=start + GRACE_PERIOD,
    )
    billing_event(subscription, "invoice.issued", actor=actor, invoice=str(invoice.pk))
    return invoice


@transaction.atomic
def select_plan(business, actor, code, *, now=None):
    now = now or timezone.now()
    subscription = lock_subscription(business.pk, actor)
    plan = selectable_plans().filter(code=code).first()
    if plan is None or not legal_plan(plan):
        raise ValueError("That plan is unavailable.")
    if subscription.status == STATE.SUSPENDED:
        raise ValueError("Contact platform support to restore billing access.")
    if subscription.cancel_at_period_end and subscription.current_period_end and subscription.current_period_end > now:
        raise ValueError("Resume renewal before scheduling a plan change.")
    outstanding = subscription.invoices.filter(status=BillingInvoice.Status.OPEN).first()
    if outstanding:
        if outstanding.plan_id == plan.pk and outstanding.period_end > now:
            return outstanding
        if outstanding.attempts.filter(status=BillingAttempt.Status.PENDING).exists():
            raise ValueError("Check the outstanding payment before choosing a different plan.")
        outstanding.status = BillingInvoice.Status.VOID
        outstanding.save(update_fields=["status"])
        billing_event(subscription, "invoice.closed", actor=actor, invoice=str(outstanding.pk))
    if subscription.status == STATE.ACTIVE and subscription.current_period_end and subscription.current_period_end > now:
        subscription.pending_plan = None if plan.pk == subscription.plan_id else plan
        subscription.save(update_fields=["pending_plan", "updated_at"])
        billing_event(subscription, "plan.scheduled" if subscription.pending_plan_id else "plan.schedule_cleared",
                      actor=actor, plan=plan.code, effective=subscription.current_period_end.isoformat())
        return None
    return issue_invoice(subscription, plan, now, actor=actor)


@transaction.atomic
def set_cancellation(business, actor, *, cancel, now=None):
    now = now or timezone.now()
    subscription = lock_subscription(business.pk, actor)
    if subscription.status != STATE.ACTIVE or not subscription.current_period_end or subscription.current_period_end <= now:
        raise ValueError("End-of-period cancellation is available during a paid period.")
    if cancel == subscription.cancel_at_period_end:
        return
    subscription.cancel_at_period_end = cancel
    if cancel:
        subscription.pending_plan = None
    subscription.save(update_fields=["cancel_at_period_end", "pending_plan", "updated_at"])
    billing_event(subscription, "cancellation.scheduled" if cancel else "cancellation.reversed", actor=actor)


@transaction.atomic
def advance_subscription(business_id, now):
    subscription = lock_subscription(business_id)
    previous = subscription.status
    end = subscription.current_period_end
    if previous == STATE.TRIAL and subscription.trial_ends_at and subscription.trial_ends_at <= now:
        subscription.status = STATE.EXPIRED
    elif previous == STATE.ACTIVE and end and end <= now:
        if subscription.cancel_at_period_end:
            subscription.status = STATE.CANCELED
            subscription.canceled_at = end
        else:
            plan = subscription.pending_plan or subscription.plan
            if now < end + GRACE_PERIOD and legal_plan(plan) and (plan.active or subscription.pending_plan is None):
                if not subscription.invoices.filter(status=BillingInvoice.Status.OPEN).exists():
                    issue_invoice(subscription, plan, end)
                subscription.status = STATE.PAST_DUE
            else:
                subscription.status = STATE.EXPIRED
    elif previous == STATE.PAST_DUE and end and end + GRACE_PERIOD <= now:
        subscription.status = STATE.EXPIRED
    if subscription.status != previous:
        subscription.save(update_fields=["status", "canceled_at", "updated_at"])
        billing_event(subscription, "subscription." + subscription.status, previous_status=previous)


def apply_paid_invoice(subscription, invoice, now):
    previous_status, previous_plan = subscription.status, subscription.plan.code
    invoice.status = BillingInvoice.Status.PAID
    invoice.paid_at = now
    invoice.save(update_fields=["status", "paid_at"])
    if subscription.status == STATE.SUSPENDED or invoice.period_end <= now:
        billing_event(subscription, "invoice.paid_review_required", invoice=str(invoice.pk))
        return
    subscription.plan = invoice.plan
    subscription.terms = invoice.terms
    subscription.status = STATE.ACTIVE
    subscription.current_period_start = invoice.period_start
    subscription.current_period_end = invoice.period_end
    subscription.cancel_at_period_end = False
    subscription.canceled_at = None
    subscription.pending_plan = None
    subscription.save()
    billing_event(subscription, "invoice.paid", previous_status=previous_status, previous_plan=previous_plan,
                  invoice=str(invoice.pk))


def billing_usage(business):
    return {
        "locations": business.locations.filter(active=True).count(),
        "employees": business.memberships.filter(active=True).exclude(user_id=business.owner_id).count(),
        "workspaces": business.stations.filter(active=True).count(),
        "products": business.products.filter(active=True).count(),
    }

@transaction.atomic
def restrict_subscription(business_id, actor, *, restrict):
    if not actor.is_active or not actor.has_perm("core.change_businesssubscription"):
        raise PermissionError("Only authorized platform staff can change a subscription restriction.")
    subscription = lock_subscription(business_id)
    previous = subscription.status
    if restrict and previous != STATE.SUSPENDED:
        subscription.status = STATE.SUSPENDED
        event = "platform.suspended"
    elif not restrict and previous == STATE.SUSPENDED:
        event = "platform.restored"
        prior = subscription.events.filter(event_type="platform.suspended").order_by("-created_at", "-pk").first()
        subscription.status = prior.from_status if prior else STATE.EXPIRED
    else:
        return False
    subscription.save(update_fields=["status", "updated_at"])
    billing_event(subscription, event, actor=actor, previous_status=previous)
    return True
