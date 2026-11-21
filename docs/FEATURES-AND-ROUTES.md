# Features and application routes

This document describes routes found in `core/urls.py` and views found in `core/views.py`. Authentication and permission checks are applied per view; consult the security document before exposing or extending a route.

## Public and account routes

| Method / route | Name | Purpose |
|---|---|---|
| `GET /` | `home` | Public landing page |
| `GET /media/landing-film/` | `landing_film` | Serves the landing film with byte-range support |
| `GET /healthz/` | `healthz` | Liveness response |
| `GET /readyz/` | `readiness` | Aggregate PostgreSQL/Redis dependency readiness (200/503) |
| `GET/POST /verification/resend/` | `resend_verification` | CSRF-protected throttled verification recovery with uniform confirmation |
| `/register/` | `register` | Account registration |
| `/login/` | `login` | Sign in |
| `/logout/` | `logout` | Sign out |
| `/verify/<uidb64>/<token>/` | `verify_email` | Email verification |
| `/password-reset/` and related routes | Django auth views | Password-reset request, confirmation and completion |

## Management routes

| Route | Purpose |
|---|---|
| `/app/` | User's business/workspace landing page |
| `/app/business/new/` | Create a business |
| `/app/<slug>/` | Business dashboard |
| `/admin/core/subscriptionplan/` | Platform-admin plan and entitlement configuration |
| `/admin/core/businesssubscription/` | Platform-admin trial/status/period administration; edits append subscription history |
| `/admin/core/subscriptionevent/` | Read-only subscription history |
| `/app/<slug>/catalog/` | View/manage products and catalog |
| `/app/<slug>/promotions/` | Configure server-evaluated scheduled prices |
| `/app/<slug>/promotions/<id>/edit/` | Edit a tenant-scoped promotion |
| `/app/<slug>/pos/` | Create POS orders and record cash/card tender |
| `/app/<slug>/orders/<uuid>/payment/` | Record idempotent cashier tender and cash change |
| `/app/<slug>/reports/` | Permission-gated sales reporting with date, location and category filters |
| `/app/<slug>/orders/<uuid>/status/` | Change order status |
| `/app/<slug>/locations/` | View/manage locations |
| `/app/<slug>/tables/` | Create tables, download/print QR codes, rotate or revoke QR access |
| `/order/table/<signed-token>/` | Guest menu and server-priced dine-in order submission |
| `/order/table/<signed-token>/menu.json` | Current server-priced guest menu snapshot for live refresh |
| `/order/<uuid>/status/` | Private guest order and payment tracking |
| `/order/<uuid>/payment.svg` | Private active QRIS payment image |
| `/app/<slug>/customers/` | View/manage customers |
| `/app/<slug>/inventory/` | View/manage inventory items and stock |
| `/app/<slug>/recipes/` | Configure per-product ingredient quantities |
| `/app/<slug>/team/` | Manage optional collaborators, invitations, and revocation |
| `/app/<slug>/stations/` | Configure stations, printers, issue pairing codes, manage paired devices |
| `/app/<slug>/stations/<uuid>/screen/` | Live operational KDS station screen |
| `/app/<slug>/payments/` | Owner-controlled payment credential settings, verification, and audit log |
| `POST /api/stations/<uuid>/rotate-token/` | Rotate station bearer token |
| `GET /api/stations/<uuid>/print-jobs/` | Station pending and recent print queue |
| `POST /api/stations/<uuid>/print-jobs/<uuid>/ack/` | Station reports printed or failed status |
| `POST /api/stations/<uuid>/orders/<uuid>/reprint/` | Controlled prep ticket reprint |
| `POST /api/stations/<uuid>/orders/<uuid>/pay/` | Station initiates QRIS dynamic payment |
| `POST /api/payments/webhooks/<provider>/` | Provider notification callback (Midtrans, BCA SNAP) |

## Current operational behavior

### Accounts and business setup

The custom user model uses unique email as the username field. Registration creates an unverified account and sends a verification link. Business creation sets the creator as canonical owner, creates an owner membership, creates a default `Main location`, and records a business-created audit event.

