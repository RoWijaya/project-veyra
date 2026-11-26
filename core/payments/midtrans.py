import base64
import hashlib
import hmac
import json
import re
import urllib.request
import urllib.error
from urllib.parse import quote
from uuid import uuid4
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, Tuple

import requests
from django.conf import settings

from .base import BasePaymentAdapter, PaymentResult, qris_start_time


def notification_credentials(connection, credentials):
    secret = credentials.get("signature_secret_key", "").strip()
    if secret and secret != credentials.get("server_key", "").strip():
        return {"signature_secret_key": secret}
    if "signature_secret_key" not in credentials and str(connection.pk) in settings.MIDTRANS_LEGACY_SIGNING_CONNECTIONS:
        return {"server_key": credentials.get("server_key", "")}
    return {}


def valid_notification_signature(payload, secret):
    if not secret or not isinstance(payload, dict):
        return False
    reference, code, amount, signature = (
        payload.get(key) for key in ("order_id", "status_code", "gross_amount", "signature_key")
    )
    if (
        not isinstance(reference, str) or not reference or len(reference) > 120 or not reference.isascii()
        or type(code) not in (str, int) or not re.fullmatch(r"[0-9]{3}", str(code))
        or type(amount) not in (str, int) or not re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,2})?", str(amount))
        or not isinstance(signature, str) or not re.fullmatch(r"[0-9a-fA-F]{128}", signature)
    ):
        return False
    expected = hashlib.sha512(f"{reference}{code}{amount}{secret}".encode()).hexdigest()
    return hmac.compare_digest(expected, signature.lower())


class MidtransStatusUnavailable(RuntimeError):
    pass


