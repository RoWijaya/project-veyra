# Realtime order events

## Current implementation

The project uses Django Channels and a Redis channel layer when Channels is installed and the application is not in SQLite test mode. The ASGI application routes WebSocket connections to `BusinessOrdersConsumer`.

WebSocket URL:

```text
ws/business/<business-slug>/orders/
```

## Authorization

The consumer requires an authenticated Django user. It resolves the requested business slug and accepts the connection only when the user has an active membership that allows `orders.view`. The consumer joins a group named for the resolved business ID, not the raw slug.

On an order update event, the consumer sends JSON containing:

- `event_id`
- `event`
- `order_id`
- `status`

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

The outbox prevents the application from relying solely on an in-memory post-save callback: the event record is persisted and can be retried after transient delivery failures.

## Deployment requirements

- Run the ASGI app (for example, Uvicorn), not only a WSGI server, for WebSocket support.
- Configure `REDIS_URL` to a protected Redis service.
- Run at least one `process_outbox` worker.
- Monitor worker health, retry counts, event age, and `last_error`.
- Use TLS (`wss://`) for browser connections in production.

## Verification caveat

The archive contains unit tests for outbox dispatch behavior, but this does not establish successful end-to-end delivery through a live Redis service and browser WebSocket client. Verify this in a production-like environment before relying on realtime operations.

## Scope

The inspected consumer and outbox payload are order-oriented. Do not describe the current implementation as realtime synchronization for inventory, catalog, prices, employees, payment state, dashboards, or every workspace until those event producers and consumers exist and have been tested.
