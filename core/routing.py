from django.urls import path

from .consumers import (
    BusinessBillingConsumer,
    BusinessInventoryConsumer,
    BusinessOrdersConsumer,
    BusinessPurchasingConsumer,
    GuestMenuConsumer,
    GuestOrderTrackingConsumer,
)

websocket_urlpatterns = [
    path("ws/business/<slug:slug>/billing/", BusinessBillingConsumer.as_asgi()),
    path("ws/business/<slug:slug>/orders/", BusinessOrdersConsumer.as_asgi()),
    path("ws/guest/orders/<uuid:order_id>/", GuestOrderTrackingConsumer.as_asgi()),
    path("ws/guest/menu/<str:token>/", GuestMenuConsumer.as_asgi()),
    path("ws/business/<slug:slug>/inventory/", BusinessInventoryConsumer.as_asgi()),
    path("ws/business/<slug:slug>/purchasing/", BusinessPurchasingConsumer.as_asgi()),
]
