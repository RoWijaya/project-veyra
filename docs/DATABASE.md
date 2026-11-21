# Domain model and database

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
| `Station` | Paired operational endpoint; business, optional location, kind, capability list, active flag |
| `StationPairingCode` | Hashed, expiring, single-use station pairing code |
| `PairedDevice` | Paired station device; hashed bearer-token digest, label, last-seen and revocation timestamps |
| `PaymentConnection` | Per-business provider/environment/merchant record with encrypted credential ciphertext and optional `verified_at` |
| `Category` | Business catalog category |
| `Product` | Business catalog item with optional category, SKU, decimal price, and active flag |
| `Customer` | Business customer contact record |
| `InventoryItem` | Business stock item with SKU, unit, active flag |
| `InventoryBalance` | Quantity of an inventory item at a location |
| `StockMovement` | Append-only stock movement with quantity delta, reason, idempotency key and actor/device context |
| `RecipeLine` | Product-to-inventory-item quantity relation; schema exists, but recipe-based stock consumption is not implemented |
| `Order` | Business order with optional location/customer, snapshots, idempotency fields, order type, status and total |
| `OrderLine` | Product name and unit-price snapshot plus quantity |
| `OutboxEvent` | Durable pending event payload, retry timing, published timestamp, attempt count and last error |
| `AuditEvent` | Append-only business audit record with actor, action, object identifiers and metadata |

## Relationship overview

- A `User` can own multiple `Business` records.
- A `User` can have memberships in multiple businesses.
- A `Business` has locations, catalog records, customers, inventory, orders, stations, payment connections, and audit/outbox events.
- A `Station` may be scoped to one `Location` and has pairing codes and paired devices.
- An `Order` has order lines and may reference a customer, location, user, and paired device.
- An `InventoryItem` has location-specific balances and stock movements.
- A `Product` can have recipe lines, but automatic recipe consumption is not part of the current implemented workflow.

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
