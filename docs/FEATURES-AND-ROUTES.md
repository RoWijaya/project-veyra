# Browser, device and provider routes

This index documents existing entry points. [HTTP URL configuration](../core/urls.py) · [Root URLs](../config/urls.py) · [Channels routing](../core/routing.py) · [Authorization](permissions.md)

Placeholders show Django converters. Routes can accept form actions at the same URL. The view checks method, tenancy, entitlement, and permission. Signed URLs, bearer credentials, and tracking cookies are private access values.

## Public and account browser routes

| Route | Access and purpose |
| --- | --- |
| ``/``, ``/media/landing-film/`` | Public landing and range-enabled film. |
| ``/healthz/``, ``/readyz/`` | Public liveness and PostgreSQL/Redis readiness. |
| ``/register/``, ``/login/``, ``/verification/resend/`` | Public forms with CSRF on writes. |
| ``/verify/<str:uidb64>/<str:token>/`` | Signed email verification. |
| ``/password-reset/``, ``/password-reset/done/``, ``/password-reset/<uidb64>/<token>/``, ``/password-reset/complete/`` | Django password recovery. |
| ``POST /auth/google/start/``, ``GET /auth/google/callback/`` | Configured OIDC start and callback. |
| ``/account/security/``, ``/account/mfa/``, ``/account/mfa/qr.svg``, ``/logout/`` | Account management, challenge and session termination. The setup QR is limited to the authorized enrollment session. |
| ``/invitations/<slug:slug>/<str:token>/accept/`` | Expiring invitation entry. Acceptance requires the intended verified identity. |
| ``/admin/login/``, ``/admin/`` | Shared sign-in and Django staff administration with applicable MFA. |

## Management browser routes

All suffixes in this table are relative to ``/app/<slug:slug>/``. ``/app/`` lists memberships and ``/app/business/new/`` opens business creation.

| Suffix | Workflow |
| --- | --- |
| Empty suffix, ``settings/``, ``locations/``, ``team/`` | Overview, business settings, branches, and collaborators. Inventory grants use ``team/<int:membership_id>/inventory-access/``. |
| ``catalog/``, ``catalog/<uuid:product_id>/edit/``, ``catalog/categories/``, ``catalog/modifiers/`` | Products, categories and reusable option groups. Detail forms include ``catalog/categories/<int:category_id>/`` and ``catalog/modifiers/<int:group_id>/``. Product images have ``catalog/<uuid:product_id>/image/`` and ``catalog/<uuid:product_id>/image/edit/``. |
| ``promotions/``, ``promotions/vouchers/``, ``banners/`` | Scheduled pricing, codes and campaign banners. Edits use ``promotions/<int:promotion_id>/edit/``, ``promotions/vouchers/<int:voucher_id>/``, and ``banners/<int:banner_id>/edit/``. Management banner media uses ``banners/<int:banner_id>/image/``. |
| ``pos/``, ``orders/``, ``orders/<uuid:order_id>/status/``, ``orders/<uuid:order_id>/payment/`` | POS, history, and order changes. Status and tender changes use POST. |
| ``orders/<uuid:order_id>/receipt/``, ``orders/<uuid:order_id>/refunds/`` | Scoped receipts and refund history/actions. |
| ``tables/``, ``customers/``, ``stations/``, ``stations/<uuid:station_id>/screen/`` | Table links, customer records, device/print configuration, and browser KDS. |
| ``inventory/``, ``recipes/``, ``inventory/balances/<int:balance_id>/count/`` | Stock, recipes and physical counts. |
| ``inventory/suppliers/``, ``inventory/purchases/``, ``inventory/purchases/new/`` | Supplier and purchase lists and creation. Supplier detail and add use ``inventory/suppliers/<int:supplier_id>/`` and ``inventory/suppliers/new/``. |
| ``inventory/purchases/<uuid:purchase_id>/``, ``inventory/purchases/<uuid:purchase_id>/edit/``, ``inventory/purchases/<uuid:purchase_id>/receive/``, ``inventory/purchases/<uuid:purchase_id>/lines/<int:line_id>/return/`` | Purchase detail, edit, receiving and supplier return. |
| ``settings/ordering-page/``, ``settings/ordering-page/preview/``, ``settings/ordering-page/preview/products/<uuid:product_id>/`` | Builder, private render, and private preview product image. Private design media is served at ``settings/ordering-page/banner/`` and ``settings/ordering-page/logo/``. |
| ``payments/``, ``settings/billing/``, ``settings/billing/invoices/<uuid:invoice_id>/``, ``reports/`` | Tenant merchant settings, separate owner billing, and scoped sales reports. |

## Guest ordering

| Route | Access |
| --- | --- |
| ``/order/table/<str:token>/``, ``/order/table/<str:token>/qr.svg``, ``/order/table/<str:token>/menu.json`` | Revocable signed table menu, QR and menu data. |
| ``/order/table/<str:token>/cart/`` | Browser cart for a valid table token and session. |
| ``/order/pickup/<str:token>/``, ``/order/pickup/<str:token>/cart/`` | Revocable pickup location entry and session cart. |
| ``/order/table/<str:token>/products/<uuid:product_id>/image/``, ``/order/table/<str:token>/banners/<int:banner_id>/image/`` | Scoped product and campaign images. |
| ``/order/table/<str:token>/ordering-banner/``, ``/order/table/<str:token>/ordering-logo/`` | Active published branding. The existing guest media paths serve eligible table and pickup tokens. |
| ``/order/<uuid:order_id>/status/``, ``/order/<uuid:order_id>/payment.svg`` | Private tracking and eligible QRIS image. Both require that order's cookie. |

## Device and provider HTTP

The station pairing API starts at ``POST /api/stations/pair/``. Further endpoints below ``/api/stations/<uuid:station_id>/`` require the station bearer token and current capability. See the [device API contract](STATIONS-AND-DEVICE-API.md) for payloads. Management pages do not require or expose a device bearer token.

- ``POST /api/payments/webhooks/<str:provider>/`` receives tenant provider evidence.
- ``POST /api/billing/notifications/midtrans/`` receives platform subscription notification evidence.

[Payment lifecycle and verification](PAYMENTS.md)

## WebSocket endpoints

| Route | Required scope |
| --- | --- |
| ``/ws/business/<slug:slug>/orders/`` | Owner or active member with order read access. |
| ``/ws/business/<slug:slug>/inventory/`` | Owner or current inventory read access with applicable location scope. |
| ``/ws/business/<slug:slug>/purchasing/`` | Inventory and purchasing scope, with optional location routing validated in the consumer. |
| ``/ws/business/<slug:slug>/billing/`` | Canonical owner. |
| ``/ws/guest/menu/<str:token>/`` | Current signed table or pickup scope. |
| ``/ws/guest/orders/<uuid:order_id>/`` | Valid private tracking cookie for the identified order. |

[Event delivery and reconnection](REALTIME.md)
