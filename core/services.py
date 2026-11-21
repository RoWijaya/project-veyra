import asyncio
import hashlib
import json
import secrets
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography.fernet import Fernet
from django.conf import settings
from django.core import signing
from django.db import models, transaction
from django.utils import timezone

from .models import (
    AuditEvent,
    Business,
    BusinessSubscription,
    Customer,
    DiningTable,
    InventoryBalance,
    InventoryItem,
    Invitation,
    Location,
    Membership,
    Order,
    OrderLine,
    OrderLinePromotion,
    OrderTender,
    OutboxEvent,
    PairedDevice,
    PaymentConnection,
    PaymentTransaction,
    PaymentWebhookEvent,
    Printer,
    PrintJob,
    Product,
    Promotion,
    PromotionScheduleState,
    Station,
    StationPairingCode,
    StockMovement,
    SubscriptionEvent,
    SubscriptionPlan,
)
from .modifiers import normalize_options
from .observability import logger
from .payments import get_payment_adapter
from .permissions import STATION_CAPABILITIES, order_tracking_group_name
from .pricing import (
    calculate_voucher, normalize_voucher_code, pos_voucher_approval, price_order_items,
    promotion_is_in_window,
)


def dispatch_outbox_event(event_id):
    now = timezone.now()
    with transaction.atomic():
        event = (
            OutboxEvent.objects.select_for_update()
            .filter(pk=event_id, published_at__isnull=True, next_attempt_at__lte=now)
            .first()
        )
        if event is None:
            return False
        event.attempts += 1
        event.next_attempt_at = now + timedelta(seconds=30)
        event.save(update_fields=["attempts", "next_attempt_at"])
    try:
        from channels.layers import get_channel_layer

        layer = get_channel_layer()
        if layer is None:
            raise RuntimeError("No channel layer is configured")
        async def publish():
            message = {"type": event.event_type, "event_id": str(event.pk), **event.payload}
            if event.event_type == "menu.update":
                await layer.group_send(f"guest_menu_{event.business_id.hex}", message)
                if "modifier_group" in event.payload or "voucher" in event.payload or event.payload.get("catalog_changed"):
                    await layer.group_send(f"business_{event.business_id.hex}", message)
            elif event.event_type == "inventory.update":
                await layer.group_send(f"inventory_{event.business_id.hex}", message)
            elif event.event_type == "purchase.update":
                await layer.group_send(f"purchasing_{event.business_id.hex}", message)
            elif event.event_type == "billing.update":
                await layer.group_send(f"billing_{event.business_id.hex}", message)
            else:
                await layer.group_send(f"business_{event.business_id.hex}", message)
            if event.payload.get("source") in Order.GUEST_SOURCES:
                await layer.group_send(
                    order_tracking_group_name(event.payload["order_id"]), message
                )

        asyncio.run(publish())
    except Exception as error:
        retry_delay = min(5 * 2 ** min(event.attempts - 1, 6), 300)
        OutboxEvent.objects.filter(pk=event.pk, published_at__isnull=True).update(
            next_attempt_at=timezone.now() + timedelta(seconds=retry_delay),
            last_error=type(error).__name__,
        )
        logger.error("Outbox delivery failed", extra={
            "event": "outbox.delivery_failed", "event_id": str(event.pk),
            "attempts": event.attempts,
        }, exc_info=True)
        return False
    OutboxEvent.objects.filter(pk=event.pk, published_at__isnull=True).update(
        published_at=timezone.now(), last_error=""
    )
    return True


def queue_promotion_boundary_events(*, at=None):
    """Notify open QR menus when an active scheduled promotion crosses a boundary."""
    now = at or timezone.now()
    queued = 0
    with transaction.atomic():
        promotions = (
            Promotion.objects.select_for_update()
            .select_related("business")
            .filter(active=True)
        )
        for promotion in promotions:
            in_window = promotion_is_in_window(promotion, at=now)
            state, created = PromotionScheduleState.objects.get_or_create(
                promotion=promotion,
                defaults={"is_in_window": in_window},
            )
            if created or state.is_in_window == in_window:
                continue
            state.is_in_window = in_window
            state.save(update_fields=["is_in_window", "checked_at"])
            OutboxEvent.objects.create(
                business=promotion.business,
                event_type="menu.update",
                payload={"promotion_id": promotion.pk},
                next_attempt_at=now,
            )
            queued += 1
    return queued


def _queue_order_event(order, name):
    OutboxEvent.objects.create(
        business=order.business,
        event_type="order.update",
        payload={
            "event": name,
            "order_id": str(order.pk),
            "status": order.status,
            "payment_status": order.payment_status,
            "payment_method": order.payment_method,
            "total": str(order.total),
            "order_type": order.order_type,
            "source": order.source,
            "table_id": str(order.table_id) if order.table_id else None,
            "table_label": order.table_label,
            "created_at": order.created_at.isoformat(),
        },
        next_attempt_at=timezone.now(),
    )


def _queue_inventory_event(*, business, item, location, balance, order=None):
    OutboxEvent.objects.create(
        business=business,
        event_type="inventory.update",
        payload={
            **({"order_id": str(order.pk)} if order else {}),
            "item_id": str(item.pk),
            "location_id": str(location.pk),
            "quantity": str(balance.quantity),
            "last_purchase_unit_cost": str(balance.last_purchase_unit_cost) if balance.last_purchase_unit_cost is not None else None,
        },
        next_attempt_at=timezone.now(),
    )


def _consume_order_recipe(order, actor):
    needs = {}
    for line in order.lines.select_related("product").prefetch_related("product__recipe_lines"):
        for ingredient in line.product.recipe_lines.all():
            needs[ingredient.item_id] = (ingredient.item, needs.get(ingredient.item_id, (None, 0))[1] + ingredient.quantity * line.quantity)
    if needs and order.location_id is None:
        raise ValueError("Choose a location before preparing an order that uses recipes")
    for item_id, (item, required) in sorted(needs.items()):
        try:
            balance = InventoryBalance.objects.select_for_update().get(
                item_id=item_id, location_id=order.location_id
            )
        except InventoryBalance.DoesNotExist as error:
            raise ValueError("Recipe stock is not available at this location") from error
        if balance.quantity < required:
            raise ValueError(f"Not enough {item.name} to prepare this order")
        balance.quantity -= required
        balance.save(update_fields=["quantity"])
        StockMovement.objects.create(
            business=order.business,
            item=item,
            location=order.location,
            order=order,
            actor=actor,
            quantity_delta=-required,
            reason=StockMovement.Reason.SALE,
            note=f"Recipe use for order {str(order.pk)[:8]}",
        )
        _queue_inventory_event(
            business=order.business,
            item=item,
            location=order.location,
            balance=balance,
            order=order,
        )


def _return_order_recipe(order, actor):
    sales = order.stock_movements.filter(reason=StockMovement.Reason.SALE).select_related("item")
    for sale in sales:
        balance = InventoryBalance.objects.select_for_update().get(
            item=sale.item, location=sale.location
        )
        balance.quantity -= sale.quantity_delta
        balance.save(update_fields=["quantity"])
        StockMovement.objects.create(
            business=order.business,
            item=sale.item,
            location=sale.location,
            order=order,
            actor=actor,
            quantity_delta=-sale.quantity_delta,
            reason=StockMovement.Reason.RETURN,
            note=f"Recipe return for order {str(order.pk)[:8]}",
        )
        _queue_inventory_event(
            business=order.business,
            item=sale.item,
            location=sale.location,
            balance=balance,
            order=order,
        )


