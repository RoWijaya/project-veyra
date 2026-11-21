from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .permissions import (
    guest_menu_business_id,
    inventory_stream_business_id,
    order_for_tracking_cookie,
    order_stream_business_id,
    order_tracking_cookie_name,
    order_tracking_group_name,
)


class BusinessOrdersConsumer(AsyncJsonWebsocketConsumer):
    async def menu_update(self, event):
        await self.send_json({"event_id": event["event_id"], "event": "catalog.changed"})

    async def connect(self):
        user = self.scope["user"]
        if not user.is_authenticated:
            await self.close(code=4403)
            return
        slug = self.scope["url_route"]["kwargs"]["slug"]
        access = await self._membership_access(user.pk, slug)
        if not access:
            await self.close(code=4403)
            return
        self.group_name = f"business_{access}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    @database_sync_to_async
    def _membership_access(self, user_id, slug):
        return order_stream_business_id(user_id, slug)

    async def order_update(self, event):
        await self.send_json(
            {
                "event_id": event["event_id"],
                "event": event["event"],
                "order_id": event["order_id"],
                "status": event["status"],
                "payment_status": event.get("payment_status", "unpaid"),
                "payment_method": event.get("payment_method", "counter"),
            }
        )

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)


class GuestOrderTrackingConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        order_id = self.scope["url_route"]["kwargs"]["order_id"]
        cookie_name = order_tracking_cookie_name(order_id)
        cookie = self.scope.get("cookies", {}).get(cookie_name)
        order = await self._authorized_order(order_id, cookie)
        if order is None:
            await self.close(code=4403)
            return
        self.order_id = order["order_id"]
        self.group_name = order_tracking_group_name(self.order_id)
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self.send_json(order)

    @database_sync_to_async
    def _authorized_order(self, order_id, cookie):
        order = order_for_tracking_cookie(order_id, cookie)
        if order is None:
            return None
        return {
            "event_id": "snapshot",
            "event": "snapshot",
            "order_id": str(order.pk),
            "status": order.status,
            "payment_status": order.payment_status,
            "payment_method": order.payment_method,
        }

    async def order_update(self, event):
        if event.get("order_id") == self.order_id:
            await self.send_json({
                "event_id": event["event_id"],
                "event": event["event"],
                "order_id": event["order_id"],
                "status": event["status"],
                "payment_status": event.get("payment_status", "unpaid"),
                "payment_method": event.get("payment_method", "counter"),
            })

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)


class GuestMenuConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        token = self.scope["url_route"]["kwargs"]["token"]
        business_id = await self._business_id(token)
        if not business_id:
            await self.close(code=4403)
            return
        self.group_name = f"guest_menu_{business_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    @database_sync_to_async
    def _business_id(self, token):
        return guest_menu_business_id(token)

    async def menu_update(self, event):
        await self.send_json({"event_id": event["event_id"], "event": "menu.update"})

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)


class BusinessInventoryConsumer(AsyncJsonWebsocketConsumer):
    group_prefix = "inventory"

    async def connect(self):
        user = self.scope["user"]
        if not user.is_authenticated:
            await self.close(code=4403)
            return
        slug = self.scope["url_route"]["kwargs"]["slug"]
        business_id = await self._membership_access(user.pk, slug)
        if not business_id:
            await self.close(code=4403)
            return
        self.group_name = f"{self.group_prefix}_{business_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    @database_sync_to_async
    def _membership_access(self, user_id, slug, location_id=None):
        return inventory_stream_business_id(user_id, slug, location_id)

    async def may_read_location(self, event):
        return await self._membership_access(
            self.scope["user"].pk, self.scope["url_route"]["kwargs"]["slug"], event["location_id"],
        )

    async def inventory_update(self, event):
        if not await self.may_read_location(event):
            return
        await self.send_json({
            "event_id": event["event_id"],
            "event": "inventory.update",
            "item_id": event["item_id"],
            "location_id": event["location_id"],
            "quantity": event["quantity"],
            "last_purchase_unit_cost": event.get("last_purchase_unit_cost"),
        })

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

class BusinessPurchasingConsumer(BusinessInventoryConsumer):
    group_prefix = "purchasing"

    async def purchase_update(self, event):
        if not await self.may_read_location(event):
            return
        await self.send_json({
            "event": "purchase.update",
            "event_id": event["event_id"],
            "purchase_id": event.get("purchase_id"),
            "location_id": event["location_id"],
        })

class BusinessBillingConsumer(AsyncJsonWebsocketConsumer):
    @database_sync_to_async
    def business_id(self):
        from .models import Business

        user = self.scope["user"]
        if not user.is_authenticated:
            return None
        value = Business.objects.filter(
            slug=self.scope["url_route"]["kwargs"]["slug"], owner_id=user.pk, owner__is_active=True,
        ).values_list("pk", flat=True).first()
        return value.hex if value else None

    async def connect(self):
        business_id = await self.business_id()
        if not business_id:
            await self.close(code=4403)
            return
        self.group_name = f"billing_{business_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def billing_update(self, event):
        business_id = await self.business_id()
        if not business_id or self.group_name != f"billing_{business_id}":
            await self.close(code=4403)
            return
        await self.send_json({"event": "billing.update"})

    async def disconnect(self, code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