class MidtransAdapter(BasePaymentAdapter):
    SANDBOX_BASE = "https://api.sandbox.midtrans.com/v2"
    PROD_BASE = "https://api.midtrans.com/v2"

    def _get_base_url(self, environment: str) -> str:
        return self.SANDBOX_BASE if environment == "sandbox" else self.PROD_BASE

    def _transaction_request(self, *, connection, credentials, reference, operation, data=None):
        key = credentials.get("server_key", "").strip()
        auth = base64.b64encode(f"{key}:".encode()).decode()
        url = f"{self._get_base_url(connection.environment)}/{quote(reference, safe='')}/{operation}"
        request = urllib.request.Request(
            url, data=json.dumps(data).encode() if data is not None else None,
            headers={"Accept": "application/json", "Content-Type": "application/json", "Authorization": f"Basic {auth}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                result = json.loads(response.read())
        except (OSError, ValueError) as error:
            raise MidtransStatusUnavailable("Midtrans did not provide a verifiable response.") from error
        if not isinstance(result, dict):
            raise MidtransStatusUnavailable("Midtrans did not provide a verifiable response.")
        return result

    def transaction_status(self, *, connection, credentials, reference):
        return self._transaction_request(
            connection=connection, credentials=credentials, reference=reference, operation="status",
        )

    def verify_transaction(self, payment, payload, *, allow_unbound=False):
        provider_id, reference, amount, _ = self.parse_webhook_status(payload)
        if (
            not isinstance(provider_id, str) or not provider_id or len(provider_id) > 120
            or (payment.provider_transaction_id != provider_id
                and (payment.provider_transaction_id or not allow_unbound))
            or reference != payment.merchant_reference or not amount.is_finite() or amount != payment.amount
            or payload.get("currency", "IDR") != payment.currency or payload.get("payment_type") != "qris"
            or (not payment.provider_transaction_id and not payload.get("merchant_id"))
            or payload.get("merchant_id", payment.connection.merchant_id) != payment.connection.merchant_id
        ):
            raise MidtransStatusUnavailable("Midtrans status did not match the original QRIS payment.")

    def cancel_transaction(self, *, connection, credentials, reference):
        return self._transaction_request(
            connection=connection, credentials=credentials, reference=reference, operation="cancel", data={},
        )

    def request_refund(self, *, connection, credentials, reference, key, amount, reason):
        return self._transaction_request(
            connection=connection, credentials=credentials, reference=reference, operation="refund/online/direct",
            data={"refund_key": key, "amount": int(amount), "reason": reason},
        )

    def create_qris(
        self,
        *,
        connection,
        credentials: Dict[str, Any],
        order,
        amount: Decimal,
        expiry_minutes: int = 10,
        merchant_reference: str = "",
        requested_at: datetime | None = None,
    ) -> PaymentResult:
        server_key = credentials.get("server_key", "").strip()
        if not server_key:
            return PaymentResult(success=False, error="Midtrans server_key is required")

        if amount <= 0 or amount != amount.to_integral_value():
            return PaymentResult(success=False, error="Midtrans QRIS requires a positive whole-rupiah total.")
        merchant_ref = merchant_reference or f"ORD-{order.pk.hex}-{uuid4().hex[:10]}"
        now = qris_start_time(requested_at)
        expires_at = now + timedelta(minutes=expiry_minutes)

        int_amount = int(amount)
        payload = {
            "payment_type": "qris",
            "transaction_details": {
                "order_id": merchant_ref,
                "gross_amount": int_amount,
            },
            "qris": {
                "acquirer": "gopay",
            },
            "custom_expiry": {
                "order_time": now.strftime("%Y-%m-%d %H:%M:%S %z"),
                "expiry_duration": expiry_minutes,
                "unit": "minute",
            },
        }

        url = f"{self._get_base_url(connection.environment)}/charge"
        auth_bytes = base64.b64encode(f"{server_key}:".encode()).decode()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Basic {auth_bytes}",
        }

        try:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode(), headers=headers, method="POST"
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as error:
            try:
                data = json.loads(error.read())
            except (OSError, ValueError):
                data = {}
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}

        status_code = str(data.get("status_code", ""))
        if status_code in ("200", "201"):
            actions = data.get("actions")
            actions = actions if isinstance(actions, list) else []
            qr_image_url = ""
            for action in actions:
                if isinstance(action, dict) and action.get("name") == "generate-qr-code":
                    qr_image_url = action.get("url", "")
                    break

            qr_string = data.get("qr_string", "")
            return PaymentResult(
                success=True,
                transaction_id=data.get("transaction_id", ""),
                merchant_reference=merchant_ref,
                amount=amount,
                currency="IDR",
                status="pending",
                qr_payload=qr_string,
                qr_image_url=qr_image_url,
                expires_at=expires_at,
                raw_response=data,
            )
        return PaymentResult(
            success=False,
            error="Midtrans did not confirm that a QR code was created.",
            uncertain=status_code not in ("400", "401", "402", "403"),
            raw_response=data,
        )

    def verify_webhook_signature(
        self,
        *,
        connection,
        credentials: Dict[str, Any],
        payload: Dict[str, Any],
        headers: Dict[str, Any],
        raw_body: bytes,
    ) -> Tuple[bool, str]:
        signing = notification_credentials(connection, credentials)
        secret = signing.get("signature_secret_key", signing.get("server_key", ""))
        valid = valid_notification_signature(payload, secret)
        return valid, "" if valid else "Signature mismatch"


    def parse_webhook_status(
        self, payload: Dict[str, Any]
    ) -> Tuple[str, str, Decimal, str]:
        provider_tx_id = payload.get("transaction_id", "")
        merchant_ref = payload.get("order_id", "")
        raw_amount = payload.get("gross_amount", "0")
        try:
            amount = Decimal(str(raw_amount))
        except Exception:
            amount = Decimal("0.00")

        tx_status = payload.get("transaction_status", "")
        fraud_status = payload.get("fraud_status", "accept")

        if tx_status in ("capture", "settlement"):
            status = "paid" if fraud_status == "accept" else "failed"
        elif tx_status == "pending":
            status = "pending"
        elif tx_status in ("deny", "cancel"):
            status = "failed"
        elif tx_status == "expire":
            status = "expired"
        elif tx_status in ("refund", "partial_refund"):
            status = "refunded"
        else:
            status = "pending"

        return provider_tx_id, merchant_ref, amount, status

    def check_qris_evidence(self, *, connection, credentials, reference):
        url = f"{self._get_base_url(connection.environment)}/{quote(reference, safe='')}/status"
        try:
            response = requests.get(
                url, auth=(credentials["server_key"], ""), timeout=(3, 6), allow_redirects=False,
            )
            if response.status_code == 401:
                return "failed", "Midtrans rejected the Server Key for this environment."
            if response.status_code == 403:
                return "failed", "The Server Key lacks access to Midtrans Core API status."
            if response.status_code == 404:
                return "failed", "Midtrans could not find the reference in this environment."
            if not 200 <= response.status_code < 300:
                return "failed", "Midtrans could not verify the reference. Try again later."
            result = response.json()
        except requests.Timeout:
            return "failed", "The Midtrans verification timed out. Try again."
        except (requests.RequestException, ValueError):
            return "failed", "Midtrans verification is temporarily unavailable."
        if not isinstance(result, dict) or str(result.get("status_code")) not in ("200", "201"):
            return "failed", "Midtrans did not return a verifiable transaction."
        if reference not in (result.get("order_id"), result.get("transaction_id")):
            return "failed", "The transaction response did not match the reference."
        if result.get("merchant_id") != connection.merchant_id:
            return "authenticated", "The transaction did not match the configured Merchant ID."
        if result.get("payment_type") != "qris" or result.get("currency", "IDR") != "IDR":
            return "authenticated", "Use a QRIS transaction in IDR from this Midtrans merchant."
        if not valid_notification_signature(result, credentials["signature_secret_key"]):
            return "authenticated", "The Signature Secret Key did not match the signed Midtrans response."
        return "evidence", "Merchant, QRIS history and signing key checked. Core API Write and callback approval are separate."

    def verify_credentials(
        self, *, connection, credentials: Dict[str, Any]
    ) -> Tuple[bool, str]:
        server_key = credentials.get("server_key", "").strip()
        if not server_key:
            return False, "server_key is required"
        if connection.environment == "sandbox" and not (
            server_key.startswith("SB-Mid-server-") or "sandbox" in server_key.lower()
        ):
            return False, "Sandbox server_key typically starts with 'SB-Mid-server-'"
        return True, "Valid credential format"
