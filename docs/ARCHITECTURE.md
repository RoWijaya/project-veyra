# System architecture

## Identity modules — 2026-11-17

`core/identity.py` owns Authlib Google configuration, dedicated factor encryption, primary-login completion, atomic factor consumption and HTTP/ASGI MFA enforcement. `identity_views.py` exposes enrollment, protected QR, challenge and Google start/callback. GoogleIdentity binds unique provider subjects to users. Authenticator stores encrypted secret, confirmation state, last counter, hashed recovery codes and a version for session proofs. Migration 0019 is additive.

All primary-login routes, including admin, follow MFA policy. AuthMiddlewareStack precedes MFAWebsocketMiddleware; partial sessions cannot establish protected streams. Outbound data rechecks the active user, current database session and factor proof. Logout blocks further data on the next attempted delivery. Existing tenant permissions remain necessary. Debug ASGIStaticFilesHandler serves public local assets under Uvicorn; production retains WhiteNoise. Passkeys, assisted factor recovery and session/device inventory remain missing.

## 1. Architectural style

The inspected codebase is a **Django modular monolith**. The application is organized around one Django project (`config`) and one principal application (`core`). Business rules are concentrated in service functions in `core/services.py`; request handlers live in `core/views.py` and `core/device_views.py`; persistence is defined in `core/models.py`.

The current web interface is server-rendered with Django templates. Static interaction and presentation are implemented in `core/static/core/site.js` and `site.css`. The landing page uses GSAP/ScrollTrigger according to the existing landing workflow note.

## 2. Runtime components

| Component | Responsibility |
|---|---|
| Django HTTP application | Authentication, Management pages, POS pages, catalog, locations, customers, inventory, team, station and payment settings |
| PostgreSQL | Authoritative relational data store in normal development and production |
| Django Channels | Authorized business order, private guest tracking, menu promotion and inventory WebSocket consumers |
| Redis | Channels layer and production Django cache/rate-limit backend |
| Outbox worker | Polls `OutboxEvent` rows and dispatches pending events to the Channels layer |
| Uvicorn / ASGI | Runs the ASGI application and HTTP/WebSocket protocols |
| WhiteNoise | Serves collected static assets when `DEBUG` is false |

## 3. Request paths

### Management browser

1. The user authenticates using Django's session-based authentication.
2. The route resolves a business slug.
3. The view obtains an active membership for that user and business, or otherwise checks canonical business ownership as implemented by the membership helper.
4. The view checks the required permission.
5. The query is scoped to the resolved business.
6. Mutations are delegated to service functions where the operation has a corresponding service.
7. Important operations create audit records.

### Paired station

1. An owner creates a station and chooses its capability list, handling mode (`kds`, `printed_tickets`, `both`), optional location, and optional printer.
2. The owner issues a pairing code.
3. The station device submits the code to `POST /api/stations/pair/`.
4. The server consumes the code and returns a random bearer token once.
5. Subsequent station API requests use `Authorization: Bearer <device-token>`.
6. Devices can securely rotate bearer tokens via `POST /api/stations/<station-id>/rotate-token/`.
7. The server hashes the supplied token and resolves an active, non-revoked `PairedDevice` for the URL station ID.
8. Each endpoint checks the station's current capability list and scopes data to its business and, where applicable, location.

### Operational print and KDS dispatch

1. Order line items are routed to stations based on configured product assignments or station kind (kitchen vs bar).
2. When an order transitions to `preparing` (or upon payment confirmation in `after_paid` workflows), preparation ticket print jobs are generated idempotently for stations configured for print.
3. Preparation tickets remain distinct from customer receipts and display item quantities, table identifiers, fulfillment types, order timestamps, and instructions.
4. Stations poll or query pending print jobs via `GET /api/stations/<station-id>/print-jobs/` and acknowledge completion or failure via `POST .../ack/`.
5. Stations and Management support controlled ticket reprinting via `reprint_order_ticket`.
6. Stations can display live operational order queues via `/app/<slug>/stations/<station-id>/screen/`.

### Payment provider callbacks and release

