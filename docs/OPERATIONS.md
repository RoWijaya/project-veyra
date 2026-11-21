# Operations

## Identity deployment and recovery

Apply migration 0019 before starting this release. Configure Google web-client credentials/exact HTTPS callback and a separate stable ACCOUNT_SECURITY_ENCRYPTION_KEY before TOTP enrollment. Back up that key securely with encrypted database backups. Never log provisioning URIs, secrets or recovery codes. Missing configuration prevents enrollment; changing a key without re-encryption breaks existing factors.

Keep MFA_REQUIRE_PRIVILEGED=true in production; it defaults on when DEBUG is false. Platform administrators use normal login/MFA before Django admin. Users must save recovery codes when first displayed. Each works once; password reset does not remove MFA. No email-only or support endpoint bypasses MFA, and no assisted total-loss recovery flow exists. Establish a reviewed recovery procedure before production use.

## Startup and releases

Follow [DEVELOPMENT-AND-DEPLOYMENT.md](DEVELOPMENT-AND-DEPLOYMENT.md) for environment setup. Preserve `.env`, encryption keys and PostgreSQL/Redis volumes. Start dependencies with `docker compose up -d postgres redis`. Apply additive migrations with `.venv\Scripts\python.exe manage.py migrate`; never reset the database to solve a migration failure. Production runs `uvicorn config.asgi:application --no-access-log` behind a trusted TLS proxy, plus a separately supervised `python manage.py process_outbox` worker.

Set strong `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=false`, allowed hosts, private DB/Redis URLs, stable payment encryption key and SMTP. Run `collectstatic`, `check --deploy`, migration checks and PostgreSQL/Redis tests before release. The Compose file currently supplies dependency services only; it does not deploy or supervise the app/worker.

## Health and logs

`GET /healthz/` is liveness. `GET /readyz/` checks PostgreSQL and Redis; success is `{"status":"ready"}`, failure is HTTP 503 with `{"status":"unavailable"}`. Responses prohibit caching. Redis uses two-second connect/read timeouts; DB uses configured five-second connection timeout. Readiness cannot establish worker, email, payment-provider or schema readiness.

HTTP responses include `X-Request-ID`. JSON `request.completed` logs include request ID, HTTP method, route pattern, status and duration. Raw paths/query strings, request bodies, cookies, arbitrary messages and exception text are excluded. A validated incoming 32-character lowercase hexadecimal ID is retained; otherwise one is generated. IDs correlate requests and are not authentication credentials. `readiness.failed` and `outbox.delivery_failed` are operational error events. Outbox failures persist an exception class and retry schedule, excluding exception secrets.

Disable full-URL access logs at Uvicorn and the reverse proxy. Debug console email contains verification links by design; keep it local. Logs lack full stack traces, metrics and external exception tracking. Configure a protected collector, retention policy and alerts for 5xx, readiness failures, repeated outbox failures and oldest unpublished event age. No alert destination or external collector is configured here.

## Investigation and recovery

1. Record time and response request ID; locate the matching `request.completed` event.
2. Check status, route and duration. Do not paste raw credentials, token URLs or bodies into tickets.
3. Check DB/Redis container health and `/readyz/`. Restore connectivity before restarting workers.
4. Inspect unpublished OutboxEvent count, oldest creation time, attempts and next retry. Supervise/restart the worker; pending events survive application restarts. Published channel messages are not a client acknowledgement.
5. For payment incidents, compare tenant-scoped transaction/provider references with the provider dashboard. Never manually claim settlement from an unverified callback.
6. Rotate exposed station/provider credentials, revoke affected access, preserve audit history and record impact. Encryption-key rotation needs a deliberate decrypt/re-encrypt procedure.

## Backup and restore

Use PostgreSQL custom-format dumps, encrypted off-site storage and protected key backups. Example: `pg_dump --format=custom --file=veyra.dump "$DATABASE_URL"`. Restore into an explicitly named isolated recovery database using `pg_restore --dbname=<recovery-database> veyra.dump`; verify tenant data, migrations and transactional workflows there before any production recovery. No restore drill, RPO/RTO, production retention or failover evidence exists yet. Never point a recovery command at the current development or production database casually.

## Platform subscription billing

Publish monthly whole-IDR plans in the platform admin before enabling checkout. Provide a dedicated Veyra Midtrans environment and server key through VEYRA_BILLING_MIDTRANS_ENV and VEYRA_BILLING_MIDTRANS_SERVER_KEY. Set VEYRA_BILLING_PUBLIC_ORIGIN to the public HTTPS origin. Configure the platform merchant's notification URL to /api/billing/notifications/midtrans/. Keep the platform merchant separate from tenant QRIS credentials.

Run python manage.py process_billing as a supervised worker alongside process_outbox, or schedule python manage.py process_billing --once at least once per minute. The first advances subscription boundaries and reconciles payment status. The second publishes post-commit owner updates. Without a running billing worker, renewal invoices and provider status reconciliation are delayed even though time-based entitlement checks still apply.

Review payment and subscription audit events in Django admin for failed, late, or conflicting transactions. Live merchant credentials, notification delivery and provider settlement need sandbox and production acceptance. Refunds and chargebacks require platform review and reconciliation.
