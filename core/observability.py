import json
import logging
import re
import time
import uuid
from datetime import UTC, datetime

from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.http import require_safe
from redis import Redis

logger = logging.getLogger("veyra.operations")


class SafeJSONFormatter(logging.Formatter):
    """Only emit operational fields; never interpolate arbitrary messages or exceptions."""

    def format(self, record):
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", "application.log"),
        }
        for name in (
            "request_id",
            "method",
            "route",
            "status_code",
            "duration_ms",
            "event_id",
            "attempts",
        ):
            if hasattr(record, name):
                payload[name] = getattr(record, name)
        if record.exc_info and record.exc_info[0]:
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload)


class RequestContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        supplied = request.headers.get("X-Request-ID", "")
        request.request_id = (
            supplied if re.fullmatch(r"[0-9a-f]{32}", supplied) else uuid.uuid4().hex
        )
        started = time.monotonic()
        response = self.get_response(request)
        response["X-Request-ID"] = request.request_id
        match = getattr(request, "resolver_match", None)
        log = logger.error if response.status_code >= 500 else logger.info
        log(
            "HTTP request completed",
            extra={
                "event": "request.completed",
                "request_id": request.request_id,
                "method": request.method,
                "route": match.route if match else "unresolved",
                "status_code": response.status_code,
                "duration_ms": round((time.monotonic() - started) * 1000, 2),
            },
        )
        return response


@require_safe
def readiness(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        with Redis.from_url(
            settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2
        ) as client:
            client.ping()
    except Exception:
        logger.error(
            "Readiness dependency failure", extra={"event": "readiness.failed"}, exc_info=True
        )
        response = JsonResponse({"status": "unavailable"}, status=503)
    else:
        response = JsonResponse({"status": "ready"})
    response["Cache-Control"] = "no-store"
    return response
