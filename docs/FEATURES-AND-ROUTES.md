# Features and application routes

This document describes routes found in `core/urls.py` and views found in `core/views.py`. Authentication and permission checks are applied per view; consult the security document before exposing or extending a route.

## Public and account routes

| Method / route | Name | Purpose |
|---|---|---|
| `GET /` | `home` | Public landing page |
| `GET /media/landing-film/` | `landing_film` | Serves the landing film with byte-range support |
| `GET /healthz/` | `healthz` | Liveness response |
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
| `/app/<slug>/catalog/` | View/manage products and catalog |
| `/app/<slug>/pos/` | Create POS orders |
| `/app/<slug>/orders/<uuid>/status/` | Change order status |
| `/app/<slug>/locations/` | View/manage locations |
| `/app/<slug>/customers/` | View/manage customers |
| `/app/<slug>/inventory/` | View/manage inventory items and stock |
| `/app/<slug>/team/` | Manage optional collaborators and invitations |
| `/app/<slug>/stations/` | Configure stations, issue pairing codes, manage paired devices |
| `/app/<slug>/payments/` | Owner-controlled payment credential settings |

## Current operational behavior

### Accounts and business setup

The custom user model uses unique email as the username field. Registration creates an unverified account and sends a verification link. Business creation sets the creator as canonical owner, creates an owner membership, creates a default `Main location`, and records a business-created audit event.

### Catalog and POS

Products are business-scoped and have decimal prices. POS order creation takes product identifiers and quantities, then resolves product prices on the server. It does not trust a client-supplied total. Orders support `dine_in` and `takeaway` order types; dine-in can include a table label. Order lines preserve product name and unit price snapshots.

Order status transitions are validated in the service layer. Invalid transitions are rejected. The exact allowed status values and transition graph should be read from `Order.Status` and `transition_order` in the source when changing the workflow.

### Customers

Customers are business-scoped and store name, email, phone, and a note. Orders can associate a customer and preserve contact snapshots.

### Inventory

Inventory items are business-scoped. Balances are tracked per location. Stock changes are recorded as movements with an idempotency key; the current code protects movement history from ordinary updates/deletes and rejects negative stock balances.

### Team / collaborators

Collaborators are optional. Invitations grant an explicit subset of supported permission strings and expire after seven days. The invitation token is stored as a digest rather than plaintext. There is no Admin/Manager/Employee role hierarchy in the current implementation.

### Stations

Stations are owner-managed operational endpoints with explicit capabilities. Devices are paired using a one-time code and subsequently authenticate with a bearer token. See [Stations and device API](STATIONS-AND-DEVICE-API.md).

### Payments

The Management page stores per-business credentials for supported provider labels and environments. Credentials are Fernet-encrypted at rest. The UI and model do not prove that a provider account is connected or that credentials are valid. No provider payment creation, QR generation, notification/webhook verification, settlement reconciliation, or refund workflow was found.

## UI templates

The current template set includes authentication, landing/home, dashboard, catalog, POS, customers, inventory, locations, team, stations, payments, invitation acceptance, verification, and status pages under `templates/core/`.
