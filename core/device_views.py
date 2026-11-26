import hashlib
import json

from django.core.cache import cache
from django.db.models import F, Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import Order, PairedDevice, PrintJob, Product, Station
from .payments.midtrans import MidtransStatusUnavailable
from .services import (
    acknowledge_print_job,
    create_order,
    initiate_qris_payment,
    pair_station_device,
    process_payment_webhook,
    reprint_order_ticket,
    rotate_station_device_token,
    transition_order,
)


def _json_body(request):
    if len(request.body) > 16_384:
        raise ValueError("Request body is too large")
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("Send a valid JSON object") from error
    if not isinstance(body, dict):
        raise ValueError("Send a JSON object")
    return body


def _error(message, status):
    return JsonResponse({"error": message}, status=status, headers={"Cache-Control": "no-store"})


@csrf_exempt
@require_POST
def pair_device(request):
    ip = request.META.get("REMOTE_ADDR", "unknown")
    rate_key = "station-pair:" + hashlib.sha256(ip.encode()).hexdigest()
    cache.add(rate_key, 0, timeout=600)
    if cache.incr(rate_key) > 5:
        return _error("Too many pairing attempts", 429)
    try:
        data = _json_body(request)
        code = data.get("code")
        label = data.get("label")
        if not isinstance(code, str) or not isinstance(label, str) or not label.strip():
            raise ValueError("Pairing code and device label are required")
        if len(code) > 100 or len(label) > 120:
            raise ValueError("Pairing code or device label is too long")
        device, token = pair_station_device(code=code, label=label)
    except ValueError as error:
        return _error(str(error), 400)
    cache.delete(rate_key)
    return JsonResponse(
        {
            "device_token": token,
            "station": {
                "id": str(device.station_id),
                "name": device.station.name,
                "kind": device.station.kind,
                "capabilities": device.station.capabilities,
            },
        },
        status=201,
        headers={"Cache-Control": "no-store"},
    )


def _device(request, station_id):
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token or len(token) > 100:
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    device = (
        PairedDevice.objects.select_related("station", "station__business")
        .filter(
            Q(station__location__isnull=True) | Q(station__location__business_id=F("station__business_id")),
            station_id=station_id,
            token_digest=digest,
            revoked_at__isnull=True,
            station__active=True,
        )
        .first()
    )
    if device:
        PairedDevice.objects.filter(pk=device.pk, revoked_at__isnull=True).update(
            last_seen_at=timezone.now()
        )
    return device


def _require_capability(device, capability):
    return device is not None and capability in device.station.capabilities


@require_GET
def station_catalog(request, station_id):
    device = _device(request, station_id)
    if not device:
        return _error("Station session is invalid or revoked", 401)
    if not _require_capability(device, "catalog.view"):
        return _error("Station lacks catalog.view", 403)
    products = Product.objects.filter(business=device.station.business, active=True).order_by(
        "name"
    )
    from .modifiers import modifier_catalog

    catalog = modifier_catalog(
        business=device.station.business, product_ids=[product.pk for product in products],
        location=device.station.location,
    )
    return JsonResponse(
        {"products": [
            {
                "id": str(product.pk), "name": product.name, "price": str(product.price),
                "modifier_groups": [
                    {
                        "id": group.pk, "name": group.name, "min": group.min_selections, "max": group.max_selections,
                        "options": [
                            {"id": option.pk, "name": option.name, "surcharge": str(option.surcharge)}
                            for option in group.active_options
                        ],
                    }
                    for group in catalog[product.pk]
                ],
            }
            for product in products
        ]},
        headers={"Cache-Control": "no-store"},
    )


@require_GET
def station_orders(request, station_id):
    device = _device(request, station_id)
    if not device:
        return _error("Station session is invalid or revoked", 401)
    if not _require_capability(device, "orders.view"):
        return _error("Station lacks orders.view", 403)
    orders = Order.objects.filter(business=device.station.business)
    if device.station.location_id:
        orders = orders.filter(location_id=device.station.location_id)
    status = request.GET.get("status")
    if status:
        if status not in Order.Status.values:
            return _error("Unknown order status", 400)
        orders = orders.filter(status=status)
    orders = orders.prefetch_related("lines").order_by("created_at")[:100]
    return JsonResponse(
        {
            "orders": [
                {
                    "id": str(order.pk),
                    "status": order.status,
                    "order_type": order.order_type,
                    "total": str(order.total),
                    "note": order.note,
                    "created_at": order.created_at.isoformat(),
                    "items": [
                        {"name": line.product_name, "quantity": line.quantity, "modifiers": line.modifiers}
                        for line in order.lines.all()
                    ],
                }
                for order in orders
            ]
        },
        headers={"Cache-Control": "no-store"},
    )


