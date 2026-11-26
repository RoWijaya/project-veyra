import uuid
from decimal import Decimal

from django import forms

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef
from django.http import Http404, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_datetime
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .models import Order, OrderRefund, PaymentTransaction, PrintJob, Station
from .payments.midtrans import MidtransStatusUnavailable
from .refunds import (
    can_manage_refunds, reconcile_midtrans_refund, refund_capture, refundable_amount,
    refund_total, reserve_refund,
)
from .page_errors import access_restricted
from .receipts import queue_receipt
from .services import format_receipt
from .views import membership_for


@login_required
@never_cache
@require_http_methods(["GET"])
def order_history(request, slug):
    membership = membership_for(request, slug)
    if not membership.allows("orders.view"):
        return access_restricted(request, "Your role does not include viewing orders.")
    orders = membership.scope_locations(membership.business.orders).select_related("location").annotate(
        qris_blocks_tender=Exists(PaymentTransaction.objects.filter(
            order=OuterRef("pk"), status__in=PaymentTransaction.TENDER_BLOCKING_STATUSES,
        )),
        qris_needs_review=Exists(PaymentTransaction.objects.filter(
            order=OuterRef("pk"), status=PaymentTransaction.Status.REVIEW,
        )),
    ).order_by("-created_at", "-pk")
    page = Paginator(orders, 25).get_page(request.GET.get("page"))
    can_manage_orders = membership.allows("orders.manage")
    if can_manage_orders:
        for order in page:
            order.payment_key = uuid.uuid4()
    return render(request, "core/order_history.html", {
        "business": membership.business, "page_obj": page, "orders": page.object_list,
        "total_count": page.paginator.count, "pagination_query": "", "can_manage_orders": can_manage_orders,
    })


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def order_receipt(request, slug, order_id):
    membership = membership_for(request, slug)
    json_response = request.GET.get("format") == "json"
    if not membership.allows("orders.view"):
        if json_response:
            return HttpResponseForbidden("Your role does not include viewing receipts.")
        return access_restricted(request, "Your role does not include viewing receipts.")
    business = membership.business
    order = get_object_or_404(
        membership.scope_locations(business.orders).select_related("location", "created_by", "tender__recorded_by"),
        pk=order_id, payment_status=Order.PaymentStatus.PAID,
    )
    error = None
    status = 200
    if request.method == "POST":
        if not membership.allows("orders.manage"):
            if json_response:
                return HttpResponseForbidden("Your role does not include printing receipts.")
            return access_restricted(request, "Your role does not include printing receipts.")
        try:
            job, created = queue_receipt(
                business=business, order_id=order.pk, actor=request.user,
                station_id=request.POST.get("station"),
                idempotency_key=request.POST.get("idempotency_key"),
                action=request.POST.get("action"),
            )
        except Order.DoesNotExist:
            raise Http404 from None
        except (ValueError, Station.DoesNotExist):
            error = "The receipt could not be queued. Check the printer and current print status."
            status = 409
        except PermissionError:
            if json_response:
                return HttpResponseForbidden("Your role does not include printing receipts.")
            return access_restricted(request, "Your role does not include printing receipts.")
        else:
            if json_response:
                return JsonResponse({"job": str(job.pk), "status": job.status}, status=201 if created else 200)
            messages.success(request, "Receipt queued. Waiting for the printer to acknowledge it.")
            return redirect("order_receipt", slug=slug, order_id=order.pk)
    jobs = list(order.print_jobs.filter(job_type=PrintJob.JobType.RECEIPT).select_related("printer").order_by("-created_at"))
    receipt = dict(jobs[-1].ticket_payload) if jobs else format_receipt(order=order)
    receipt.setdefault("promotion_discount", receipt.get("discount", "0.00"))
    if json_response:
        history = [
            {"id": str(job.pk), "status": job.status, "printer": job.printer.name if job.printer else "Removed printer",
             "created_at": job.created_at.isoformat(), "error": job.last_error,
             "acknowledged_at": job.printed_at.isoformat() if job.printed_at else None}
            for job in jobs
        ]
        return JsonResponse({"receipt": receipt, "history": history, "error": error}, status=status)
    stations = Station.objects.select_related("printer").filter(
        business=business, active=True, location_id=order.location_id,
        printer__business=business, printer__is_active=True,
    ).order_by("name")
    stations = [station for station in stations if "orders.view" in station.capabilities
                and station.printer.location_id in {None, order.location_id}
                and station.printer.paper_width in {"58mm", "80mm"}]
    latest = jobs[0] if jobs else None
    can_queue = membership.allows("orders.manage") and (not latest or latest.status not in {"pending", "printing"})
    action = "print" if not latest else "reprint" if latest.status == "printed" else "retry"
    refunded = refund_total(order.refunds.filter(status=OrderRefund.Status.SUCCEEDED))
    return render(request, "core/receipt.html", {
        "refunded": refunded, "retained": order.total - refunded,
        "business": business, "order": order, "receipt": receipt, "history": jobs,
        "receipt_created": parse_datetime(receipt.get("created_at") or "") or order.created_at,
        "stations": stations, "can_queue": can_queue, "can_print": membership.allows("orders.manage"),
        "action": action, "idempotency_key": uuid.uuid4(), "error": error,
        "width": "58" if request.GET.get("width") == "58" else "80",
    }, status=status)


