# Realtime order events

## Current implementation

The project uses Django Channels and a Redis channel layer when Channels is installed and the application is not in SQLite test mode. The ASGI application routes business order streams, private guest order tracking, and signed table QR or location pickup menu streams.

WebSocket URL:

```text
ws/business/<business-slug>/orders/
```

Additional streams are `ws/guest/orders/<order-uuid>/` for cookie-authorized guest tracking, `ws/guest/menu/<signed-menu-token>/` for guest menu pricing, and `ws/business/<business-slug>/inventory/` for permission-authorized stock balances.

## Authorization

The consumer requires an authenticated Django user. It resolves the requested business slug and accepts the connection when the user is the canonical business owner or has an active membership that allows `orders.view`. The consumer joins a group named for the resolved business ID, not the raw slug.

On an order update event, the consumer sends JSON containing:

- `event_id`
- `event`
- `order_id`
- `status`
- `payment_status`
- `payment_method`

## Transactional outbox

Order service operations create `OutboxEvent` records as durable pending events. The management command:

```bash
python manage.py process_outbox
```

polls due unpublished events in batches of up to 100. It supports:

```bash
python manage.py process_outbox --once
python manage.py process_outbox --interval 2
```

`--once` processes one batch and exits. The default worker polls continuously with a two-second interval.

The worker also evaluates active promotion schedules on each pass. A schedule state transition creates a durable `menu.update` outbox event so connected guest menus fetch the current server-priced menu. Saving a promotion also queues this event directly. Scheduled live delivery therefore depends on this worker remaining supervised.

The outbox prevents the application from relying solely on an in-memory post-save callback: the event record is persisted and can be retried after transient delivery failures.

## Deployment requirements

- Run the ASGI app (for example, Uvicorn), not only a WSGI server, for WebSocket support.
- Configure `REDIS_URL` to a protected Redis service.
- Run at least one `process_outbox` worker.
- Monitor worker health, retry counts, event age, and `last_error`.
- Use TLS (`wss://`) for browser connections in production.

## Verification

PostgreSQL/Redis integration tests cover private guest snapshot authorization, order state delivery, promotion updates and scheduled promotion boundary delivery. A real browser verified guest order state changes and repricing of an already-open QR menu after a promotion save. Provider callbacks and other operational domains still require dedicated integration verification.

## Scope

Order and payment tracking, guest menu pricing and inventory balance updates are live. Catalog administration, employee/workspace changes, dashboards, station performance and notifications do not yet have complete realtime synchronization.

## Modifier changes

A successful editor save enqueues a menu event in the same transaction as the group and option changes. The outbox invalidates both the signed guest menu and the authenticated business order stream. The latter sends a `catalog.changed` notification, and authorized POS screens request a new catalog snapshot for the selected location. Draft quantities and eligible choices survive the refresh. A draft containing an unavailable choice requires review before submission. Guest checkout revalidates signed quotes against the database.