### Catalog and POS

Products are business-scoped and have decimal prices. Products can specify a routed operational station (kitchen or bar). POS order creation takes product identifiers and quantities, then resolves product prices on the server. It does not trust a client-supplied total. Orders support `dine_in` and `takeaway` order types; dine-in can include a table label. Order lines preserve product name, base/effective unit price and applied promotion snapshots.

Scheduled promotions support business-local date/time windows, weekdays, product/category/location targeting, discount types, priority and optional stacking. Guest checkout, POS and reporting use server-side order snapshots. The outbox worker publishes live menu changes when a promotion is saved or crosses a schedule boundary.

Dining tables belong to a business and one of its physical locations. Their signed QR links bind to a table and revocable version. Guests can browse active products and submit separate dine-in orders without an account; the order service supplies prices and associates each order with its table and location. Managers can download SVG codes or print the table sheet.

Order status transitions are validated in the service layer. Invalid transitions are rejected. Transitioning to `preparing` automatically evaluates product routing, deducts recipe ingredients under balance row locks, and queues preparation ticket print jobs for stations configured with handling mode `printed_tickets` or `both`. Cancellation returns consumed recipe stock. Cash tenders verify received amount and calculate change server-side; card tender recording assumes a cashier has already verified the external terminal result.

### Customers

Customers are business-scoped and store name, email, phone, and a note. Orders can associate a customer and preserve contact snapshots.

### Inventory

Inventory items are business-scoped. Balances are tracked per location. Stock changes are recorded as movements with an idempotency key; the current code protects movement history from ordinary updates/deletes and rejects negative stock balances. Recipe usage is deducted under row locks when an order enters preparation and returned when a preparing order is cancelled. The outbox publishes authorized location stock changes to live inventory workspaces.

### Team / collaborators

Collaborators are optional. Invitations grant an explicit subset of supported permission strings and expire after seven days. The invitation token is stored as a digest rather than plaintext. Active pending invitations can be revoked by the owner or managers holding `employees.manage`. There is no Admin/Manager/Employee role hierarchy.

### Stations and operational dispatch

Stations are owner-managed operational endpoints with explicit capabilities, handling modes (`kds`, `printed_tickets`, `both`), and assigned printers. Devices are paired using a one-time code and subsequently authenticate with a bearer token. Paired devices can securely rotate their bearer token. Operational screens at `/app/<slug>/stations/<station_id>/screen/` provide live digital KDS workflows with status advancement and controlled ticket reprinting.

### Payments

The Management page stores per-business credentials for supported provider labels (Midtrans, BCA SNAP QRIS) and environments. Credentials are Fernet-encrypted at rest. Owners can test and verify connection format. Payment creation generates dynamic QRIS codes with 10-minute expiry. Secure webhook handling verifies provider signatures, enforces replay protection via request digests, and transitions orders to preparation upon provider-confirmed settlement (`after_paid`).

### SaaS subscriptions

New businesses receive a 14-day Starter Trial. Existing tenants are migrated to grandfathered access without changing their data or implying paid status. Subscription feature flags gate order creation, inventory mutations, reports and promotion/catalog management; configured product/location limits are enforced. Subscription history is append-only. Platform staff configure plans and tenant state in Django admin. Checkout, charging, invoices, renewal automation and dunning are not implemented.

## UI templates

The current template set includes authentication, landing/home, dashboard, catalog, promotions, sales reports, POS, customers, inventory, locations, team, stations, payments, invitation acceptance, verification, guest ordering/tracking, and status pages under `templates/core/`.

## Identity routes

| Route | Purpose |
|---|---|
| GET/POST /account/security/ | Authenticator enrollment, confirmation, one-time recovery display and reauthenticated removal; Google linking entry |
| GET /account/mfa/qr.svg | Private uncached QR during pending setup |
| GET/POST /account/mfa/ | Primary-authenticated TOTP/recovery challenge |
| POST /auth/google/start/ | CSRF-protected Google login/link start |
| GET /auth/google/callback/ | Authlib-validated provider callback |
| /admin/login/ | Shared primary-login and MFA policy |