def create_business(*, owner, name, slug, business_type="other"):
    from .billing import plan_terms

    with transaction.atomic():
        business = Business.objects.create(
            owner=owner, name=name, slug=slug, business_type=business_type
        )
        Membership.objects.create(business=business, user=owner)
        Location.objects.create(business=business, name="Main location")
        plan, _ = SubscriptionPlan.objects.get_or_create(
            code="starter-trial",
            defaults={
                "name": "Veyra Starter Trial",
                "monthly_price": Decimal("0.00"),
                "currency": "IDR",
                "trial_days": 14,
            },
        )
        now = timezone.now()
        trial_days = plan.trial_days if plan.active else 0
        subscription = BusinessSubscription.objects.create(
            business=business,
            plan=plan,
            status=BusinessSubscription.Status.TRIAL if trial_days else BusinessSubscription.Status.EXPIRED,
            trial_started_at=now if trial_days else None,
            trial_ends_at=now + timedelta(days=trial_days) if trial_days else None,
            terms={**plan_terms(plan, interval="trial"), "trial_days": trial_days},
        )
        SubscriptionEvent.objects.create(
            subscription=subscription,
            actor=owner,
            event_type="trial.started" if trial_days else "trial.unavailable",
            to_status=subscription.status,
            to_plan=plan.code,
            metadata={"trial_days": trial_days},
        )
        AuditEvent.objects.create(
            business=business, actor=owner, action="business.created", object_id=str(business.pk)
        )
    return business


def business_entitlements(business, *, at=None):
    subscription = (
        BusinessSubscription.objects.select_related("plan")
        .filter(business=business)
        .first()
    )
    if subscription is None:
        return {"active": False, "features": [], "limits": {}, "subscription": None}
    now = at or timezone.now()
    from .billing import subscription_access

    active = subscription_access(subscription, now)
    entitlements = subscription.terms.get("entitlements", subscription.plan.entitlements) or {}
    return {
        "active": active,
        "features": entitlements.get("features", []),
        "limits": entitlements.get("limits", {}),
        "subscription": subscription,
    }


def require_business_feature(business, feature, *, at=None):
    entitlements = business_entitlements(business, at=at)
    if not entitlements["active"] or "*" not in entitlements["features"] and feature not in entitlements["features"]:
        raise PermissionError("This feature is unavailable on the current subscription")
    return entitlements


def enforce_business_limit(business, limit_name, current_count):
    access = business_entitlements(business)
    if not access["active"]:
        raise PermissionError("Renew the subscription before expanding your business.")
    limit = access["limits"].get(limit_name)
    if limit is not None and current_count >= int(limit):
        raise PermissionError(f"The current subscription has reached its {limit_name} limit")


def create_location(*, business, actor, name, address=""):
    with transaction.atomic():
        business = Business.objects.select_for_update().get(pk=business.pk)
        membership = Membership.objects.get(business=business, user=actor, active=True)
        if not membership.allows("locations.manage"):
            raise PermissionError("You cannot manage locations for this business")
        enforce_business_limit(
            business, "locations", business.locations.filter(active=True).count()
        )
        location = Location.objects.create(
            business=business, name=name.strip(), address=address.strip()
        )
        InventoryBalance.objects.bulk_create(
            [
                InventoryBalance(item=item, location=location, quantity=Decimal("0"))
                for item in business.inventory_items.all()
            ]
        )
        AuditEvent.objects.create(
            business=business,
            actor=actor,
            action="location.created",
            object_type="Location",
            object_id=str(location.pk),
        )
    return location


def create_invitation(*, business, actor, email, permissions):
    email = email.strip().lower()
    requested_permissions = set(permissions)
    valid_permissions = {
        "catalog.view",
        "catalog.manage",
        "orders.view",
        "orders.create",
        "orders.manage",
        "inventory.view",
        "inventory.manage",
        "locations.view",
        "locations.manage",
        "customers.view",
        "customers.manage",
        "reports.view",
        "employees.view",
        "employees.manage",
    }
    if not requested_permissions.issubset(valid_permissions):
        raise ValueError("Invitation contains an unknown permission")
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode()).hexdigest()
    with transaction.atomic():
        business = Business.objects.select_for_update().get(pk=business.pk)
        membership = Membership.objects.get(business=business, user=actor, active=True)
        if not membership.allows("employees.manage"):
            raise PermissionError("You cannot manage employees for this business")
        if business.owner_id != actor.pk and not requested_permissions.issubset(
            set(membership.permissions)
        ):
            raise PermissionError("You cannot grant permissions you do not have")
        if Membership.objects.filter(business=business, user__email=email, active=True).exists():
            raise ValueError("This person is already an active member")
        if Invitation.objects.filter(
            business=business, email=email, accepted_at__isnull=True, expires_at__gt=timezone.now()
        ).exists():
            raise ValueError("A current invitation already exists for this email")
        invitation = Invitation.objects.create(
            business=business,
            invited_by=actor,
            email=email,
            permissions=sorted(requested_permissions),
            token_digest=digest,
            expires_at=timezone.now() + timedelta(days=7),
        )
        AuditEvent.objects.create(
            business=business,
            actor=actor,
            action="invitation.created",
            object_type="Invitation",
            object_id=str(invitation.pk),
            metadata={"email": email, "permissions": sorted(requested_permissions)},
        )
    return invitation, token


def accept_invitation(*, token, business_slug, actor):
    digest = hashlib.sha256(token.encode()).hexdigest()
    with transaction.atomic():
        business_id = Invitation.objects.values_list("business_id", flat=True).get(
            business__slug=business_slug, token_digest=digest,
        )
        business = Business.objects.select_for_update().get(pk=business_id)
        invitation = Invitation.objects.select_for_update().get(business=business, token_digest=digest)
        if (
            invitation.accepted_at
            or invitation.expires_at <= timezone.now()
            or invitation.email.lower() != actor.email.lower()
        ):
            raise ValueError("This invitation is invalid or expired")
        require_business_feature(business, "team")
        if not business.memberships.filter(user=actor, active=True).exists():
            enforce_business_limit(business, "employees", business.memberships.filter(active=True).exclude(user_id=business.owner_id).count())
        membership, created = Membership.objects.get_or_create(
            business=business,
            user=actor,
            defaults={
                "permissions": invitation.permissions,
                "active": True,
            },
        )
        if not created and not membership.active:
            membership.permissions = invitation.permissions
            membership.active = True
            membership.save(update_fields=["permissions", "active"])
        invitation.accepted_at = timezone.now()
        invitation.save(update_fields=["accepted_at"])
        AuditEvent.objects.create(
            business=invitation.business,
            actor=actor,
            action="invitation.accepted",
            object_type="Membership",
            object_id=str(membership.pk),
        )
    return membership


def revoke_invitation(*, business, invitation_id, actor):
    with transaction.atomic():
        business = Business.objects.select_for_update().get(pk=business.pk)
        membership = Membership.objects.get(business=business, user=actor, active=True)
        if not membership.allows("employees.manage"):
            raise PermissionError("You cannot manage invitations for this business")
        invitation = Invitation.objects.select_for_update().get(
            pk=invitation_id, business=business
        )
        if invitation.accepted_at is not None:
            raise ValueError("Accepted invitations cannot be revoked")
        now = timezone.now()
        if invitation.expires_at > now:
            invitation.expires_at = now
            invitation.save(update_fields=["expires_at"])
            AuditEvent.objects.create(
                business=business,
                actor=actor,
                action="invitation.revoked",
                object_type="Invitation",
                object_id=str(invitation.pk),
                metadata={"email": invitation.email},
            )
    return invitation


