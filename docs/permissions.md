# Access model

## Management accounts

One authenticated user may own multiple businesses. `Business.owner` is the canonical owner and is independent of collaborator memberships. New businesses set the owner at creation. The migration backfills owners from legacy Owner memberships; the field stays nullable to preserve older businesses that have no owner record.

Collaborators are optional. `Membership.permissions` stores explicit Management permissions. Invitations preserve hashed, expiring tokens and permission grants, but have no role field. `Membership.allows()` grants the canonical owner access and checks exact permission strings for collaborators. No Admin, Manager, or Employee hierarchy grants access.

`Department`, `Workspace`, and `WorkspaceAssignment` remain in the schema to preserve existing operational configuration. They are not an authorization source. `WorkspaceAssignment.permissions` does not grant station or Management access.

## Paired stations

Only the business owner can create stations, issue pairing codes, and revoke device sessions. Codes are random, hashed at rest, single-use, and expire after five minutes. Pairing returns a random bearer token once; only its SHA-256 digest is stored. Sessions persist until revoked or their station is disabled.

Each request resolves an active device and its current station. The server checks the station capability, business, and optional location before returning or changing data. Station capabilities currently cover catalog view, order view, order creation, and order-status updates. Station APIs do not use user roles or browser sessions.

| Operation | Station capability | Scope |
| --- | --- | --- |
| Read catalog | `catalog.view` | Active products in station business |
| Read orders | `orders.view` | Station business; station location when set |
| Create orders | `catalog.view`, `orders.create` | Server-priced products; station location |
| Update order status | `orders.view`, `orders.status.update` | Valid transitions; station location when set |
| Print queue & ack | `orders.view` | Station business; station-routed preparation tickets |
| Initiate QRIS payment | `orders.view` | Dynamic QR generation with 10-minute expiry |

Pairing attempts and token rotation are rate limited by source IP. Keep pairing codes and device tokens private. Devices can rotate bearer tokens via `POST /api/stations/<id>/rotate-token/`. Revoke lost devices in Management.

## Payment credentials

`PaymentConnection` is unique per business and provider. Only the canonical owner can save, read through service code, update, verify, or remove credentials. Credential bundles use Fernet authenticated encryption and require a stable `PAYMENT_CREDENTIALS_ENCRYPTION_KEY`. Credentials never appear in audit metadata or connection pages.

Provider adapters (Midtrans Core API and BCA SNAP QRIS MPM Dynamic) support dynamic QRIS generation, status polling, and webhook notification handling with cryptographic signature verification (Midtrans SHA-512, BCA SNAP HMAC-SHA512). Webhook events enforce replay protection via append-only request digests. Order release occurs upon provider-confirmed settlement (`after_paid`).

## Existing user permissions

| Capability | Required permission | Current behavior |
| --- | --- | --- |
| Open a business dashboard | Active membership or canonical ownership | Business-specific counts and shortcuts |
| View or manage catalog | `catalog.view`, `catalog.manage` | Catalog, categories, and station routing are tenant scoped |
| Create orders | `orders.create` and `catalog.view` | Server-priced tenant products; snapshots preserved |
| View order history | `orders.view` | Orders are tenant scoped |
| Change order status | `orders.manage` and `orders.view` | Server validates state transitions, writes audit events, triggers print routing |
| Record POS cash/card payment | `orders.manage` and `orders.view` | Cash tender/change validated server-side; manual card confirmation and tender audit; QRIS remains provider-settled |
| View or manage locations | `locations.view`, `locations.manage` | Tenant-scoped records |
| View or manage tables and QR codes | `tables.view`, `tables.manage` | Business-scoped table and location records; rotating or disabling a QR invalidates the previous link |
| View or manage customers | `customers.view`, `customers.manage` | Tenant-scoped records |
| View or manage inventory | `inventory.view`, `inventory.manage` | Tenant-scoped records; stock cannot go below zero |
| View sales reports | `reports.view` | Business-scoped order/payment aggregates with date, location and category filters |
| Manage collaborators | `employees.manage` | Owner or collaborator with this explicit permission; includes invitation creation and revocation |

Owners can restrict a collaborator's inventory and purchasing access to selected locations under Team access. A restricted membership with no locations grants access to no location data. Supplier contacts remain business-wide for users with inventory.view. Supplier purchase history follows the location restriction. Existing memberships remain unrestricted by default. Other Management domains still use business-wide access. Workspace-specific authorization and ownership transfer remain open. PostgreSQL/Redis tests and real-browser guest order/menu streams have been verified; other Management data domains still lack complete realtime synchronization.
