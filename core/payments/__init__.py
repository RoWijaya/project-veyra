from .base import BasePaymentAdapter, PaymentResult
from .bca_snap import BcaSnapAdapter
from .midtrans import MidtransAdapter

ADAPTERS = {
    "midtrans": MidtransAdapter(),
    "bca_qris": BcaSnapAdapter(),
}


def get_payment_adapter(provider: str) -> BasePaymentAdapter:
    if provider not in ADAPTERS:
        raise ValueError(f"No payment adapter registered for '{provider}'")
    return ADAPTERS[provider]
