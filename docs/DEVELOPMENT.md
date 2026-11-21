# Development

## Structure and workflow

`config/` defines Django/ASGI settings; `core/models.py` owns persistence; `core/services.py` coordinates transactional business operations; `views.py` and `device_views.py` expose HTTP; `permissions.py` supplies access checks; `consumers.py`/`routing.py` handle Channels; `observability.py` supplies correlation, safe JSON and readiness. Templates and `core/static/core/` implement the server-rendered interface. No frontend framework migration is planned.

Use [DEVELOPMENT-AND-DEPLOYMENT.md](DEVELOPMENT-AND-DEPLOYMENT.md) for setup, [TESTING.md](TESTING.md) for verification and [PRD.md](PRD.md) plus [FEATURE-STATUS.md](FEATURE-STATUS.md) for scope. Preserve tenants and applied migrations. Add new migrations for schema changes. Reuse existing service/authorization/outbox patterns. Check every caller when changing shared behavior. New user workflows must connect HTTP, business logic, persistence and real interfaces.

Scope queries to the authenticated tenant; validate object relationships, money and idempotency at trust boundaries. Use Decimal for money, transactions/row locks for coordinated writes and append-only records for operational history. Never trust browser-supplied prices or payment success. Keep credentials and token-bearing URLs out of source and logs.

## Common commands

```powershell
.venv\Scripts\python.exe manage.py check
.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.venv\Scripts\python.exe manage.py test core --keepdb
.venv\Scripts\python.exe -m ruff check --select I,E4,E7,E9 core
.venv\Scripts\python.exe -m uvicorn config.asgi:application --reload --no-access-log
.venv\Scripts\python.exe manage.py process_outbox
```

The focused Ruff selection checks imports and structural errors; it is not a full style-lint pass. Existing broader lint debt remains.

## Recovery and Git

The inspected repo has untracked project source and no HEAD commit. Do not stage secrets or invent a historical review baseline. A source recovery ZIP excluding `.env` was saved outside the repo before the 2026-11-17 edits; it is not a database backup. Establish a reviewed initial commit before using fixed-point diff reviews. Tracker configuration is absent; to publish specs/tickets, run `/setup-matt-pocock-skills` and configure the tracker. Implementation does not depend on tracker setup.

For each substantial change update applicable PRD, architecture, schema, integration, security, test and operations docs. Record actual verification, missing infrastructure and remaining gaps. Use decision records for significant choices; do not mark planned work as implemented.

Identity code now lives in core/identity.py and identity_views.py; GoogleIdentity and Authenticator were added by migration 0019. New identity tests are in core/test_identity.py and use provider HTTP mocks with signed tokens, plus real PostgreSQL/Redis stream checks. Required owner/admin enrollment defaults on in production and can be enabled locally via MFA_REQUIRE_PRIVILEGED.