@csrf_exempt
@require_POST
def station_create_order(request, station_id):
    device = _device(request, station_id)
    if not device:
        return _error("Station session is invalid or revoked", 401)
    if not _require_capability(device, "orders.create") or not _require_capability(
        device, "catalog.view"
    ):
        return _error("Station lacks order creation capabilities", 403)
    try:
        data = _json_body(request)
        items = data.get("items")
        if not isinstance(items, list) or not items or len(items) > 100:
            raise ValueError("Provide between 1 and 100 order items")
        if not data.get("idempotency_key"):
            raise ValueError("An idempotency key is required")
        order = create_order(
            business=device.station.business,
            actor=None,
            station_device=device,
            items=[(item["product_id"], item["quantity"], item.get("modifiers", [])) for item in items],
            idempotency_key=data["idempotency_key"],
            order_type=data.get("order_type", Order.OrderType.TAKEAWAY),
            table_label=data.get("table_label", ""),
            note=data.get("note", ""),
        )
    except (KeyError, TypeError, ValueError, Product.DoesNotExist) as error:
        return _error(str(error) or "Invalid order", 400)
    except PermissionError as error:
        return _error(str(error), 403)
    return JsonResponse(
        {"id": str(order.pk), "status": order.status, "total": str(order.total)}, status=201
    )


@csrf_exempt
@require_POST
def station_update_order(request, station_id, order_id):
    device = _device(request, station_id)
    if not device:
        return _error("Station session is invalid or revoked", 401)
    if not _require_capability(device, "orders.status.update") or not _require_capability(
        device, "orders.view"
    ):
        return _error("Station lacks order status capabilities", 403)
    try:
        data = _json_body(request)
        if not isinstance(data.get("status"), str):
            raise ValueError("Order status is required")
        order = transition_order(
            business=device.station.business,
            order_id=order_id,
            status=data["status"],
            station_device=device,
        )
    except Order.DoesNotExist:
        return _error("Order not found", 404)
    except (TypeError, ValueError) as error:
        return _error(str(error), 400)
    except PermissionError as error:
        return _error(str(error), 403)
    return JsonResponse({"id": str(order.pk), "status": order.status})


@csrf_exempt
@require_POST
def station_rotate_token(request, station_id):
    device = _device(request, station_id)
    if not device:
        return _error("Station session is invalid or revoked", 401)
    ip = request.META.get("REMOTE_ADDR", "unknown")
    rate_key = "station-rot:" + hashlib.sha256(f"{ip}:{device.pk}".encode()).hexdigest()
    cache.add(rate_key, 0, timeout=300)
    if cache.incr(rate_key) > 10:
        return _error("Too many token rotation attempts", 429)
    try:
        new_token = rotate_station_device_token(station_id=station_id, device=device)
    except PermissionError as error:
        return _error(str(error), 403)
    return JsonResponse(
        {"device_token": new_token, "status": "rotated"},
        headers={"Cache-Control": "no-store"},
    )


@require_GET
def station_print_jobs(request, station_id):
    device = _device(request, station_id)
    if not device:
        return _error("Station session is invalid or revoked", 401)
    if not _require_capability(device, "orders.view"):
        return _error("Station lacks orders.view", 403)
    status_filter = request.GET.get("status", PrintJob.Status.PENDING)
    jobs = PrintJob.objects.filter(
        business=device.station.business,
        station=device.station,
    )
    if device.station.location_id:
        jobs = jobs.filter(order__location_id=device.station.location_id)
    if status_filter != "all":
        jobs = jobs.filter(status=status_filter)
    jobs = jobs.order_by("-created_at")[:50]
    return JsonResponse(
        {
            "jobs": [
                {
                    "id": str(j.pk),
                    "order_id": str(j.order_id),
                    "job_type": j.job_type,
                    "status": j.status,
                    "ticket_payload": j.ticket_payload,
                    "created_at": j.created_at.isoformat(),
                    "printed_at": j.printed_at.isoformat() if j.printed_at else None,
                }
                for j in jobs
            ]
        },
        headers={"Cache-Control": "no-store"},
    )


