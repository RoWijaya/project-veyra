from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple


@dataclass
class PaymentResult:
    success: bool
    transaction_id: str = ""
    merchant_reference: str = ""
    amount: Decimal = Decimal("0.00")
    currency: str = "IDR"
    status: str = "pending"
    qr_payload: str = ""
    qr_image_url: str = ""
    expires_at: Optional[datetime] = None
    raw_response: Dict[str, Any] = field(default_factory=dict)
    error: str = ""


class BasePaymentAdapter:
    def create_qris(
        self,
        *,
        connection,
        credentials: Dict[str, Any],
        order,
        amount: Decimal,
        expiry_minutes: int = 10,
    ) -> PaymentResult:
        raise NotImplementedError

    def verify_webhook_signature(
        self,
        *,
        connection,
        credentials: Dict[str, Any],
        payload: Dict[str, Any],
        headers: Dict[str, Any],
        raw_body: bytes,
    ) -> Tuple[bool, str]:
        raise NotImplementedError

    def parse_webhook_status(
        self, payload: Dict[str, Any]
    ) -> Tuple[str, str, Decimal, str]:
        """Returns (provider_transaction_id, merchant_reference, amount, status)."""
        raise NotImplementedError

    def verify_credentials(
        self, *, connection, credentials: Dict[str, Any]
    ) -> Tuple[bool, str]:
        raise NotImplementedError
