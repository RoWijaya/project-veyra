# Veyra

Veyra is a greenfield Django modular monolith for multi-business operations. The current MVP includes verified-email accounts, business setup, tenant-scoped catalog and POS orders, status transitions, locations, customers, inventory balances and movements, append-only audit events, PostgreSQL, Redis, and Channels.

## Windows development

Install Python 3.12+, Docker Desktop, and Git. From this directory:

    py -3.12 -m venv .venv
    .\.venv\Scripts\Activate.ps1
    python -m pip install --upgrade pip
    python -m pip install -e ".[dev]"
    Copy-Item .env.example .env
    docker compose up -d postgres redis
    python manage.py migrate
    python manage.py createsuperuser
    uvicorn config.asgi:application --reload --no-access-log

In a second terminal, run `python manage.py process_outbox` to publish order events to Redis and connected WebSocket clients.

Registration email uses Django's console backend in debug mode. Email links appear in the server terminal. PostgreSQL and Redis are separate services; the application does not silently fall back to SQLite.

Payment credential storage requires a stable Fernet key in `PAYMENT_CREDENTIALS_ENCRYPTION_KEY`. Generate one with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` and keep it in the deployment secret store. Losing or rotating this key without re-encrypting stored credentials makes them unreadable.

For isolated local tests only, set $env:DJANGO_SQLITE_TEST = "1" and run python manage.py test. Production and normal development use PostgreSQL.

## Production

Set DJANGO_DEBUG=false, a random DJANGO_SECRET_KEY of at least 40 characters, DJANGO_ALLOWED_HOSTS, DATABASE_URL, REDIS_URL, and PAYMENT_CREDENTIALS_ENCRYPTION_KEY in the deployment environment. Terminate HTTPS at a trusted proxy, run migrations as a release step, then run uvicorn config.asgi:application --no-access-log. Configure SMTP with EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD, and one of EMAIL_USE_TLS or EMAIL_USE_SSL. Run `python manage.py process_outbox` as a separate worker to retry operational event delivery.

`/healthz/` is a liveness check only; it does not claim PostgreSQL, Redis, email, or payment-provider readiness.

PostgreSQL backup and restore:

    pg_dump --format=custom --file=veyra.dump "$DATABASE_URL"
    pg_restore --clean --if-exists --dbname="$DATABASE_URL" veyra.dump

Use encrypted off-site backups and test restores on a separate database. Do not run restore against production unless the recovery procedure calls for it.

## Scope status

This repository provides the multi-tenant SaaS foundation for Veyra. Business ownership is explicit. Optional Management collaborators receive permission strings without rigid employee-role hierarchies. Owners can create stations, issue one-time pairing codes, revoke persistent device sessions, and rotate device bearer tokens. Operational stations support digital KDS screens, preparation ticket print queues, product-to-station routing, and controlled reprinting.

Per-business payment provider connections support Midtrans QRIS and BCA SNAP QRIS MPM Dynamic API adapters, Fernet credential encryption at rest, connection verification, webhook signature checks, idempotent replay-safe processing, and provider-confirmed order release (`after_paid`).

Passkeys and assisted factor recovery, workspace-scoped collaborator authorization grants, suppliers/purchase orders, SaaS billing, and production media storage still need implementation and verification. WebSocket delivery has not been verified against deployed Redis infrastructure. See `docs/` for architecture and specification details.

## Development checks

    python manage.py makemigrations core
    python manage.py migrate
    python manage.py check
    python manage.py test

## Maintained product documentation

Start with [PRD](docs/PRD.md), [feature status](docs/FEATURE-STATUS.md), [architecture](docs/ARCHITECTURE.md), [security](docs/SECURITY.md), [integrations](docs/INTEGRATIONS.md), [testing](docs/TESTING.md), [operations](docs/OPERATIONS.md) and [development](docs/DEVELOPMENT.md). `/readyz/` checks actual PostgreSQL/Redis connectivity; `/healthz/` remains liveness only.