class RefundForm(forms.Form):
    amount = forms.DecimalField(label="Refund amount (Rp)", min_value=Decimal("0.01"), max_digits=12, decimal_places=2)
    reason = forms.CharField(max_length=200, widget=forms.Textarea(attrs={"rows": 2}))
    reference = forms.CharField(label="Card terminal refund reference", required=False, max_length=120)
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def order_refunds(request, slug, order_id):
    member = membership_for(request, slug)
    if not member.allows("orders.view"):
        raise PermissionDenied
    order = get_object_or_404(
        member.scope_locations(member.business.orders), pk=order_id, payment_status=Order.PaymentStatus.PAID,
    )
    can_manage = can_manage_refunds(member)
    tender, payment = refund_capture(order)
    form = RefundForm(initial={"idempotency_key": uuid.uuid4()})
    status = 200
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied
        action = request.POST.get("action")
        if action in ("verify", "retry"):
            try:
                record_id = uuid.UUID(request.POST.get("refund", ""))
            except ValueError:
                raise Http404 from None
            record = get_object_or_404(order.refunds.filter(payment__isnull=False), pk=record_id)
            records_pending = record.status == OrderRefund.Status.PENDING
            if records_pending:
                try:
                    reconcile_midtrans_refund(record, submit=action == "retry")
                except MidtransStatusUnavailable:
                    messages.warning(request, "The provider could not verify the refund. The reservation is still open.")
            return redirect("order_refunds", slug=slug, order_id=order_id)
        if action != "refund":
            raise Http404
        form = RefundForm(request.POST)
        status = 400
        if form.is_valid():
            try:
                refund, created = reserve_refund(
                    business=member.business, actor=request.user, order_id=order_id, **form.cleaned_data,
                )
            except ValueError as error:
                form.add_error(None, str(error))
                status = 409
            except PermissionError:
                raise PermissionDenied from None
            except Order.DoesNotExist:
                raise Http404 from None
            else:
                if created and refund.payment_id:
                    try:
                        reconcile_midtrans_refund(refund, submit=True)
                    except MidtransStatusUnavailable:
                        messages.warning(request, "The provider could not verify the refund. The reservation is still open.")
                else:
                    messages.success(request, "Refund recorded." if created else "This refund was already recorded.")
                return redirect("order_refunds", slug=slug, order_id=order_id)
    card = tender and tender.method == "card"
    if not card:
        form.fields["reference"].widget = forms.HiddenInput()
    remaining = refundable_amount(order, tender, payment)
    records = order.refunds.select_related("actor")
    refunded = refund_total(records.filter(status=OrderRefund.Status.SUCCEEDED))
    pending = refund_total(records.filter(status=OrderRefund.Status.PENDING))
    return render(request, "core/order_refunds.html", {
        "business": member.business, "order": order, "form": form, "page_obj": Paginator(records, 20).get_page(request.GET.get("page")),
        "can_manage": can_manage, "capture_available": bool(tender or payment),
        "is_card": card, "is_qris": bool(payment), "remaining": remaining, "refunded": refunded, "pending": pending,
    }, status=status)
