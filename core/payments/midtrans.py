import base64
import hashlib
import hmac
import json
import urllib.request
import urllib.error
from datetime import timedelta
from decimal import Decimal
from typing import Any, Dict, Tuple
from django.utils import timezone

from .base import BasePaymentAdapter, PaymentResult


class MidtransAdapter(BasePaymentAdapter):
    SANDBOX_BASE = "https://api.sandbox.midtrans.com/v2"
    PROD_BASE = "https://api.midtrans.com/v2"

    def _get_base_url(self, environment: str) -> str:
        return self.SANDBOX_BASE if environment == "sandbox" else self.PROD_BASE

    def create_qris(
        self,
        *,
        connection,
        credentials: Dict[str, Any],
        order,
        amount: Decimal,
        expiry_minutes: int = 10,
    ) -> PaymentResult:
        server_key = credentials.get("server_key", "").strip()
        if not server_key:
            return PaymentResult(success=False, error="Midtrans server_key is required")

        merchant_ref = f"ORD-{str(order.pk)[:8]}-{int(timezone.now().timestamp())}"
        now = timezone.now()
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
                "order_time": now.strftime("%Y-%m-%d %H:%M:%S +0700"),
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
                data = json.loads(error.read().decode())
                err_msg = data.get("status_message") or str(error)
            except Exception:
                err_msg = str(error)
            return PaymentResult(success=False, error=f"Midtrans error: {err_msg}")
        except Exception as error:
            return PaymentResult(success=False, error=f"Connection failure: {error}")

        status_code = data.get("status_code", "")
        if status_code in ("200", "201"):
            actions = data.get("actions", [])
            qr_image_url = ""
            for action in actions:
                if action.get("name") == "generate-qr-code":
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
            error=data.get("status_message", "Failed to generate QRIS"),
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
        server_key = credentials.get("server_key", "").strip()
        if not server_key:
            return False, "Server key missing"

        signature_key = payload.get("signature_key", "")
        order_id = payload.get("order_id", "")
        status_code = payload.get("status_code", "")
        gross_amount = payload.get("gross_amount", "")

        if not (signature_key and order_id and status_code and gross_amount):
            return False, "Missing fields for signature verification"

        # Midtrans signature key: SHA512(order_id + status_code + gross_amount + ServerKey)
        data = f"{order_id}{status_code}{gross_amount}{server_key}"
        computed = hashlib.sha512(data.encode()).hexdigest()

        if hmac.compare_digest(computed.lower(), signature_key.lower()):
            return True, ""
        return False, "Signature mismatch"

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