1. Payment transactions are created per business through Midtrans or BCA SNAP QRIS Dynamic adapters with strict 10-minute expiry.
2. Provider webhook notifications are received at `POST /api/payments/webhooks/<provider>/`.
3. Signatures are verified using provider-specific algorithms (Midtrans SHA-512; BCA ASPI SNAP symmetric HMAC-SHA512).
4. Replay attacks are prevented using append-only SHA-256 request digests in `PaymentWebhookEvent`.
5. On confirmed payment, the order is released (`after_paid`), transitioning from `new` to `preparing` and dispatching operational preparation tickets.

## 4. Tenant boundaries

`Business` is the tenant root. Catalog, customers, locations, inventory, orders, stations, printers, print jobs, payment connections, payment transactions, and operational events are related to a business. Service and view code must derive the tenant from the authenticated membership or paired-device record, not trust a client-supplied business identifier.

The current Management collaborator permission model is business-wide. The schema contains `Department`, `Workspace`, and `WorkspaceAssignment`, but those records are not currently used as authorization grants.

## 5. ASGI and WebSockets

`config/asgi.py` composes Django's HTTP application with Channels routing. The WebSocket route is:

```text
ws/business/<slug:slug>/orders/
```

`BusinessOrdersConsumer` requires an authenticated user and checks active business membership with `orders.view` (or canonical ownership) before joining the business-specific group.

## 6. Architectural limitations

- The codebase is not split into independently deployed microservices.
- The current WebSocket consumer handles order updates; it is not a universal event bus for every business domain.
- Live Redis/WebSocket delivery must be verified against target staging/production Redis infrastructure.
- TOTP MFA is locally integrated/tested; passkeys and assisted recovery remain missing.
- Recipe-based stock deduction/returns and subscription plan/trial models are integrated. Supplier purchasing uses the shared inventory ledger. Live SaaS billing remains open.

## Current integrated paths (2026-11-17)

Shared pricing computes promotion-aware order snapshots for POS/QR guests. Preparation deducts recipe stock under row locks; cancellation returns consumed quantities. Cash/card tenders record staff-confirmed payment and change; QRIS uses provider-confirmed callbacks. Subscription plans, trials and grandfathered access gate selected operations; billing is not integrated. See REALTIME.md for all current stream routes.

RequestContextMiddleware returns correlation IDs and emits safe route-pattern JSON. `/readyz/` checks PostgreSQL/Redis separately from `/healthz/` liveness. Registration and resend share verification mail generation; verification consumes locked account state. SMTP failures are not durably retried. See SECURITY.md, INTEGRATIONS.md and OPERATIONS.md.

## Storefront, media and receipts — 2026-11-18

- The single Django application serves Menu and Your Order routes for signed table QR and location pickup links. PostgreSQL stores each cart by a keyed hash of the browser session, the table or pickup location and the link revision. A cart row lock and revision prevent concurrent overwrites. Checkout reuses the existing price engine, verifies the review quote, and associates the committed order with the cart for retry.
- Product images and campaign banners use database-backed WebP data served through permission-checked Django routes. Scheduled visibility uses the business time zone, campaign and linked promotion windows, and location targeting. The outbox worker records visibility transitions and publishes menu invalidations through Redis.
- POS and station views reconcile their own panels from authorized HTTP snapshots when order events arrive over Channels. The POS form stays in place. Order History and receipt preview enforce the order-view permission. Authorized receipt dispatch uses the existing station printer jobs, with browser print available from the same preview.

## Guest takeaway and storefront controls

- Location managers enable takeaway through Locations. The management POST stores a new pickup revision and an audit/outbox event together. Disabling clears the revision. Re-enabling creates a distinct link.
- Guest carts have exactly one scope. The table and location paths use separate signed identities and corresponding PostgreSQL uniqueness constraints. Switching a table cart to takeaway keeps the verified location and clears the table from the resulting order.
- Both guest sources run through the existing pricing, payment, inventory and order services. Business and location rows are checked inside the order transaction. Checkout verifies the current server quote and uses the cart key to pair retries with the same private tracking cookie.
- The customer menu stream accepts either signed scope. POS and station queues use the existing order event stream. Guest tracking reapplies the server snapshot after reconnecting and deduplicates order events by ID.

## Product customization

Catalog management saves modifier groups, options and assignments under the same business lock used by order creation. Both storefront and POS call the same location-scoped validation and pricing functions. Options add their surcharge after product promotions. The service records group names, option names and unit surcharges on each order line.

Existing plain guest cart entries retain their product ID and integer quantity. Configured entries include option IDs and their own line key. Different selections can coexist, and matching selections merge in the guest cart. Checkout revalidates the signed per-line quote inside the order transaction. Paid receipts and prep tickets read the snapshot stored with the order.