@csrf_exempt
@require_POST
def station_ack_print_job(request, station_id, job_id):
    device = _device(request, station_id)
    if not device:
        return _error("Station session is invalid or revoked", 401)
    if not _require_capability(device, "orders.view"):
        return _error("Station lacks orders.view", 403)
    try:
        data = _json_body(request)
        status = data.get("status")
        error = data.get("error", "")
        if status not in (PrintJob.Status.PRINTED, PrintJob.Status.FAILED, PrintJob.Status.PRINTING):
            raise ValueError("Provide a valid print job status")
        job = acknowledge_print_job(
            business=device.station.business,
            job_id=job_id,
            status=status,
            error=error,
            station_device=device,
        )
    except PrintJob.DoesNotExist:
        return _error("Print job not found", 404)
    except (ValueError, TypeError) as error:
        return _error(str(error), 400)
    except PermissionError as error:
        return _error(str(error), 403)
    return JsonResponse({"id": str(job.pk), "status": job.status})


@csrf_exempt
@require_POST
def station_reprint_order(request, station_id, order_id):
    device = _device(request, station_id)
    if not device:
        return _error("Station session is invalid or revoked", 401)
    if not _require_capability(device, "orders.view"):
        return _error("Station lacks orders.view", 403)
    try:
        job = reprint_order_ticket(
            business=device.station.business,
            order_id=order_id,
            station_id=station_id,
            station_device=device,
        )
    except Order.DoesNotExist:
        return _error("Order not found", 404)
    except (ValueError, TypeError) as error:
        return _error(str(error), 400)
    except PermissionError as error:
        return _error(str(error), 403)
    return JsonResponse({"print_job_id": str(job.pk), "status": "queued"})


@csrf_exempt
@require_POST
def station_initiate_payment(request, station_id, order_id):
    device = _device(request, station_id)
    if not device:
        return _error("Station session is invalid or revoked", 401)
    if not _require_capability(device, "orders.view"):
        return _error("Station lacks orders.view", 403)
    try:
        data = _json_body(request)
        provider = data.get("provider")
        if not provider:
            raise ValueError("Payment provider is required")
        tx = initiate_qris_payment(
            business=device.station.business,
            order_id=order_id,
            provider=provider,
            station_device=device,
        )
    except Order.DoesNotExist:
        return _error("Order not found", 404)
    except (ValueError, TypeError) as error:
        return _error(str(error), 400)
    except PermissionError as error:
        return _error(str(error), 403)
    return JsonResponse(
        {
            "id": str(tx.pk),
            "status": tx.status,
            "provider": tx.provider,
            "amount": str(tx.amount),
            "currency": tx.currency,
            "qr_payload": tx.qr_payload,
            "qr_image_url": tx.qr_image_url,
            "expires_at": tx.expires_at.isoformat(),
        },
        status=201,
        headers={"Cache-Control": "no-store"},
    )


@csrf_exempt
@require_POST
def payment_webhook(request, provider):
    ip = request.META.get("REMOTE_ADDR", "unknown")
    rate_key = "pay-hook:" + hashlib.sha256(f"{ip}:{provider}".encode()).hexdigest()
    cache.add(rate_key, 0, timeout=60)
    if cache.incr(rate_key) > 120:
        return _error("Too many webhook requests", 429)

    try:
        tx = process_payment_webhook(
            provider=provider,
            headers=dict(request.headers),
            raw_body=request.body,
        )
    except PermissionError as error:
        return _error(str(error), 401)
    except MidtransStatusUnavailable:
        return _error("Payment verification is temporarily unavailable.", 503)
    except ValueError as error:
        return _error(str(error), 400)
    except Exception:
        return _error("Processing error", 500)

    return JsonResponse({"status": "ok", "transaction_status": tx.status})
