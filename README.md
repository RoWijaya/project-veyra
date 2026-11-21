# Veyra

**Everything in sync.**

Veyra is a Django modular-monolith foundation for multi-business operations. The inspected repository currently provides verified-email accounts, business ownership and optional collaborators, tenant-scoped catalog and POS orders, locations, customers, inventory records and stock movements, audit events, paired operational stations, encrypted per-business payment-credential storage, and an order-event outbox with a Channels/WebSocket delivery path.

> **Implementation status:** This repository is an MVP foundation, not a complete production POS SaaS. In particular, payment-provider API processing and verification, MFA, advanced workspace authorization, analytics, billing, and several operational workflows are not implemented. See [Feature status](docs/FEATURE-STATUS.md).

## Documentation

- [System architecture](docs/SYSTEM-ARCHITECTURE.md)
- [Domain model and database](docs/DATABASE.md)
- [Application workflows and routes](docs/FEATURES-AND-ROUTES.md)
- [Authorization and security](docs/SECURITY-AND-AUTHORIZATION.md)
- [Stations and device API](docs/STATIONS-AND-DEVICE-API.md)
- [Realtime events](docs/REALTIME.md)
- [Configuration and deployment](docs/DEVELOPMENT-AND-DEPLOYMENT.md)
- [Feature status and known gaps](docs/FEATURE-STATUS.md)

Existing project notes:
- [Architecture reconciliation](docs/architecture-reconciliation.md)
- [Permissions](docs/permissions.md)
- [Landing film workflow](docs/landing-cinematic-workflow.md)

## Stack

- Python 3.12+
- Django (dependency range: `>=5.2,<6.2`)
- PostgreSQL (normal development and production)
- Django Channels and `channels-redis`
- Redis
- Uvicorn / ASGI
- WhiteNoise for production static assets
- Fernet encryption for stored payment credential bundles
- Django templates and static CSS/JavaScript for the current web interface

## Windows development

Install Python 3.12+, Docker Desktop, and Git. Run these commands from the repository root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
docker compose up -d postgres redis
python manage.py migrate
python manage.py createsuperuser
uvicorn config.asgi:application --reload
```

In a second terminal, run the outbox worker:

```powershell
python manage.py process_outbox
```

In debug mode, account email uses Django's console email backend. Verification and password-reset links are printed to the application terminal.

For isolated local tests only:

```powershell
$env:DJANGO_SQLITE_TEST = "1"
python manage.py test
```

Normal development and production require PostgreSQL; the application deliberately does not silently fall back to SQLite.

## Production configuration

Set `DJANGO_DEBUG=false`, a strong `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DATABASE_URL`, `REDIS_URL`, and `PAYMENT_CREDENTIALS_ENCRYPTION_KEY`. Configure SMTP using the variables listed in [Development and deployment](docs/DEVELOPMENT-AND-DEPLOYMENT.md). Terminate HTTPS at a trusted proxy and run the ASGI application with Uvicorn.

`/healthz/` is a liveness endpoint only. It does not establish that PostgreSQL, Redis, SMTP, or payment providers are ready.

## Important operational notes

- Keep `.env`, database dumps, payment credentials, pairing codes, and device bearer tokens out of source control.
- Back up the Fernet encryption key separately and securely. Losing it can make stored payment credentials unreadable.
- Use encrypted off-site database backups and regularly test restores.
- The `process_outbox` command is a separate long-running worker in normal operation.
- Do not interpret stored payment credentials as a verified provider connection. The current code does not call BCA or Midtrans APIs.
