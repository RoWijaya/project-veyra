import asyncio
from contextlib import suppress
from decimal import Decimal

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .permissions import (
    guest_menu_business_id,
    inventory_stream_business_id,
    order_for_tracking_cookie,
    order_stream_business_id,
    order_stream_snapshot,
    order_tracking_cookie_name,
    order_tracking_group_name,
)

ACCESS_RECHECK_SECONDS = 30


class AuthorizedStream(AsyncJsonWebsocketConsumer):
    async def subscribe(self, group):
        self.group_name = group
        await self.channel_layer.group_add(group, self.channel_name)
        await self.accept()
        self.access_check = asyncio.create_task(self.watch_access())

    async def watch_access(self):
        try:
            while True:
                await asyncio.sleep(ACCESS_RECHECK_SECONDS)
                if not await self.check_access():
                    return
        except Exception:
            await self.close(code=1011)

    async def check_access(self):
        session_check = self.scope.get("veyra_session_authorized")
        if (session_check and not await session_check()) or not await self.authorized():
            await self.close(code=4403)
            return False
        return True

    async def receive_json(self, content, **kwargs):
        await self.check_access()

    async def disconnect(self, close_code):
        task = getattr(self, "access_check", None)
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)


class BusinessStream(AuthorizedStream):
    async def connect(self):
        user = self.scope["user"]
        if not user.is_authenticated:
            await self.close(code=4403)
            return
        self.user_id = user.pk
        self.slug = self.scope["url_route"]["kwargs"]["slug"]
        business_id = await self.access()
        if not business_id:
            await self.close(code=4403)
            return
        self.business_id = business_id
        await self.subscribe(f"{self.group_prefix}_{business_id}")

    async def authorized(self):
        return await self.access() == self.business_id


class BusinessOrdersConsumer(BusinessStream):
    group_prefix = "business"

    async def access(self):
        return await database_sync_to_async(order_stream_business_id)(self.user_id, self.slug)

    async def menu_update(self, event):
        if await self.check_access():
            await self.send_json({"event_id": event["event_id"], "event": "catalog.changed"})

    async def order_update(self, event):
        if not await self.check_access():
            return
        order_id = event.get("order_id")
        order = await database_sync_to_async(order_stream_snapshot)(self.user_id, self.slug, order_id)
        if order:
            await self.send_json({"event_id": event["event_id"], "event": event["event"], "order_id": order_id, **order})


class GuestOrderTrackingConsumer(AuthorizedStream):
    async def connect(self):
        self.order_id = str(self.scope["url_route"]["kwargs"]["order_id"])
        cookie_name = order_tracking_cookie_name(self.order_id)
        self.tracking_cookie = self.scope.get("cookies", {}).get(cookie_name)
        if not await self.authorized():
            await self.close(code=4403)
            return
        await self.subscribe(order_tracking_group_name(self.order_id))
        order = await self.authorized_order()
        if order is None:
            await self.close(code=4403)
            return
        await self.send_json(order)

    async def authorized(self):
        return await database_sync_to_async(order_for_tracking_cookie)(self.order_id, self.tracking_cookie) is not None

    @database_sync_to_async
    def authorized_order(self):
        order = order_for_tracking_cookie(self.order_id, self.tracking_cookie, with_refunds=True)
        if order is None:
            return None
        return {
            "event_id": "snapshot",
            "event": "snapshot",
            "order_id": str(order.pk),
            "status": order.status,
            "payment_status": order.payment_status,
            "payment_method": order.payment_method,
            "payment_needs_review": order.qris_needs_review,
            "refunded_amount": str(order.refunded_amount.quantize(Decimal("0.01"))),
            "total": str(order.total),
        }

    async def order_update(self, event):
        if event.get("order_id") != self.order_id:
            return
        order = await self.authorized_order()
        if order is None:
            await self.close(code=4403)
            return
        await self.send_json({**order, "event_id": event["event_id"], "event": event["event"]})


class GuestMenuConsumer(AuthorizedStream):
    async def access(self):
        return await database_sync_to_async(guest_menu_business_id)(self.token)

    async def authorized(self):
        return f"guest_menu_{await self.access()}" == self.group_name

    async def connect(self):
        self.token = self.scope["url_route"]["kwargs"]["token"]
        business_id = await self.access()
        if not business_id:
            await self.close(code=4403)
            return
        await self.subscribe(f"guest_menu_{business_id}")

    async def menu_update(self, event):
        if await self.check_access():
            await self.send_json({"event_id": event["event_id"], "event": "menu.update"})


class BusinessInventoryConsumer(BusinessStream):
    group_prefix = "inventory"

    async def access(self, location_id=None):
        return await database_sync_to_async(inventory_stream_business_id)(self.user_id, self.slug, location_id)

    async def may_read_location(self, event):
        return await self.check_access() and bool(
            event.get("location_id") and await self.access(str(event["location_id"])) == self.business_id
        )

    async def inventory_update(self, event):
        if await self.may_read_location(event):
            await self.send_json({
                "event_id": event["event_id"],
                "event": "inventory.update",
                "item_id": event["item_id"],
                "location_id": event["location_id"],
                "quantity": event["quantity"],
                "last_purchase_unit_cost": event.get("last_purchase_unit_cost"),
                "current_cost_estimate": event.get("current_cost_estimate"),
            })


class BusinessPurchasingConsumer(BusinessInventoryConsumer):
    group_prefix = "purchasing"

    async def purchase_update(self, event):
        if await self.may_read_location(event):
            await self.send_json({
                "event": "purchase.update",
                "event_id": event["event_id"],
                "purchase_id": event.get("purchase_id"),
                "location_id": event["location_id"],
            })


class BusinessBillingConsumer(BusinessStream):
    group_prefix = "billing"

    @database_sync_to_async
    def access(self):
        from .models import Business

        value = Business.objects.filter(
            slug=self.slug, owner_id=self.user_id, owner__is_active=True,
        ).values_list("pk", flat=True).first()
        return value.hex if value else None

    async def billing_update(self, event):
        if await self.check_access():
            await self.send_json({"event": "billing.update"})
