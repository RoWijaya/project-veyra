# Operations

[Deployment](DEPLOYMENT.md) covers release topology, configuration and backup/restore. [Development](DEVELOPMENT.md) covers local startup.

## Workers and health

| Command | Responsibility | Default interval |
| --- | --- | --- |
| ``python manage.py process_outbox`` | Publish due operational events to Channels, check pricing and banner schedule boundaries, and deliver private media archive work. | Two seconds when idle. |
| ``python manage.py process_account_mail`` | Deliver queued account verification, retry temporary failures and retain terminal delivery state. | Two seconds while idle, with bounded backoff per delivery. |
| ``python manage.py process_billing`` | Advance subscription boundaries, manage renewals and reconcile pending platform attempts. | 60 seconds. |
| ``python manage.py process_refunds`` | Check pending Midtrans refunds using provider status. It does not submit new refunds. | 60 seconds, with per-refund retry timing. |
| ``python manage.py check_realtime`` | Send and receive an actual Channels group message. Run against the target Redis connection. | On demand. |

All four workers accept ``--once`` for a single cycle and ``--interval`` for polling frequency. Long-running workers record progress with a 180-second Redis heartbeat and stop between bounded jobs on shutdown. The staging supervisor restarts a crashed process. Use `python manage.py check_worker <name>` and `python manage.py check_operational_queues` for heartbeats, delayed work and terminal mail/print failures. Alert on failures and review pending event ages before resuming service. Outbox retries keep authoritative events in PostgreSQL. Printing uses external paired devices, with app-level queue and acknowledgement status.

``GET /healthz/`` reports liveness. ``GET /readyz/`` runs a PostgreSQL query and Redis PING. It returns 503 with a generic unavailable status if either dependency fails. It does not verify workers, migration level, email, or payment-provider connectivity.

## Logging and triage

- Use the response's ``X-Request-ID``, timestamp and route pattern to locate ``request.completed``. Allowlisted app events carry method, route pattern, duration and status without raw paths, request bodies, tokens, or arbitrary exception text. Keep reverse proxy access logs and debug console mail private.
- Check dependency readiness, then worker supervision. Check unpublished outbox count, oldest creation time, attempts and next retry. Confirm the current resource in an authorized browser after reconnect.
- For access issues, confirm owner or membership, active state, permission, applicable location and entitlements. Revoke a lost device in Management. For account recovery, a stored recovery code can satisfy MFA once. Password recovery preserves MFA. There is no assisted total-loss endpoint.
- Use sanitized internal IDs when coordinating a payment incident. Compare verified provider transactions with scoped Veyra records. Leave ambiguous attempts unresolved until authoritative evidence arrives.

Worker heartbeats and aggregate queue diagnostics are available in the staging image. Hosted acceptance still needs a protected log collector, an alert destination and tested escalation. Classify repeated SMTP or Resend errors before reviewing or requeuing affected verification jobs. The worker records provider acceptance without asserting inbox delivery. A resend can create a fresh request after a terminal failure. SMTP acknowledgement loss can produce duplicate emails, while activation still consumes eligibility exactly once.

## Investigate an unconfirmed QRIS attempt

1. Find the order and merchant reference in the tenant payment records. For Midtrans, use the existing payment retry to request current provider status.
2. Treat an unresolved attempt as a hold on collection. Wait for authenticated terminal evidence before generating another QR or collecting cash or card. BCA currently depends on callbacks for this resolution.
3. If the payment shows Reconciliation required, compare the amount, merchant reference and provider transaction ID with the merchant portal. Escalate contradictory captures through the approved provider procedure and preserve the audit history.

## Investigate an unconfirmed refund

1. Check the original capture and refund reference in the approved provider dashboard and in Veyra's refund history.
2. Use the verification action or let the refund worker recheck status. A pending result retains the reservation.
3. Only retry through the supported owner action. It reuses the existing merchant refund key within the supported retry window.
4. Resolve balance and receipt differences after confirmed evidence. Operational order cancellation is a separate action.

[Payment and refund lifecycle](PAYMENTS.md) · [Security and recovery](SECURITY.md)
