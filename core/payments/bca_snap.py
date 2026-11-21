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


class BcaSnapAdapter(BasePaymentAdapter):
    SANDBOX_BASE = "https://sandbox.bca.co.id"
    PROD_BASE = "https://api.bca.co.id"

    def _get_base_url(self, environment: str) -> str:
        return self.SANDBOX_BASE if environment == "sandbox" else self.PROD_BASE

    def _generate_symmetric_signature(
        self,
        *,
        http_method: str,
        endpoint_path: str,
        body_str: str,
        timestamp: str,
        client_secret: str,
    ) -> str:
        body_hash = hashlib.sha256(body_str.encode()).hexdigest().lower()
        string_to_sign = f"{http_method.upper()}:{endpoint_path}:{body_hash}:{timestamp}"
        signature = hmac.new(
            client_secret.encode(), string_to_sign.encode(), hashlib.sha512
        ).hexdigest()
        return signature

    def create_qris(
        self,
        *,
        connection,
        credentials: Dict[str, Any],
        order,
        amount: Decimal,
        expiry_minutes: int = 10,
    ) -> PaymentResult:
        client_id = credentials.get("client_id", "").strip()
        client_secret = credentials.get("client_secret", "").strip()
        channel_id = credentials.get("channel_id", "95221").strip()

        if not (client_id and client_secret):
            return PaymentResult(success=False, error="BCA SNAP client_id and client_secret are required")

        merchant_ref = f"BCA-{str(order.pk)[:8]}-{int(timezone.now().timestamp())}"
        now = timezone.now()
        expires_at = now + timedelta(minutes=expiry_minutes)
        timestamp = now.strftime("%Y-%m-%dT%H:%M:%S+07:00")

        endpoint_path = "/api/v1.0/qr/qr-mpm-generate"
        payload = {
            "partnerReferenceNo": merchant_ref,
            "amount": {
                "value": f"{amount:.2f}",
                "currency": "IDR",
            },
            "merchantId": connection.merchant_id or client_id,
            "terminalId": "00000001",
            "validityPeriod": expires_at.strftime("%Y-%m-%dT%H:%M:%S+07:00"),
        }

        minified_body = json.dumps(payload, separators=(",", ":"))
        signature = self._generate_symmetric_signature(
            http_method="POST",
            endpoint_path=endpoint_path,
            body_str=minified_body,
            timestamp=timestamp,
            client_secret=client_secret,
        )

        headers = {
            "Content-Type": "application/json",
            "X-TIMESTAMP": timestamp,
            "X-SIGNATURE": signature,
            "X-PARTNER-ID": client_id,
            "X-EXTERNAL-ID": merchant_ref,
            "CHANNEL-ID": channel_id,
        }

        url = f"{self._get_base_url(connection.environment)}{endpoint_path}"
        try:
            req = urllib.request.Request(
                url, data=minified_body.encode(), headers=headers, method="POST"
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as error:
            try:
                data = json.loads(error.read().decode())
                err_msg = data.get("responseMessage") or str(error)
            except Exception:
                err_msg = str(error)
            return PaymentResult(success=False, error=f"BCA SNAP error: {err_msg}")
        except Exception as error:
            return PaymentResult(success=False, error=f"Connection failure: {error}")

        response_code = data.get("responseCode", "")
        # SNAP success codes start with '200'
        if response_code.startswith("200"):
            qr_content = data.get("qrContent", "")
            return PaymentResult(
                success=True,
                transaction_id=data.get("referenceNo", ""),
                merchant_reference=merchant_ref,
                amount=amount,
                currency="IDR",
                status="pending",
                qr_payload=qr_content,
                qr_image_url="",
                expires_at=expires_at,
                raw_response=data,
            )
        return PaymentResult(
            success=False,
            error=data.get("responseMessage", "Failed to generate BCA QRIS"),
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
        client_secret = credentials.get("client_secret", "").strip()
        if not client_secret:
            return False, "Client secret missing"

        norm_headers = {str(k).lower(): str(v) for k, v in headers.items()}
        sig_header = norm_headers.get("x-signature", "")
        timestamp = norm_headers.get("x-timestamp", "")
        if not (sig_header and timestamp):
            return False, "Missing X-SIGNATURE or X-TIMESTAMP header"

        endpoint_path = "/api/v1.0/qr/qr-mpm-notify"
        minified_body = raw_body.decode("utf-8") if isinstance(raw_body, bytes) else str(raw_body)
        expected_sig = self._generate_symmetric_signature(
            http_method="POST",
            endpoint_path=endpoint_path,
            body_str=minified_body,
            timestamp=timestamp,
            client_secret=client_secret,
        )

        if hmac.compare_digest(expected_sig.lower(), sig_header.lower()):
            return True, ""
        return False, "Signature mismatch"

    def parse_webhook_status(
        self, payload: Dict[str, Any]
    ) -> Tuple[str, str, Decimal, str]:
        provider_tx_id = payload.get("referenceNo", "")
        merchant_ref = payload.get("partnerReferenceNo", "")
        amount_dict = payload.get("amount", {})
        val = amount_dict.get("value", "0") if isinstance(amount_dict, dict) else payload.get("amount", "0")
        try:
            amount = Decimal(str(val))
        except Exception:
            amount = Decimal("0.00")

        # SNAP standard latestTransactionStatus: "00" = Success
        tx_status = payload.get("latestTransactionStatus", "")
        if tx_status == "00":
            status = "paid"
        elif tx_status in ("03", "04"):
            status = "failed"
        elif tx_status == "05":
            status = "expired"
        else:
            status = "pending"

        return provider_tx_id, merchant_ref, amount, status

    def verify_credentials(
        self, *, connection, credentials: Dict[str, Any]
    ) -> Tuple[bool, str]:
        client_id = credentials.get("client_id", "").strip()
        client_secret = credentials.get("client_secret", "").strip()
        if not client_id or not client_secret:
            return False, "client_id and client_secret are required for BCA SNAP"
        return True, "Valid credential format"
