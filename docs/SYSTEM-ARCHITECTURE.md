# System architecture

## 1. Architectural style

The inspected codebase is a **Django modular monolith**. The application is organized around one Django project (`config`) and one principal application (`core`). Business rules are concentrated in service functions in `core/services.py`; request handlers live in `core/views.py` and `core/device_views.py`; persistence is defined in `core/models.py`.

The current web interface is server-rendered with Django templates. Static interaction and presentation are implemented in `core/static/core/site.js` and `site.css`. The landing page uses GSAP/ScrollTrigger according to the existing landing workflow note.

## 2. Runtime components

| Component | Responsibility |
|---|---|
| Django HTTP application | Authentication, Management pages, POS pages, catalog, locations, customers, inventory, team, station and payment settings |
| PostgreSQL | Authoritative relational data store in normal development and production |
| Django Channels | WebSocket consumer for authorized business order updates |
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

1. An owner creates a station and chooses its capability list and optional location.
2. The owner issues a pairing code.
3. The station device submits the code to `POST /api/stations/pair/`.
4. The server consumes the code and returns a random bearer token once.
5. Subsequent station API requests use `Authorization: Bearer <device-token>`.
6. The server hashes the supplied token and resolves an active, non-revoked `PairedDevice` for the URL station ID.
7. Each endpoint checks the station's current capability list and scopes data to its business and, where applicable, location.

### Order event delivery

1. An order operation writes the business data and an `OutboxEvent` in the database transaction.
2. The worker polls unpublished events whose `next_attempt_at` is due.
3. `dispatch_outbox_event` publishes to the configured Channels layer.
4. The WebSocket consumer sends an order update to authorized connected users.
5. Successful dispatch marks the event as published; failed attempts are scheduled for retry by the service logic.

## 4. Tenant boundaries

`Business` is the tenant root. Catalog, customers, locations, inventory, orders, stations, payment connections, and operational events are related to a business. Service and view code must derive the tenant from the authenticated membership or paired-device record, not trust a client-supplied business identifier.

The current Management collaborator permission model is business-wide. The schema contains `Department`, `Workspace`, and `WorkspaceAssignment`, but those records are not currently used as authorization grants.

## 5. ASGI and WebSockets

`config/asgi.py` composes Django's HTTP application with Channels routing. The WebSocket route is:

```text
ws/business/<slug:slug>/orders/
```

`BusinessOrdersConsumer` requires an authenticated user and checks active business membership with `orders.view` before joining the business-specific group.

## 6. Architectural limitations

- The codebase is not split into independently deployed services.
- The current WebSocket consumer handles order updates; it is not a universal event bus for every business domain.
- Live Redis/WebSocket delivery must be verified in the target deployment environment.
- There is no payment-provider callback pipeline in the inspected code.
- No printer/KDS job subsystem was found in the inspected implementation.
