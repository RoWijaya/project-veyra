import json
import logging
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase


class OperationalEndpointTests(TestCase):
    def test_live_readiness_checks_postgresql_and_redis(self):
        response = self.client.get("/readyz/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ready"})
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_redis_failure_is_unready_without_exposing_exception(self):
        with patch("redis.Redis.ping", side_effect=ConnectionError("secret-password")):
            response = self.client.get("/readyz/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})
        self.assertNotContains(response, "secret-password", status_code=503)
        self.assertEqual(self.client.get("/healthz/").status_code, 200)

    def test_request_ids_are_generated_and_untrusted_input_is_replaced(self):
        response = self.client.get("/healthz/", HTTP_X_REQUEST_ID="bad secret value")
        self.assertRegex(response["X-Request-ID"], r"^[0-9a-f]{32}$")
        valid = "a" * 32
        self.assertEqual(
            self.client.get("/healthz/", HTTP_X_REQUEST_ID=valid)["X-Request-ID"], valid
        )


class SafeLoggingTests(SimpleTestCase):
    def test_formatter_excludes_free_text_and_exception_secrets(self):
        from .observability import SafeJSONFormatter

        record = logging.LogRecord("veyra", logging.ERROR, "", 0, "password=secret", (), None)
        record.event = "request.completed"
        record.request_id = "a" * 32
        record.status_code = 500
        record.authorization = "Bearer secret"
        payload = json.loads(SafeJSONFormatter().format(record))
        self.assertEqual(payload["event"], "request.completed")
        self.assertNotIn("secret", json.dumps(payload))
