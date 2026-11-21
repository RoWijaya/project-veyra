import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .models import Order, PrintJob, Station
from .receipts import queue_receipt
from .services import format_receipt
from .views import membership_for


@login_required
@never_cache
@require_http_methods(["GET"])
def order_history(request, slug):
    membership = membership_for(request, slug)
    if not membership.allows("orders.view"):
        return HttpResponseForbidden("Your role does not include viewing orders.")
    orders = membership.business.orders.select_related("location").order_by("-created_at", "-pk")
    page = Paginator(orders, 25).get_page(request.GET.get("page"))
    return render(request, "core/order_history.html", {
        "business": membership.business, "page_obj": page, "orders": page.object_list,
        "total_count": page.paginator.count, "pagination_query": "",
    })


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def order_receipt(request, slug, order_id):
    membership = membership_for(request, slug)
    if not membership.allows("orders.view"):
        return HttpResponseForbidden("Your role does not include viewing receipts.")
    business = membership.business
    order = get_object_or_404(
        Order.objects.select_related("business", "location", "created_by", "tender__recorded_by"),
        business=business, pk=order_id, payment_status=Order.PaymentStatus.PAID,
    )
    json_response = request.GET.get("format") == "json"
    error = None
    status = 200
    if request.method == "POST":
        if not membership.allows("orders.manage"):
            return HttpResponseForbidden("Your role does not include printing receipts.")
        try:
            job, created = queue_receipt(
                business=business, order_id=order.pk, actor=request.user,
                station_id=request.POST.get("station"),
                idempotency_key=request.POST.get("idempotency_key"),
                action=request.POST.get("action"),
            )
        except (ValueError, Station.DoesNotExist):
            error = "The receipt could not be queued. Check the printer and current print status."
            status = 409
        except PermissionError:
            return HttpResponseForbidden("Your role does not include printing receipts.")
        else:
            if json_response:
                return JsonResponse({"job": str(job.pk), "status": job.status}, status=201 if created else 200)
            messages.success(request, "Receipt queued. Waiting for the printer to acknowledge it.")
            return redirect("order_receipt", slug=slug, order_id=order.pk)
    jobs = list(order.print_jobs.filter(job_type=PrintJob.JobType.RECEIPT).select_related("printer").order_by("-created_at"))
    receipt = dict(jobs[-1].ticket_payload) if jobs else format_receipt(order=order)
    receipt.setdefault("promotion_discount", receipt.get("discount", "0.00"))
    history = [
        {"id": str(job.pk), "status": job.status, "printer": job.printer.name if job.printer else "Removed printer",
         "created_at": job.created_at.isoformat(), "error": job.last_error,
         "acknowledged_at": job.printed_at.isoformat() if job.printed_at else None}
        for job in jobs
    ]
    if json_response:
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
    return render(request, "core/receipt.html", {
        "business": business, "order": order, "receipt": receipt, "history": history,
        "stations": stations, "can_queue": can_queue, "can_print": membership.allows("orders.manage"),
        "action": action, "idempotency_key": uuid.uuid4(), "error": error,
        "width": "58" if request.GET.get("width") == "58" else "80",
    }, status=status)