def _station_session(device, *capabilities):
    session = (
        PairedDevice.objects.select_related("station", "station__business")
        .filter(pk=device.pk, revoked_at__isnull=True, station__active=True)
        .first()
    )
    if session is None:
        raise PermissionError("This station session is revoked or inactive")
    if not set(capabilities).issubset(set(session.station.capabilities)):
        raise PermissionError("This station session lacks the required capability")
    return session


@transaction.atomic
def create_station(
    *,
    business,
    actor,
    name,
    kind,
    location_id,
    capabilities,
    handling_mode=Station.HandlingMode.BOTH,
    printer_id=None,
):
    business = Business.objects.select_for_update().get(pk=business.pk)
    if business.owner_id != actor.pk:
        raise PermissionError("Only the business owner can manage stations")
    require_business_feature(business, "stations")
    enforce_business_limit(business, "workspaces", business.stations.filter(active=True).count())
    requested = set(capabilities)
    valid = {value for value, _ in STATION_CAPABILITIES}
    if not requested.issubset(valid):
        raise ValueError("Station contains an unknown capability")
    location = (
        Location.objects.get(business=business, pk=location_id, active=True)
        if location_id
        else None
    )
    printer = (
        Printer.objects.get(business=business, pk=printer_id, is_active=True)
        if printer_id
        else None
    )
    station = Station(
        business=business,
        location=location,
        name=name.strip(),
        kind=kind,
        handling_mode=handling_mode,
        printer=printer,
        capabilities=sorted(requested),
        created_by=actor,
    )
    station.full_clean()
    station.save()
    AuditEvent.objects.create(
        business=business,
        actor=actor,
        action="station.created",
        object_type="Station",
        object_id=str(station.pk),
        metadata={"kind": kind, "capabilities": sorted(requested)},
    )
    return station


def issue_station_pairing_code(*, station, actor):
    if station.business.owner_id != actor.pk or not station.active:
        raise PermissionError("Only the business owner can pair active stations")
    code = secrets.token_urlsafe(32)
    now = timezone.now()
    with transaction.atomic():
        station = Station.objects.select_for_update().get(pk=station.pk, active=True)
        StationPairingCode.objects.filter(
            station=station, consumed_at__isnull=True, expires_at__gt=now
        ).update(expires_at=now)
        pairing = StationPairingCode.objects.create(
            station=station,
            issued_by=actor,
            code_digest=hashlib.sha256(code.encode()).hexdigest(),
            expires_at=now + timedelta(minutes=5),
        )
        AuditEvent.objects.create(
            business=station.business,
            actor=actor,
            action="station.pairing_code.issued",
            object_type="Station",
            object_id=str(station.pk),
        )
    return pairing, code


def pair_station_device(*, code, label):
    code_digest = hashlib.sha256(code.encode()).hexdigest()
    token = secrets.token_urlsafe(32)
    now = timezone.now()
    with transaction.atomic():
        pairing = (
            StationPairingCode.objects.select_for_update()
            .select_related("station", "station__business")
            .filter(code_digest=code_digest)
            .first()
        )
        if (
            pairing is None
            or pairing.consumed_at
            or pairing.expires_at <= now
            or not pairing.station.active
        ):
            raise ValueError("Pairing code is invalid or expired")
        pairing.consumed_at = now
        pairing.save(update_fields=["consumed_at"])
        device = PairedDevice.objects.create(
            station=pairing.station,
            label=label.strip()[:120],
            token_digest=hashlib.sha256(token.encode()).hexdigest(),
        )
        AuditEvent.objects.create(
            business=pairing.station.business,
            actor=pairing.issued_by,
            action="station.device.paired",
            object_type="PairedDevice",
            object_id=str(device.pk),
        )
    return device, token


def revoke_paired_device(*, business, device_id, actor):
    if business.owner_id != actor.pk:
        raise PermissionError("Only the business owner can revoke station devices")
    with transaction.atomic():
        device = PairedDevice.objects.select_for_update().get(
            pk=device_id, station__business=business
        )
        if device.revoked_at is None:
            device.revoked_at = timezone.now()
            device.save(update_fields=["revoked_at"])
            AuditEvent.objects.create(
                business=business,
                actor=actor,
                action="station.device.revoked",
                object_type="PairedDevice",
                object_id=str(device.pk),
            )
    return device


def rotate_station_device_token(*, station_id, device):
    with transaction.atomic():
        dev = (
            PairedDevice.objects.select_for_update()
            .select_related("station", "station__business")
            .get(pk=device.pk, station_id=station_id, revoked_at__isnull=True)
        )
        if not dev.station.active:
            raise PermissionError("This station is inactive")
        new_token = secrets.token_urlsafe(32)
        dev.token_digest = hashlib.sha256(new_token.encode()).hexdigest()
        dev.last_seen_at = timezone.now()
        dev.save(update_fields=["token_digest", "last_seen_at"])
        AuditEvent.objects.create(
            business=dev.station.business,
            actor=None,
            action="station.device.token_rotated",
            object_type="PairedDevice",
            object_id=str(dev.pk),
            metadata={"station_id": str(station_id)},
        )
    return new_token


def _payment_fernet():
    key = settings.PAYMENT_CREDENTIALS_ENCRYPTION_KEY
    if not key:
        raise RuntimeError("Payment credential encryption is not configured")
    try:
        return Fernet(key.encode())
    except (TypeError, ValueError) as error:
        raise RuntimeError("Payment credential encryption key is invalid") from error


def save_payment_connection(*, business, actor, provider, environment, merchant_id, credentials):
    if business.owner_id != actor.pk:
        raise PermissionError("Only the business owner can manage payment connections")
    if provider not in PaymentConnection.Provider.values:
        raise ValueError("Choose a supported payment provider")
    if environment not in PaymentConnection.Environment.values:
        raise ValueError("Choose a supported provider environment")
    if not isinstance(credentials, dict) or not credentials:
        raise ValueError("Enter the provider credentials")
    ciphertext = (
        _payment_fernet()
        .encrypt(json.dumps(credentials, separators=(",", ":"), sort_keys=True).encode())
        .decode()
    )
    with transaction.atomic():
        connection, _ = PaymentConnection.objects.update_or_create(
            business=business,
            provider=provider,
            defaults={
                "environment": environment,
                "merchant_id": merchant_id.strip(),
                "credentials_ciphertext": ciphertext,
                "verified_at": None,
            },
        )
        AuditEvent.objects.create(
            business=business,
            actor=actor,
            action="payment_connection.credentials_saved",
            object_type="PaymentConnection",
            object_id=str(connection.pk),
            metadata={"provider": provider, "environment": environment},
        )
    return connection


def read_payment_credentials(*, connection, actor):
    if connection.business.owner_id != actor.pk:
        raise PermissionError("Only the business owner can access payment credentials")
    return json.loads(_payment_fernet().decrypt(connection.credentials_ciphertext.encode()))


