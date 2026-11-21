from django.core.cache import cache
from django.utils.crypto import salted_hmac

from .pricing import VoucherError


class VoucherRateLimit(VoucherError):
    pass


def check_voucher_attempt(request, business):
    address = request.META.get("REMOTE_ADDR", "unknown")
    principal = str(request.user.pk) if request.user.is_authenticated else address
    identity = salted_hmac("veyra-voucher-attempt", f"{business.pk}:{principal}").hexdigest()
    key = f"voucher-attempt:{identity}"
    cache.add(key, 0, timeout=300)
    try:
        count = cache.incr(key)
    except ValueError:
        cache.add(key, 0, timeout=300)
        count = cache.incr(key)
    if count > 24:
        raise VoucherRateLimit("Too many voucher attempts. Try again in a few minutes.")
