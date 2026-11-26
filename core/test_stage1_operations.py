import asyncio
from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from uuid import uuid4

from channels.testing import HttpCommunicator, WebsocketCommunicator
from django.conf import settings
from django.core.handlers.asgi import ASGIHandler
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from redis.exceptions import ConnectionError
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from config.asgi import application

from .models import AuditEvent, OutboxEvent
from .services import MAX_OUTBOX_ATTEMPTS, dispatch_outbox_event
from .test_audit_remediation import ShopFixture
from .workers import heartbeat_key, redis_client


@override_settings(ALLOWED_HOSTS=["stage.example.invalid"], SECURE_SSL_REDIRECT=True)
class ProxyBoundaryTests(SimpleTestCase):
    def test_only_the_approved_peer_can_supply_https_and_invalid_hosts_are_rejected(self):
        async def probe(client, host):
            app = ProxyHeadersMiddleware(ASGIHandler(), trusted_hosts="192.0.2.4")
            request = HttpCommunicator(app, "GET", "/healthz/", headers=[
                (b"host", host), (b"x-forwarded-proto", b"https"),
            ])
            request.scope["client"] = (client, 12345)
            return await request.get_response()

        for peer, host, expected in (
            ("192.0.2.5", b"stage.example.invalid", 301),
            ("192.0.2.4", b"stage.example.invalid", 200),
            ("192.0.2.4", b"other.example.invalid", 400),
        ):
            self.assertEqual(asyncio.run(probe(peer, host))["status"], expected)

    def test_websocket_boundary_rejects_invalid_origins_and_hosts(self):
        async def probe(host, origin, scheme):
            socket = WebsocketCommunicator(application, "/ws/business/example/orders/", headers=[
                (b"host", host), (b"origin", origin),
            ])
            socket.scope["scheme"] = scheme
            return await socket.connect()

        for host, origin, scheme in (
            (b"other.example.invalid", b"https://stage.example.invalid", "wss"),
            (b"stage.example.invalid", b"http://stage.example.invalid", "wss"),
            (b"stage.example.invalid", b"https://other.example.invalid", "wss"),
            (b"stage.example.invalid", b"https://stage.example.invalid", "ws"),
        ):
            self.assertEqual(asyncio.run(probe(host, origin, scheme)), (False, 1000))

    def test_proxy_csrf_rejects_foreign_origin(self):
        from django.test import Client

        client = Client(enforce_csrf_checks=True, HTTP_HOST="stage.example.invalid")
        sign_in = reverse("login")
        page = client.get(sign_in, secure=True)
        self.assertEqual(page.status_code, 200)
        token = client.cookies[settings.CSRF_COOKIE_NAME].value
        denied = client.post(sign_in, {"csrfmiddlewaretoken": token}, secure=True, HTTP_ORIGIN="https://other.example.invalid")
        self.assertEqual(denied.status_code, 403)


class WorkerReplicaTests(SimpleTestCase):
    def test_progress_is_specific_to_the_named_replica(self):
        one, two = str(uuid4()), str(uuid4())
        with redis_client() as redis:
            redis.set(heartbeat_key("outbox", one), "first", ex=10)
            redis.set(heartbeat_key("outbox", two), "second", ex=10)
            try:
                call_command("check_worker", "outbox", instance=one, stdout=StringIO())
                redis.delete(heartbeat_key("outbox", one))
                with self.assertRaises(CommandError):
                    call_command("check_worker", "outbox", instance=one, stdout=StringIO())
                call_command("check_worker", "outbox", instance=two, stdout=StringIO())
            finally:
                redis.delete(heartbeat_key("outbox", one), heartbeat_key("outbox", two))


class OutboxRecoveryTests(ShopFixture, TestCase):
    def test_failed_event_is_fenced_and_can_be_redriven_once(self):
        event = OutboxEvent.objects.create(
            business=self.business, event_type="menu.update", next_attempt_at=timezone.now(),
            attempts=MAX_OUTBOX_ATTEMPTS - 1,
        )
        # Model a Redis outage at the transport boundary.
        with patch("channels_redis.core.RedisChannelLayer.group_send", side_effect=ConnectionError("offline")):
            self.assertFalse(dispatch_outbox_event(event.pk))
        event.refresh_from_db()
        self.assertIsNotNone(event.failed_at)
        self.assertEqual(event.last_error, "ConnectionError")
        self.assertFalse(dispatch_outbox_event(event.pk))
        text = StringIO()
        with self.assertRaises(CommandError):
            call_command("check_operational_queues", stdout=text)
        self.assertIn("outbox_exhausted=1", text.getvalue())
        with self.assertRaises(CommandError):
            call_command("redrive_outbox", business="other", event=str(event.pk), evidence="INC-1")
        call_command(
            "redrive_outbox", business=self.business.slug, event=str(event.pk), evidence="INC-1", stdout=StringIO(),
        )
        self.assertTrue(dispatch_outbox_event(event.pk))
        self.assertFalse(dispatch_outbox_event(event.pk))
        self.assertTrue(AuditEvent.objects.filter(action="outbox.redrive", object_id=str(event.pk)).exists())

    def test_expired_final_lease_stays_durable_after_worker_crash(self):
        event = OutboxEvent.objects.create(
            business=self.business, event_type="menu.update", attempts=MAX_OUTBOX_ATTEMPTS,
            next_attempt_at=timezone.now() - timedelta(seconds=1),
        )
        call_command("process_outbox", once=True, stdout=StringIO())
        event.refresh_from_db()
        self.assertIsNone(event.published_at)
        self.assertIsNotNone(event.failed_at)
        self.assertEqual(event.last_error, "LeaseExpired")