def delete_payment_connection(*, business, connection_id, actor):
    if business.owner_id != actor.pk:
        raise PermissionError("Only the business owner can manage payment connections")
    with transaction.atomic():
        connection = PaymentConnection.objects.get(pk=connection_id, business=business)
        AuditEvent.objects.create(
            business=business,
            actor=actor,
            action="payment_connection.removed",
            object_type="PaymentConnection",
            object_id=str(connection.pk),
            metadata={"provider": connection.provider},
        )
        connection.delete()


def create_inventory_item(*, business, actor, name, unit, location_id, sku="", opening_stock=0):
    opening_stock = Decimal(str(opening_stock))
    if (
        opening_stock < 0
        or opening_stock >= Decimal("1000000000")
        or opening_stock != opening_stock.quantize(Decimal("0.001"))
    ):
        raise ValueError("Opening stock must be nonnegative with at most three decimal places")
    with transaction.atomic():
        require_business_feature(business, "inventory")
        business = Business.objects.select_for_update().get(pk=business.pk)
        membership = Membership.objects.get(business=business, user=actor, active=True)
        if not membership.allows("inventory.manage"):
            raise PermissionError("You cannot manage inventory for this business")
        location = membership.allowed_inventory_locations().get(pk=location_id, active=True)
        item = InventoryItem.objects.create(
            business=business, name=name.strip(), unit=unit.strip(), sku=sku.strip()
        )
        InventoryBalance.objects.bulk_create(
            [
                InventoryBalance(
                    item=item,
                    location=location_option,
                    quantity=opening_stock if location_option.pk == location.pk else Decimal("0"),
                )
                for location_option in business.locations.filter(active=True)
            ]
        )
        if opening_stock:
            StockMovement.objects.create(
                business=business,
                item=item,
                location=location,
                actor=actor,
                quantity_delta=opening_stock,
                reason=StockMovement.Reason.OPENING,
                request_digest=hashlib.sha256(
                    f"{item.pk}:{location.pk}:{opening_stock}".encode()
                ).hexdigest(),
            )
        AuditEvent.objects.create(
            business=business,
            actor=actor,
            action="inventory.item_created",
            object_type="InventoryItem",
            object_id=str(item.pk),
        )
    return item


def adjust_stock(
    *, business, actor, item_id, location_id, quantity_delta, reason, idempotency_key, note=""
):
    delta = Decimal(str(quantity_delta))
    if (
        delta == 0
        or delta.copy_abs() >= Decimal("1000000000")
        or delta != delta.quantize(Decimal("0.001"))
    ):
        raise ValueError("Stock change must be nonzero with at most three decimal places")
    if reason not in {
        StockMovement.Reason.RECEIVED,
        StockMovement.Reason.ADJUSTMENT,
        StockMovement.Reason.WASTE,
    }:
        raise ValueError("Choose a valid stock movement reason")
    if len(note) > 240:
        raise ValueError("Stock note is too long")
    key = UUID(str(idempotency_key))
    digest = hashlib.sha256(
        f"{item_id}:{location_id}:{delta}:{reason}:{note.strip()}".encode()
    ).hexdigest()
    with transaction.atomic():
        require_business_feature(business, "inventory")
        business = Business.objects.select_for_update().get(pk=business.pk)
        membership = Membership.objects.get(business=business, user=actor, active=True)
        if not membership.allows("inventory.manage"):
            raise PermissionError("You cannot manage inventory for this business")
        existing = StockMovement.objects.filter(business=business, idempotency_key=key).first()
        if existing:
            if existing.request_digest != digest:
                raise ValueError("Idempotency key was reused for another stock change")
            return existing
        item = InventoryItem.objects.get(business=business, pk=item_id, active=True)
        location = membership.allowed_inventory_locations().get(pk=location_id, active=True)
        movement = record_inventory_movement(
            business=business, actor=actor, item=item, location=location, delta=delta, reason=reason,
            idempotency_key=key, request_digest=digest, note=note.strip(),
        )
        AuditEvent.objects.create(
            business=business, actor=actor, action="inventory.stock_adjusted", object_type="StockMovement",
            object_id=str(movement.pk),
            metadata={"item": item.name, "delta": str(delta), "location": location.name},
        )
    return movement


def record_inventory_movement(*, business, actor, item, location, delta, reason, receiving_line=None, **details):
    # Call inside the inventory transaction after validating scope.
    balance, _ = InventoryBalance.objects.get_or_create(
        item=item, location=location, defaults={"quantity": Decimal("0")}
    )
    balance = InventoryBalance.objects.select_for_update().get(pk=balance.pk)
    new_quantity = balance.quantity + delta
    if new_quantity < 0:
        raise ValueError("Stock cannot fall below zero")
    if new_quantity >= Decimal("1000000000"):
        raise ValueError("This receipt would exceed the supported stock balance.")
    balance.quantity = new_quantity
    update_fields = ["quantity"]
    if receiving_line is not None:
        balance.last_purchase_unit_cost = receiving_line.unit_cost
        update_fields.append("last_purchase_unit_cost")
    balance.save(update_fields=update_fields)
    movement = StockMovement.objects.create(
        business=business, item=item, location=location, actor=actor, quantity_delta=delta,
        reason=reason, receiving_line=receiving_line, **details,
    )
    _queue_inventory_event(business=business, item=item, location=location, balance=balance)
    return movement


