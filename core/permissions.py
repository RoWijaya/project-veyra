import hashlib
import uuid

from django.core import signing
from django.http import Http404

from .models import Business, Membership, Order, PairedDevice
from .storefront import resolve_storefront

ORDER_TRACKING_MAX_AGE = 60 * 60 * 24


def order_tracking_cookie_name(order_id):
    return f"veyra_track_{uuid.UUID(str(order_id)).hex}"


def order_tracking_cookie_signer(order_id):
    cookie_name = order_tracking_cookie_name(order_id)
    salt = "veyra-order-tracking"
    return signing.get_cookie_signer(
        salt=f"django.http.cookies.v2:{len(salt)}:{salt}{cookie_name}"
    )


def order_tracking_group_name(order_id):
    return f"order_tracking_{uuid.UUID(str(order_id)).hex}"


def guest_menu_business_id(token):
    try:
        return resolve_storefront(token).location.business_id.hex
    except Http404:
        return None


def order_for_tracking_cookie(order_id, cookie_value):
    if not cookie_value:
        return None
    try:
        token = order_tracking_cookie_signer(order_id).unsign(cookie_value, max_age=ORDER_TRACKING_MAX_AGE)
    except (signing.BadSignature, TypeError, ValueError):
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    return Order.objects.filter(
        pk=order_id, source__in=Order.GUEST_SOURCES, guest_tracking_digest=digest
    ).first()

STATION_CAPABILITIES = (
    ("catalog.view", "View catalog"),
    ("orders.view", "View orders"),
    ("orders.create", "Create orders"),
    ("orders.status.update", "Update order status"),
)

DEFAULT_STATION_CAPABILITIES = {
    "register": ["catalog.view", "orders.view", "orders.create"],
    "kitchen": ["orders.view", "orders.status.update"],
    "bar": ["orders.view", "orders.status.update"],
    "custom": [],
}


def order_stream_business_id(user_id, slug):
    business = Business.objects.filter(slug=slug).first()
    if not business:
        return None
    if business.owner_id == user_id:
        return business.id.hex
    membership = Membership.objects.filter(
        user_id=user_id,
        business=business,
        active=True,
    ).first()
    if membership and membership.allows("orders.view"):
        return business.id.hex
    return None


def inventory_stream_business_id(user_id, slug, location_id=None):
    business = Business.objects.filter(slug=slug).first()
    if not business:
        return None
    if business.owner_id == user_id:
        return business.id.hex
    membership = Membership.objects.filter(
        user_id=user_id, business=business, active=True
    ).first()
    if membership and membership.allows("inventory.view"):
        if location_id is None or membership.allowed_inventory_locations().filter(pk=location_id).exists():
            return business.id.hex
    return None


def device_stream_business_id(station_id, token_digest):
    device = (
        PairedDevice.objects.select_related("station", "station__business")
        .filter(
            station_id=station_id,
            token_digest=token_digest,
            revoked_at__isnull=True,
            station__active=True,
        )
        .first()
    )
    if device and "orders.view" in device.station.capabilities:
        return device.station.business_id.hex
    return None