The transactional outbox sends signed-menu invalidations and authenticated POS catalog invalidations to their existing Redis groups. The storefront and POS refresh open prices and eligible definitions. POS preserves quantities and valid selections across catalog snapshots, and an affected configuration requires review when a selected option is removed. Create-only staff without order-stream access still receive current pricing and validation at submission.

## Voucher pricing and redemption

POS and guest checkout price each configured line through the same product, promotion and modifier functions. The server computes the voucher against eligible post-promotion product prices using Decimal. Percentage discounts round half up to the nearest cent. Fixed discounts and percentage amounts are bounded by eligible value and any merchant cap. Modifier surcharges are outside voucher eligibility.

Management writes take the PostgreSQL business lock. Checkout takes that same lock, resolves an existing idempotent order first, and locks the scoped voucher row before final eligibility and capacity checks. The order, lines, one-to-one redemption and audit event commit together. Order snapshots retain the voucher and redemption IDs, voucher name/code, type, configured value, eligible subtotal, discount, quoted subtotal and redemption time. Customer receipts and print commands expose the code and discount. Reprints reuse the first print snapshot.

Guest quotes bind the cart revision, priced lines and applied voucher calculation. Signed POS previews bind the code, tenant, customer, location, order type and priced lines for ten minutes. An edit publishes an outbox invalidation through the existing guest menu and authorized POS Channels groups. POS clears its stale approval, and guest checkout requires review when the server quote changes. Order creation verifies eligibility again even when a socket update is delayed.

Redemption commits with the order. Payment initiation, retries and callbacks use its saved net amount. Zero-total QRIS and fractional Midtrans voucher totals require counter payment. Capacity stays committed across failed or expired payments, cancellations and any future refunds. Order lines are not changed by voucher discounts, so product and category rollups continue to show value before vouchers. Order-level reports use actual net totals.

## Supplier purchasing and stock ledger

Purchases reference tenant-owned InventoryItem records. The sales Product model continues to describe sellable items and recipes. An active inventory item can be purchased into an authorized active destination. The same item may have different balances and latest purchase costs by location.

Purchase order lines retain ordered quantity, unit, item name, unit cost and a Decimal line subtotal. Ordered references and parties are snapshotted on the purchase. Receiving uses the normal stock movement service and creates a linked movement only for each accepted quantity. Receipt lines also retain rejected or damaged amounts, which stay outstanding. Receipt events, balances, accumulated quantities, stock movements, audit and order state commit together.

Each delivery has a unique key per purchase and a digest of its normalized quantities, notes and delivery reference. An exact retry returns the saved receipt. The PostgreSQL business and purchase locks serialize receiving and scope changes. Balance rows are locked by the existing ledger service. Invalid or overflowing deliveries roll back the entire session. The outbox publishes purchase invalidations and existing inventory balance events after commit.

The purchasing WebSocket sends invalidations. Browser views fetch the authorized purchase snapshot on each event and reconnect. Both purchasing and inventory consumers check the current member's location access for each event. The inventory balance stores a nullable last accepted unit cost, and receipt snapshots retain historical costs. Legacy stock keeps its original quantities and can have an unknown purchase cost.

## Platform billing lifecycle

BusinessSubscription remains authoritative for the current plan, newly opened trial terms and active paid terms. BillingInvoice snapshots the plan, amount, currency, entitlements and service period. BillingAttempt maps one active hosted session per invoice to a distinct platform payment reference. The billing service takes the same business row lock used by location, employee and station expansion. A database constraint limits each subscription to a single open invoice. Settlement, subscription entitlement update, the audit event and the outbox notification share one transaction.

The local scheduler is the process_billing management command. It advances trial and period boundaries independently of page visits and checks due provider attempts. It uses durable timestamps and locked state transitions for repeat or parallel runs. A checkout retry retains the same provider reference while its outcome is uncertain. Snap page expiry is explicitly set to 24 hours. A new attempt requires a verified terminal decline or the expiry of a session with no provider transaction. Pending provider transactions continue to be reconciled.

Billing invalidations carry no invoice or financial data. Only the active business owner can connect to that business stream. The browser fetches its current authorized snapshot after a notification or reconnect. Platform staff restrictions emit the same event.