def create_order(
    *,
    business,
    actor,
    items,
    idempotency_key=None,
    order_type=Order.OrderType.TAKEAWAY,
    table_label="",
    note="",
    customer_id=None,
    station_device=None,
    table=None,
    guest_location=None,
    location_id=None,
    guest_tracking_token=None,
    payment_method=Order.PaymentMethod.COUNTER,
    payment_provider="",
    expected_total=None,
    expected_lines=None,
    voucher_code="",
    expected_voucher=None,
    voucher_quote_token="",
):
    """Create an order with server-side price snapshots."""
    key = UUID(str(idempotency_key)) if idempotency_key else None
    voucher_code = normalize_voucher_code(voucher_code) if voucher_code else ""
    items = [
        (UUID(str(item[0])), int(item[1]), normalize_options(item[2] if len(item) == 3 else []))
        for item in items
    ]
    if not items:
        raise ValueError("An order needs at least one item")
    if any(quantity < 1 or quantity > 99 for _, quantity, _ in items):
        raise ValueError("Quantity must be between 1 and 99")
    if order_type not in Order.OrderType.values:
        raise ValueError("Choose dine-in or takeaway")
    if len(note) > 500 or len(table_label) > 40:
        raise ValueError("Order note or table name is too long")
    if payment_method not in Order.PaymentMethod.values:
        raise ValueError("Choose a supported payment method")
    guest = table is not None or guest_location is not None
    if guest and (actor is not None or customer_id or station_device is not None or location_id):
        raise PermissionError("Guest orders use the location authorized by the menu link")
    if guest and not guest_tracking_token:
        raise ValueError("Guest tracking token is required")
    if guest_location is not None and (
        table is not None or order_type != Order.OrderType.TAKEAWAY or not guest_location.takeaway_version
    ):
        raise ValueError("Choose an available order type.")
    if payment_method == Order.PaymentMethod.QRIS and not payment_provider:
        raise ValueError("Choose a verified QRIS provider")
    if table is not None:
        table_label = table.name if order_type == Order.OrderType.DINE_IN else ""
    elif guest_location is not None:
        table_label = ""
    if order_type == Order.OrderType.DINE_IN and not table_label.strip():
        raise ValueError("Enter a table name for dine-in orders")
    digest = hashlib.sha256(
        json.dumps(
            [
                sorted(
                    (str(product_id), quantity, options) if options else (str(product_id), quantity)
                    for product_id, quantity, options in items
                ),
                order_type,
                table_label.strip(),
                note.strip(),
                str(customer_id or ""),
                str(table.pk) if table else "",
                payment_method,
                payment_provider if payment_method == Order.PaymentMethod.QRIS else "",
                str(guest_location.pk if guest_location is not None else location_id or ""),
                *([voucher_code] if voucher_code else []),
            ],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    with transaction.atomic():
        business = Business.objects.select_for_update().get(pk=business.pk)
        location = None
        if table is not None:
            table = DiningTable.objects.select_related("location").select_for_update().get(
                pk=table.pk, business=business, qr_version=table.qr_version,
                active=True, location__business=business, location__active=True,
            )
            location = table.location
            if order_type == Order.OrderType.TAKEAWAY:
                if not location.takeaway_version:
                    raise ValueError("Takeaway is unavailable at this location.")
                table = None
            elif table_label != table.name:
                raise ValueError("The table details changed. Review your order and try again.")
        elif guest_location is not None:
            location = Location.objects.select_for_update().get(
                pk=guest_location.pk, business=business, active=True,
                takeaway_version=guest_location.takeaway_version,
            )
        if station_device is not None:
            station_device = _station_session(station_device, "orders.create", "catalog.view")
            if station_device.station.business_id != business.pk:
                raise PermissionError("This station belongs to another business")
            if customer_id:
                raise PermissionError("This station cannot select customers")
            location = station_device.station.location
            actor = None
        else:
            if not guest:
                if location_id:
                    location = Location.objects.get(business=business, pk=location_id, active=True)
                membership = Membership.objects.get(business=business, user=actor, active=True)
                if not membership.allows("orders.create") or not membership.allows("catalog.view"):
                    raise PermissionError("You cannot create orders for this business")
                if customer_id and not membership.allows("customers.view"):
                    raise PermissionError("You cannot select customers for this business")
        if key:
            existing = Order.objects.filter(business=business, idempotency_key=key).first()
            if existing:
                if existing.idempotency_digest != digest:
                    raise ValueError("Idempotency key was reused with a different request")
                if guest and existing.guest_tracking_digest != hashlib.sha256(
                    guest_tracking_token.encode()
                ).hexdigest():
                    raise ValueError("Order retry token did not match")
                return existing
        require_business_feature(business, "qr_ordering" if guest else "pos")
        customer = Customer.objects.get(business=business, pk=customer_id) if customer_id else None
        order = Order.objects.create(
            business=business,
            location=location,
            table=table,
            created_by=actor,
            created_by_device=station_device,
            customer=customer,
            customer_name_snapshot=customer.name if customer else "",
            customer_email_snapshot=customer.email if customer else "",
            customer_phone_snapshot=customer.phone if customer else "",
            order_type=order_type,
            source=(
                Order.Source.TABLE_QR
                if table
                else Order.Source.GUEST_PICKUP
                if guest
                else Order.Source.STATION
                if station_device
                else Order.Source.POS
            ),
            table_label=table_label.strip(),
            note=note.strip(),
            idempotency_digest=digest,
            payment_method=payment_method,
            payment_provider=payment_provider if payment_method == Order.PaymentMethod.QRIS else "",
            guest_tracking_digest=(
                hashlib.sha256(guest_tracking_token.encode()).hexdigest()
                if guest else None
            ),
            **({"idempotency_key": key} if key else {}),
        )
        priced = price_order_items(business=business, items=items, location=location)
        total = sum((line.total for line in priced), Decimal("0.00"))
        if voucher_code:
            require_business_feature(business, "promotions")
            if not guest:
                try:
                    approved = signing.loads(voucher_quote_token, salt="veyra-pos-voucher", max_age=600)
                except signing.BadSignature:
                    raise ValueError("Apply the voucher to review the current discount first.") from None
                if not isinstance(approved, dict) or (
                    approved.get("business") != str(business.pk) or approved.get("code") != voucher_code
                ):
                    raise ValueError("Apply the voucher to review the current discount first.")
            applied = calculate_voucher(
                business=business, code=voucher_code, lines=priced, location=location,
                order_type=order_type, customer=customer, lock=True,
            )
            if guest and expected_voucher != applied.fingerprint():
                raise ValueError("Voucher changed. Review the updated total and place your order again.")
            if not guest:
                if approved != pos_voucher_approval(
                    business=business, location=location, order_type=order_type,
                    customer=customer, lines=priced, applied=applied,
                ):
                    raise ValueError("Voucher or prices changed. Apply the voucher again to review the total.")
            total = applied.total
            if payment_method == Order.PaymentMethod.QRIS and (
                total <= 0 or (payment_provider == "midtrans" and total != total.to_integral_value())
            ):
                raise ValueError("Choose counter payment for this voucher total.")
            redemption = applied.voucher.redemptions.create(order=order)
            order.voucher_discount = applied.discount
            order.voucher_snapshot = {
                **applied.snapshot(), "voucher_id": applied.voucher.pk, "redemption_id": str(redemption.pk),
                "redeemed_at": redemption.created_at.isoformat(),
            }
            AuditEvent.objects.create(
                business=business, actor=actor, action="voucher.redeemed",
                object_type="Voucher", object_id=str(applied.voucher.pk),
                metadata={"order_id": str(order.pk), "redemption_id": str(redemption.pk)},
            )
        if (expected_total is not None and total != expected_total) or (
            expected_lines is not None and [line.approval() for line in priced] != expected_lines
        ):
            raise ValueError("Menu prices changed. Review the updated total and place your order again.")
        if total > Decimal("9999999999.99"):
            raise ValueError("Order total exceeds the supported amount")
        for item in priced:
            line = OrderLine.objects.create(
                order=order, product=item.product, product_name=item.product.name,
                unit_price=item.unit_price, base_unit_price=item.base_unit_price,
                modifiers=item.modifiers, quantity=item.quantity,
            )
            for promotion in item.price.promotions:
                OrderLinePromotion.objects.create(
                    line=line, promotion_name=promotion.name,
                    discount_amount=promotion.discount_amount * item.quantity,
                )
        Order.objects.filter(pk=order.pk).update(
            total=total, voucher_discount=order.voucher_discount, voucher_snapshot=order.voucher_snapshot,
        )
        AuditEvent.objects.create(
            business=business,
            actor=actor,
            action="order.created",
            object_type="Order",
            object_id=str(order.pk),
            metadata={"station_device_id": str(station_device.pk)} if station_device else {},
        )
        order.total = total
        _queue_order_event(order, "created")
    return order


def transition_order(*, business, order_id, status, actor=None, station_device=None):
    allowed = {
        Order.Status.NEW: {Order.Status.PREPARING, Order.Status.CANCELLED},
        Order.Status.PREPARING: {Order.Status.READY, Order.Status.CANCELLED},
        Order.Status.READY: {Order.Status.COMPLETED},
        Order.Status.COMPLETED: set(),
        Order.Status.CANCELLED: set(),
    }
    with transaction.atomic():
        if station_device is not None:
            station_device = _station_session(station_device, "orders.status.update", "orders.view")
            if station_device.station.business_id != business.pk:
                raise PermissionError("This station belongs to another business")
            order_filter = {
                "business": business,
                "pk": order_id,
                **(
                    {"location_id": station_device.station.location_id}
                    if station_device.station.location_id
                    else {}
                ),
            }
            actor = None
        else:
            membership = Membership.objects.get(business=business, user=actor, active=True)
            if not membership.allows("orders.manage") or not membership.allows("orders.view"):
                raise PermissionError("You cannot manage orders for this business")
            order_filter = {"business": business, "pk": order_id}
        order = Order.objects.select_for_update().get(**order_filter)
        if (
            status == Order.Status.PREPARING
            and order.source in Order.GUEST_SOURCES
            and order.payment_method == Order.PaymentMethod.QRIS
            and order.payment_status != Order.PaymentStatus.PAID
        ):
            raise ValueError("Guest QRIS orders cannot enter preparation before payment is confirmed")
        if status not in allowed[order.status]:
            raise ValueError("Invalid order status transition")
        if status == Order.Status.PREPARING:
            _consume_order_recipe(order, actor)
        elif status == Order.Status.CANCELLED and order.status == Order.Status.PREPARING:
            _return_order_recipe(order, actor)
        Order.objects.filter(pk=order.pk, status=order.status).update(status=status)
        AuditEvent.objects.create(
            business=business,
            actor=actor,
            action="order.status_changed",
            object_type="Order",
            object_id=str(order.pk),
            metadata={
                "status": status,
                **({"station_device_id": str(station_device.pk)} if station_device else {}),
            },
        )
        order.status = status
        _queue_order_event(order, "status_changed")
        if status == Order.Status.PREPARING:
            route_order_to_stations(order)
        return order


def record_order_payment(
    *, business, order_id, method, actor, idempotency_key, cash_received=None, reference=""
):
    if method not in OrderTender.Method.values:
        raise ValueError("Choose cash or card")
    key = UUID(str(idempotency_key))
    try:
        received = (
            Decimal(str(cash_received))
            if method == OrderTender.Method.CASH and cash_received not in (None, "")
            else None
        )
    except InvalidOperation as error:
        raise ValueError("Cash received must be a valid amount") from error
    if not isinstance(reference, str):
        raise ValueError("Payment reference must be text")
    reference = reference.strip()
    if len(reference) > 120:
        raise ValueError("Payment reference is too long")
    with transaction.atomic():
        membership = Membership.objects.get(business=business, user=actor, active=True)
        if not membership.allows("orders.manage") or not membership.allows("orders.view"):
            raise PermissionError("You cannot record payments for this business")
        order = Order.objects.select_for_update().get(business=business, pk=order_id)
        existing = OrderTender.objects.filter(order=order, idempotency_key=key).first()
        if existing:
            if (
                existing.method != method
                or existing.cash_received != received
                or existing.reference != reference
            ):
                raise ValueError("Payment retry does not match the original request")
            return existing
        if order.payment_status == Order.PaymentStatus.PAID:
            raise ValueError("This order is already paid")
        if order.status in (Order.Status.COMPLETED, Order.Status.CANCELLED):
            raise ValueError("Payment cannot be recorded for a closed order")
        if order.payment_method == Order.PaymentMethod.QRIS:
            raise ValueError("Orders using QRIS must be settled through the provider")
        if method == OrderTender.Method.CASH:
            if (
                received is None
                or not received.is_finite()
                or received < order.total
                or received >= Decimal("10000000000")
            ):
                raise ValueError("Cash received must cover the order total")
            change_due = received - order.total
        else:
            received = None
            change_due = Decimal("0.00")
        tender = OrderTender.objects.create(
            order=order,
            method=method,
            amount=order.total,
            cash_received=received,
            change_due=change_due,
            reference=reference,
            recorded_by=actor,
            idempotency_key=key,
        )
        order.payment_method = method
        order.payment_status = Order.PaymentStatus.PAID
        order.save(update_fields=["payment_method", "payment_status", "updated_at"])
        AuditEvent.objects.create(
            business=business,
            actor=actor,
            action="order.payment_recorded",
            object_type="OrderTender",
            object_id=str(tender.pk),
            metadata={
                "order_id": str(order.pk),
                "method": method,
                "amount": str(tender.amount),
                "change_due": str(tender.change_due),
            },
        )
        _queue_order_event(order, "payment_recorded")
    return tender


def create_printer(*, business, actor, name, printer_type="network_escpos", connection_params=None, paper_width="80mm", location_id=None):
    if business.owner_id != actor.pk:
        raise PermissionError("Only the business owner can configure printers")
    location = (
        Location.objects.get(business=business, pk=location_id, active=True)
        if location_id
        else None
    )
    printer = Printer(
        business=business,
        location=location,
        name=name.strip(),
        printer_type=printer_type,
        connection_params=connection_params or {},
        paper_width=paper_width,
    )
    printer.full_clean()
    printer.save()
    AuditEvent.objects.create(
        business=business,
        actor=actor,
        action="printer.created",
        object_type="Printer",
        object_id=str(printer.pk),
        metadata={"name": printer.name, "type": printer_type},
    )
    return printer


def prep_item(line):
    return {
        "name": line.product_name,
        "quantity": line.quantity,
        "notes": ", ".join(f"{option['group']}: {option['name']}" for option in line.modifiers),
    }


def format_prep_ticket(*, order, items, station=None, is_reprint=False):
    order_num = f"#{str(order.pk)[:6].upper()}"
    station_name = station.name if station else "General Prep"
    return {
        "header": "*** PREPARATION TICKET - NOT A RECEIPT ***",
        "station": station_name,
        "order_id": str(order.pk),
        "order_number": order_num,
        "order_type": order.get_order_type_display(),
        "table_label": order.table_label or "-",
        "note": order.note or "",
        "created_at": order.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        "is_reprint": is_reprint,
        "items": [
            {
                "name": item["name"],
                "quantity": item["quantity"],
                "notes": item.get("notes", ""),
            }
            for item in items
        ],
    }


def format_receipt(*, order):
    lines = list(order.lines.prefetch_related("promotions").order_by("pk"))
    subtotal = sum((line.base_unit_price * line.quantity for line in lines), Decimal("0.00"))
    tender = getattr(order, "tender", None)
    cashier = tender.recorded_by if tender else order.created_by
    try:
        local_time = timezone.localtime(order.created_at, ZoneInfo(order.business.timezone_name))
    except (ZoneInfoNotFoundError, ValueError):
        local_time = order.created_at
    return {
        "header": "*** CUSTOMER RECEIPT ***",
        "business_name": order.business.name,
        "location": order.location.name if order.location else "",
        "order_id": str(order.pk),
        "receipt_number": str(order.pk).upper(),
        "order_number": f"#{str(order.pk)[:8].upper()}",
        "order_type": order.get_order_type_display(),
        "table_label": order.table_label,
        "created_at": local_time.isoformat(timespec="minutes"),
        "cashier": (cashier.get_full_name() or f"Staff {cashier.pk}") if cashier else "",
        "items": [
            {
                "name": line.product_name,
                "quantity": line.quantity,
                "modifiers": line.modifiers,
                "base_unit_price": str(line.base_unit_price),
                "unit_price": str(line.unit_price),
                "line_total": str(line.line_total),
                "promotions": [
                    {"name": promotion.promotion_name, "discount": str(promotion.discount_amount)}
                    for promotion in line.promotions.all()
                ],
            }
            for line in lines
        ],
        "subtotal": str(subtotal),
        "discount": str(subtotal - order.total),
        "promotion_discount": str(subtotal - order.total - order.voucher_discount),
        "voucher": {
            "code": order.voucher_snapshot["code"],
            "discount": str(order.voucher_discount),
        } if order.voucher_snapshot else {},
        "total": str(order.total),
        "payment_method": order.get_payment_method_display(),
        "payment_status": order.get_payment_status_display(),
        "amount_paid": str(order.total) if order.payment_status == Order.PaymentStatus.PAID else None,
        "cash_received": str(tender.cash_received) if tender and tender.cash_received is not None else None,
        "change_due": str(tender.change_due) if tender and tender.method == OrderTender.Method.CASH else None,
    }


def create_print_job(
    *,
    business,
    order,
    job_type=PrintJob.JobType.PREP_TICKET,
    station=None,
    printer=None,
    is_reprint=False,
    actor=None,
    items=None,
):
    if items is None:
        items = [prep_item(line) for line in order.lines.all()]

    if job_type == PrintJob.JobType.PREP_TICKET:
        payload = format_prep_ticket(order=order, items=items, station=station, is_reprint=is_reprint)
    else:
        payload = format_receipt(order=order)

    resolved_printer = printer or (station.printer if station else None)
    if is_reprint:
        idempotency_key = uuid.uuid4()
    else:
        hash_seed = f"{order.pk}:{station.pk if station else 'all'}:{job_type}"
        idempotency_key = UUID(hashlib.md5(hash_seed.encode()).hexdigest())

    existing = PrintJob.objects.filter(business=business, idempotency_key=idempotency_key).first()
    if existing:
        return existing

    job = PrintJob.objects.create(
        business=business,
        station=station,
        printer=resolved_printer,
        order=order,
        job_type=job_type,
        ticket_payload=payload,
        idempotency_key=idempotency_key,
        status=PrintJob.Status.PENDING,
    )
    AuditEvent.objects.create(
        business=business,
        actor=actor,
        action="print_job.created",
        object_type="PrintJob",
        object_id=str(job.pk),
        metadata={"job_type": job_type, "is_reprint": is_reprint},
    )
    return job


def route_order_to_stations(order):
    """Route order lines to operational stations and queue prep tickets."""
    business = order.business
    stations_qs = Station.objects.filter(business=business, active=True)
    if order.location_id:
        stations_qs = stations_qs.filter(
            models.Q(location__isnull=True) | models.Q(location_id=order.location_id)
        )
    stations = list(stations_qs)
    if not stations:
        return []

    kitchen_station = next((s for s in stations if s.kind == Station.Kind.KITCHEN), None)
    bar_station = next((s for s in stations if s.kind == Station.Kind.BAR), None)

    # Group order lines by assigned station
    station_items = {}
    for line in order.lines.select_related("product__station", "product__category").all():
        product = line.product
        target = product.station
        if not target or not target.active:
            cat_name = product.category.name.lower() if product.category else ""
            prod_name = product.name.lower()
            if any(term in cat_name or term in prod_name for term in ("drink", "coffee", "tea", "beverage", "bar")):
                target = bar_station or kitchen_station or stations[0]
            else:
                target = kitchen_station or bar_station or stations[0]

        if target not in station_items:
            station_items[target] = []
        station_items[target].append(prep_item(line))

    created_jobs = []
    for station, items in station_items.items():
        if station.handling_mode in (Station.HandlingMode.PRINTED, Station.HandlingMode.BOTH):
            try:
                job = create_print_job(
                    business=business,
                    order=order,
                    job_type=PrintJob.JobType.PREP_TICKET,
                    station=station,
                    printer=station.printer,
                    is_reprint=False,
                    items=items,
                )
                created_jobs.append(job)
            except Exception:
                # Printer failure must never silently discard or fail an order
                pass

    return created_jobs


def reprint_order_ticket(*, business, order_id, station_id=None, actor=None, station_device=None):
    if station_device is not None:
        station_device = _station_session(station_device, "orders.view")
        if station_device.station.business_id != business.pk:
            raise PermissionError("This station belongs to another business")
        station = station_device.station
    elif actor is not None:
        membership = Membership.objects.get(business=business, user=actor, active=True)
        if not membership.allows("orders.view"):
            raise PermissionError("You cannot view or reprint orders")
        station = Station.objects.filter(business=business, pk=station_id).first() if station_id else None
    else:
        station = None

    order = Order.objects.get(business=business, pk=order_id)
    items = [prep_item(line) for line in order.lines.all()]

    job = create_print_job(
        business=business,
        order=order,
        job_type=PrintJob.JobType.PREP_TICKET,
        station=station,
        printer=station.printer if station else None,
        is_reprint=True,
        actor=actor,
        items=items,
    )
    AuditEvent.objects.create(
        business=business,
        actor=actor,
        action="order.ticket_reprinted",
        object_type="Order",
        object_id=str(order.pk),
        metadata={"print_job_id": str(job.pk)},
    )
    return job


def acknowledge_print_job(*, business, job_id, status, error="", station_device=None, actor=None):
    with transaction.atomic():
        job_filter = {"business": business, "pk": job_id}
        if station_device is not None:
            station_device = _station_session(station_device, "orders.view")
            job_filter["station"] = station_device.station
        job = PrintJob.objects.select_for_update().get(**job_filter)
        if status not in (PrintJob.Status.PRINTED, PrintJob.Status.FAILED, PrintJob.Status.PRINTING):
            raise ValueError("Invalid print job status")
        if job.job_type == PrintJob.JobType.RECEIPT:
            if job.status == status:
                return job
            if job.status not in (PrintJob.Status.PENDING, PrintJob.Status.PRINTING):
                raise ValueError("This receipt job is closed. Submit a new print request.")
        job.status = status
        if status == PrintJob.Status.PRINTED:
            job.printed_at = timezone.now()
            job.last_error = ""
        elif status == PrintJob.Status.FAILED:
            job.attempts += 1
            job.last_error = str(error)[:300]
        job.save()
        if job.job_type == PrintJob.JobType.RECEIPT:
            _queue_order_event(job.order, "receipt.updated")
        AuditEvent.objects.create(
            business=business,
            actor=actor,
            action="print_job.acknowledged",
            object_type="PrintJob",
            object_id=str(job.pk),
            metadata={"status": status, "error": error},
        )
    return job


def verify_payment_connection(*, business, connection_id, actor):
    if business.owner_id != actor.pk:
        raise PermissionError("Only the business owner can verify payment connections")
    connection = PaymentConnection.objects.get(pk=connection_id, business=business)
    credentials = read_payment_credentials(connection=connection, actor=actor)
    adapter = get_payment_adapter(connection.provider)
    valid, message = adapter.verify_credentials(connection=connection, credentials=credentials)
    if valid:
        connection.verified_at = timezone.now()
        connection.save(update_fields=["verified_at"])
        AuditEvent.objects.create(
            business=business,
            actor=actor,
            action="payment_connection.verified",
            object_type="PaymentConnection",
            object_id=str(connection.pk),
            metadata={"provider": connection.provider},
        )
    return valid, message


def initiate_qris_payment(
    *, business, order_id, provider, actor=None, station_device=None, guest_tracking_token=None
):
    if guest_tracking_token is not None:
        pass
    elif station_device is not None:
        station_device = _station_session(station_device, "orders.view")
        if station_device.station.business_id != business.pk:
            raise PermissionError("This station belongs to another business")
    else:
        membership = Membership.objects.get(business=business, user=actor, active=True)
        if not membership.allows("orders.view"):
            raise PermissionError("You cannot initiate payments for this business")

    with transaction.atomic():
        order = Order.objects.select_for_update().get(business=business, pk=order_id)
        if guest_tracking_token is not None:
            token_digest = hashlib.sha256(guest_tracking_token.encode()).hexdigest()
            if (
                order.source not in Order.GUEST_SOURCES
                or order.guest_tracking_digest != token_digest
                or order.payment_method != Order.PaymentMethod.QRIS
                or order.payment_provider != provider
            ):
                raise PermissionError("This tracking link cannot access the payment")
        if order.status in (Order.Status.COMPLETED, Order.Status.CANCELLED):
            raise ValueError("Cannot initiate payment for completed or cancelled order")
        if order.payment_status == Order.PaymentStatus.PAID:
            raise ValueError("This order has already been paid")

        connection = PaymentConnection.objects.get(
            business=business, provider=provider, verified_at__isnull=False
        )
        pending = order.payments.filter(
            status=PaymentTransaction.Status.PENDING,
            expires_at__gt=timezone.now(),
            provider=provider,
        ).first()
        if pending:
            return pending
        expired = order.payments.filter(
            status=PaymentTransaction.Status.PENDING, expires_at__lte=timezone.now()
        )
        if expired.exists():
            expired.update(status=PaymentTransaction.Status.EXPIRED)
            order.payment_status = Order.PaymentStatus.EXPIRED
            order.save(update_fields=["payment_status", "updated_at"])
            _queue_order_event(order, "payment_expired")
        if order.payments.filter(status=PaymentTransaction.Status.PENDING).exists():
            raise ValueError("An existing QRIS payment is still pending")
        credentials = json.loads(_payment_fernet().decrypt(connection.credentials_ciphertext.encode()))

        adapter = get_payment_adapter(provider)
        result = adapter.create_qris(
            connection=connection,
            credentials=credentials,
            order=order,
            amount=order.total,
            expiry_minutes=10,
        )
        if not result.success:
            raise ValueError(result.error or "Failed to generate QRIS payment")

        tx = PaymentTransaction.objects.create(
            business=business,
            order=order,
            connection=connection,
            provider=provider,
            environment=connection.environment,
            provider_transaction_id=result.transaction_id,
            merchant_reference=result.merchant_reference,
            amount=result.amount,
            currency=result.currency,
            status=result.status,
            qr_payload=result.qr_payload,
            qr_image_url=result.qr_image_url,
            expires_at=result.expires_at or (timezone.now() + timedelta(minutes=10)),
            raw_response=result.raw_response,
        )
        order.payment_status = Order.PaymentStatus.PENDING
        order.save(update_fields=["payment_status", "updated_at"])
        AuditEvent.objects.create(
            business=business,
            actor=actor,
            action="payment.initiated",
            object_type="PaymentTransaction",
            object_id=str(tx.pk),
            metadata={
                "order_id": str(order.pk),
                "provider": provider,
                "amount": str(result.amount),
                "merchant_reference": result.merchant_reference,
            },
        )
        _queue_order_event(order, "payment_initiated")
    return tx


def process_payment_webhook(*, provider, headers, raw_body):
    body_bytes = raw_body if isinstance(raw_body, bytes) else str(raw_body).encode()
    digest = hashlib.sha256(body_bytes).hexdigest()

    try:
        payload = json.loads(body_bytes.decode("utf-8"))
    except Exception as error:
        raise ValueError(f"Invalid JSON webhook payload: {error}")

    adapter = get_payment_adapter(provider)
    provider_tx_id, merchant_ref, amount, status = adapter.parse_webhook_status(payload)

    existing_event = PaymentWebhookEvent.objects.filter(request_digest=digest).first()
    if existing_event and existing_event.processed:
        tx = None
        if merchant_ref:
            tx = PaymentTransaction.objects.filter(merchant_reference=merchant_ref).first()
        if not tx and provider_tx_id:
            tx = PaymentTransaction.objects.filter(provider_transaction_id=provider_tx_id).first()
        return tx

    tx = None
    if merchant_ref:
        tx = PaymentTransaction.objects.filter(merchant_reference=merchant_ref).first()
    if not tx and provider_tx_id:
        tx = PaymentTransaction.objects.filter(provider_transaction_id=provider_tx_id).first()

    if not tx:
        PaymentWebhookEvent.objects.create(
            provider=provider,
            request_digest=digest,
            headers=headers,
            payload=payload,
            is_valid_signature=False,
            error=f"No matching PaymentTransaction for ref '{merchant_ref}' or tx '{provider_tx_id}'",
        )
        raise ValueError("Payment transaction not found for webhook")

    connection = tx.connection
    credentials = json.loads(_payment_fernet().decrypt(connection.credentials_ciphertext.encode()))
    is_valid, sig_err = adapter.verify_webhook_signature(
        connection=connection,
        credentials=credentials,
        payload=payload,
        headers=headers,
        raw_body=body_bytes,
    )

    if not is_valid:
        PaymentWebhookEvent.objects.create(
            business=tx.business,
            provider=provider,
            request_digest=digest,
            headers=headers,
            payload=payload,
            is_valid_signature=False,
            error=sig_err or "Invalid signature",
        )
        raise PermissionError(f"Webhook signature rejected: {sig_err}")

    with transaction.atomic():
        PaymentWebhookEvent.objects.create(
            business=tx.business,
            provider=provider,
            request_digest=digest,
            headers=headers,
            payload=payload,
            is_valid_signature=True,
            processed=True,
        )
        tx = PaymentTransaction.objects.select_for_update().get(pk=tx.pk)
        tx.status = status
        if provider_tx_id and not tx.provider_transaction_id:
            tx.provider_transaction_id = provider_tx_id

        if status == "paid":
            tx.paid_at = timezone.now()
            order = Order.objects.select_for_update().get(pk=tx.order_id)
            order.payment_status = Order.PaymentStatus.PAID
            order.payment_method = Order.PaymentMethod.QRIS
            order.payment_provider = provider
            order.save(update_fields=["payment_status", "payment_method", "payment_provider", "updated_at"])
            if order.status == Order.Status.NEW:
                order.status = Order.Status.PREPARING
                order.save(update_fields=["status"])
                route_order_to_stations(order)
            AuditEvent.objects.create(
                business=tx.business,
                actor=None,
                action="payment.confirmed",
                object_type="PaymentTransaction",
                object_id=str(tx.pk),
                metadata={
                    "order_id": str(tx.order_id),
                    "amount": str(tx.amount),
                    "provider": provider,
                },
            )
            _queue_order_event(order, "payment_confirmed")
        elif status in ("failed", "expired"):
            order = Order.objects.select_for_update().get(pk=tx.order_id)
            order.payment_status = (
                Order.PaymentStatus.FAILED if status == "failed" else Order.PaymentStatus.EXPIRED
            )
            order.save(update_fields=["payment_status", "updated_at"])
            AuditEvent.objects.create(
                business=tx.business,
                actor=None,
                action=f"payment.{status}",
                object_type="PaymentTransaction",
                object_id=str(tx.pk),
                metadata={"order_id": str(tx.order_id), "provider": provider},
            )
            _queue_order_event(order, f"payment_{status}")

        tx.save()
    return tx
