# Real-time events

[Architecture](ARCHITECTURE.md) · [Socket endpoint index](FEATURES-AND-ROUTES.md#websocket-endpoints)

## Delivery path

1. A service changes the authoritative PostgreSQL state and creates the corresponding outbox event in its transaction.
2. ``process_outbox`` scans pending events, dispatches due events to Redis Channels, and retries failures. Its loop also checks promotion and campaign banner boundaries. It handles optional private media copies by event type.
3. Channels consumers send scoped events or snapshots. Browser code updates the live region or refreshes the authorized representation after the event. On reconnect, each supported client reloads the relevant current state.

Guest order notifications are change signals. Each connected guest is reauthorized and gets a current PostgreSQL snapshot that includes order, payment and confirmed refund state. The consumer rereads the snapshot after subscribing so the subscription boundary has no stale-state gap. Reordered notifications carry current state, and the browser drops repeated event IDs. Redis transports messages rather than storing business records. Durable outbox publication is not proof that every browser received an event. Clients may disconnect or see duplicate update hints. The normal client behavior is to reconnect and reconcile with current state.

## Authorization

Origin validation and the Django session stack wrap the Channels router. MFA blocks incomplete protected sessions. Business orders use order permissions and business scope. Inventory and purchasing enforce their own permission and location rules. Platform billing is owner-only. Guest tracking uses the signed cookie for that order and guest menu uses a live signed table or pickup token. Protected streams recheck relevant access before sending data. Paired devices currently use bearer-authenticated HTTP APIs, with no device-specific WebSocket route.

The browser KDS uses a user-session stream. Guest menus, guest order state, POS, inventory and purchasing, and billing have live paths. Staff and workspace administration, general notifications, and comprehensive dashboard updates remain incomplete. [Permission code](../core/permissions.py) · [Consumers](../core/consumers.py) · [ASGI routing](../config/asgi.py)

## Operate and verify

Run the ASGI process and an outbox worker alongside PostgreSQL and Redis. Test group fan-out and then check two independent, authorized browser clients. In a hosted environment, verify upgrade headers and WSS through the actual reverse proxy.

```powershell
.\.venv\Scripts\python.exe manage.py check_realtime
.\.venv\Scripts\python.exe manage.py process_outbox
```

The probe exercises Redis/Channels, and ``/readyz/`` checks dependency connectivity. Measure the oldest unpublished event and retries to observe worker progress. [Testing](TESTING.md) · [Operations](OPERATIONS.md)
