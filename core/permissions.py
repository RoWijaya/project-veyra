import hashlib
import uuid
from decimal import Decimal

from django.core import signing
from django.core.exceptions import ValidationError
from django.db.models import Exists, OuterRef, Q, Sum
from django.http import Http404

from .models import Business, InventoryBalance, Membership, Order, OrderRefund, PairedDevice, PaymentTransaction
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


def order_for_tracking_cookie(order_id, cookie_value, *, with_refunds=False):
    if not cookie_value:
        return None
    try:
        token = order_tracking_cookie_signer(order_id).unsign(cookie_value, max_age=ORDER_TRACKING_MAX_AGE)
    except (signing.BadSignature, TypeError, ValueError):
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    orders = Order.objects.filter(
        pk=order_id, source__in=Order.GUEST_SOURCES, guest_tracking_digest=digest
    )
    if with_refunds:
        orders = orders.annotate(
            refunded_amount=Sum(
                "refunds__amount", filter=Q(refunds__status=OrderRefund.Status.SUCCEEDED),
                default=Decimal("0.00"),
            ),
            qris_needs_review=Exists(PaymentTransaction.objects.filter(
                order=OuterRef("pk"), status=PaymentTransaction.Status.REVIEW,
            )),
        )
    return orders.first()

PERMISSION_CHOICES = (
    ("catalog.view", "View catalog"),
    ("catalog.manage", "Manage catalog"),
    ("ordering_page.view", "View ordering page builder"),
    ("ordering_page.edit", "Edit ordering page drafts"),
    ("orders.view", "View orders"),
    ("orders.create", "Create orders"),
    ("orders.manage", "Manage order status"),
    ("inventory.view", "View inventory"),
    ("inventory.manage", "Manage inventory"),
    ("locations.view", "View locations"),
    ("locations.manage", "Manage locations"),
    ("tables.view", "View tables and QR codes"),
    ("tables.manage", "Manage tables and QR codes"),
    ("customers.view", "View customers"),
    ("customers.manage", "Manage customers"),
    ("reports.view", "View reports"),
    ("employees.view", "View team"),
    ("employees.manage", "Manage team"),
)

INVITABLE_PERMISSIONS = frozenset(
    code for code, _ in PERMISSION_CHOICES if code not in {"tables.view", "tables.manage"}
)


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


def valid_location_key(value):
    return (
        isinstance(value, str) and value.isascii() and value.isdecimal()
        and len(value) <= 19 and 0 < int(value) <= 2**63 - 1
    )


def stream_access(user_id, slug, permission):
    member = Membership.objects.select_related("business").filter(
        user_id=user_id, user__is_active=True, business__slug=slug, active=True,
    ).first()
    if not member:
        business = Business.objects.filter(slug=slug, owner_id=user_id, owner__is_active=True).first()
        if business is None:
            return None
        member = Membership(business=business, user_id=user_id, active=True)
    if member.allows(permission) and (not member.location_restricted or member.allowed_locations().exists()):
        return member
    return None


def order_stream_business_id(user_id, slug):
    member = stream_access(user_id, slug, "orders.view")
    return member.business_id.hex if member else None


def order_stream_snapshot(user_id, slug, order_id):
    member = stream_access(user_id, slug, "orders.view")
    if not member:
        return None
    try:
        return member.scope_locations(member.business.orders).filter(pk=order_id).values(
            "status", "payment_status", "payment_method",
        ).first()
    except (ValidationError, TypeError, ValueError):
        return None


def inventory_stream_business_id(user_id, slug, location_id=None):
    member = stream_access(user_id, slug, "inventory.view")
    if member and (
        location_id is None
        or (valid_location_key(location_id) and member.allowed_locations().filter(pk=location_id).exists())
    ):
        return member.business_id.hex
    return None


def inventory_stream_snapshot(user_id, slug, item_id, location_id):
    member = stream_access(user_id, slug, "inventory.view")
    if not member or not all(valid_location_key(value) for value in (item_id, location_id)):
        return None
    balance = InventoryBalance.objects.filter(
        item_id=item_id, item__business_id=member.business_id,
        location_id=location_id, location__in=member.allowed_locations(),
    ).first()
    if balance is None:
        return None
    return {
        "quantity": str(balance.quantity),
        "last_purchase_unit_cost": str(balance.last_purchase_unit_cost) if balance.last_purchase_unit_cost is not None else None,
        "current_cost_estimate": str(balance.current_cost_estimate) if balance.last_purchase_unit_cost is not None else None,
    }


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
