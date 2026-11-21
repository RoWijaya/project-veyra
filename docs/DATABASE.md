# Domain model and database

## Identity migration 0019

`GoogleIdentity` is one-to-one with User and stores a unique 255-character provider subject and email snapshot. `Authenticator` is one-to-one with User and stores encrypted secret, confirmed state, last accepted counter, recovery-code digests and UUID session-proof version. PostgreSQL row locks serialize TOTP/recovery consumption. Plaintext secrets/codes are never audit metadata. Audit events record Google linking/sign-in and MFA enable/disable/verification/recovery use. Migration 0019 is additive and applied locally; existing tenant, order and subscription data is preserved.

## Database engine

Normal runtime configuration requires PostgreSQL through `DATABASE_URL`. SQLite is available only when `DJANGO_SQLITE_TEST=1`, primarily for isolated tests. Migrations are in `core/migrations/`.

Most tenant-owned records reference `Business` directly or through a parent record. UUID primary keys are used for several externally addressed entities, including `Business`, `Product`, `Order`, `Station`, and `OutboxEvent`.

## Main entities

| Model | Purpose / important fields |
|---|---|
| `User` | Custom Django user; unique email is the login identifier; includes `email_verified_at` |
| `Business` | Tenant root; `owner`, `name`, unique `slug`, `business_type`, `created_at` |
| `Membership` | User-to-business collaborator membership; `permissions` JSON list and `active` flag |
| `Invitation` | Email invitation with permission list, SHA-256 token digest, expiry, and acceptance timestamp |
| `Location` | Physical business location; name, address, active flag |
| `Department` | Business-level operational classification |
| `Workspace` | Optional business workspace, optionally linked to a location and department |
| `WorkspaceAssignment` | Links a membership to a workspace and stores a permission list; not currently an authorization source |
| `Printer` | Operational ESC/POS or virtual network printer with connection parameters, paper width, and online status |
| `Station` | Paired operational endpoint; business, optional location, kind, handling mode (`kds`, `printed_tickets`, `both`), optional assigned printer, capability list, active flag |
| `StationPairingCode` | Hashed, expiring, single-use station pairing code |
| `PairedDevice` | Paired station device; hashed bearer-token digest, label, last-seen and revocation timestamps |
| `PrintJob` | Durable print queue entity for prep tickets and receipts; tracks status, attempts, errors, formatted payload, idempotency key |
| `PaymentConnection` | Per-business provider/environment/merchant record with encrypted credential ciphertext and `verified_at` |
| `PaymentTransaction` | Per-order payment lifecycle entity; provider, environment, transaction ID, merchant reference, QRIS payload, 10-minute expiry, status, paid timestamp |
| `PaymentWebhookEvent` | Append-only raw webhook audit log; provider, request digest, headers, payload, signature validation, processed flag |
| `Category` | Business catalog category |
| `Product` | Business catalog item with optional category, routed station, SKU, decimal price, and active flag |
| `Customer` | Business customer contact record |
| `InventoryItem` | Business stock item with SKU, unit, active flag |
| `InventoryBalance` | Quantity of an inventory item at a location |
| `StockMovement` | Append-only stock movement with quantity delta, reason, idempotency key and actor/device context |
| `RecipeLine` | Product-to-inventory-item quantity relation; ingredient quantity used for locked stock consumption on preparation and returns on cancellation |
| `Order` | Business order with optional location/customer, snapshots, idempotency fields, order type, status and total |
| `OrderLine` | Product name and unit-price snapshot plus quantity |
| `OutboxEvent` | Durable pending event payload, retry timing, published timestamp, attempt count and last error |
| `AuditEvent` | Append-only business audit record with actor, action, object identifiers and metadata |

## Relationship overview

- A `User` can own multiple `Business` records.
- A `User` can have memberships in multiple businesses.
- A `Business` has locations, catalog records, customers, inventory, orders, stations, printers, print jobs, payment connections, payment transactions, and audit/outbox events.
- A `Station` may be scoped to one `Location` and has pairing codes, paired devices, and assigned printers.
- A `Product` can specify a routed `Station` for kitchen/bar dispatch.
- An `Order` has order lines and may reference a customer, location, user, and paired device.
- An `Order` generates routed preparation `PrintJob` records and tracks `PaymentTransaction` records.
- An `InventoryItem` has location-specific balances and stock movements.
- A `Product` has recipe lines used when an order enters preparation; linked sale/return StockMovement records prevent duplicate recipe consumption and preserve cancellation history.

## Important integrity and behavior notes

- Order line names and unit prices are snapshots. Later product edits do not rewrite historical order line details.
- Customer name/email/phone snapshots are stored on an order when a customer is selected.
- Order creation supports an idempotency key and request digest to prevent accidental duplicate submissions and detect key reuse with a different payload.
- Stock movements and audit events use append-only queryset/model protections in the current code.
- Inventory balance is location-specific. Stock adjustment rejects invalid cross-tenant item/location combinations and negative resulting stock.
- Tenant scoping must be maintained in every new query and service.
- `Business.owner` is canonical ownership. Owner membership rows remain for compatibility with existing membership-based flows.
- `WorkspaceAssignment.permissions` must not be treated as a grant of station or Management access.

## Migrations

The current migration sequence includes initial domain schema, order metadata/idempotency, customers and inventory, outbox events, customer snapshots, invitations, and owner/station/payment-connection additions. Run `python manage.py migrate` to apply migrations. Do not edit already-applied migrations in shared environments; create new migrations for schema changes.

## Additive migrations 0010–0018

0010–0012 add revocable dining tables, order source and private guest tracking/payment selection. 0013 adds scheduled promotions, line promotion snapshots and historic base-price backfill. 0014 persists schedule state for restart-safe boundary detection. 0015 links stock movements to orders and constrains order/item/reason uniqueness. 0016–0017 add cashier tender records and cash/card consistency constraints. 0018 adds SubscriptionPlan, BusinessSubscription and append-only SubscriptionEvent; its data migration explicitly preserves existing businesses with grandfathered access. New business service creates an audited 14-day trial.

Promotion targets include products, categories and locations; OrderLinePromotion preserves applied amounts. OrderTender is one-to-one with Order and records staff method/amount, cash received/change and retry identity. BusinessSubscription is one-to-one with Business and protects its Plan; feature/limit JSON gates selected workflows. Audit and subscription history use application-level append-only protections. No schema changes were needed for the 2026-11-17 verification-resend/readiness/logging changes.
