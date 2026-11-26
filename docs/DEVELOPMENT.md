# Development

Start with the [Windows quick start](../README.md#run-locally-on-windows) and [environment reference](ENVIRONMENT.md). Use Git, Docker Compose, and an installed Python 3.12 or newer.

## Repository map

| Path | Purpose |
| --- | --- |
| [config/](../config/) | Settings, root URLs, ASGI and WSGI entrypoints |
| [core/](../core/) | Business models, permissions, views, services and tests |
| [core/payments/](../core/payments/) | Tenant Midtrans and BCA SNAP adapters |
| [core/management/commands/](../core/management/commands/) | Workers, media backfill and the Channels probe |
| [core/migrations/](../core/migrations/) | Schema and data migrations |
| [templates/](../templates/) and [core/static/core/](../core/static/core/) | Server-rendered pages and browser behavior |
| [pyproject.toml](../pyproject.toml) | Dependency ranges, development extra, pytest and Ruff settings |
| [compose.yaml](../compose.yaml) and [Dockerfile](../Dockerfile) | Local database/cache and separate production app image |

## Run in a POSIX shell

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
test -f .env || cp .env.example .env
docker compose up -d postgres redis
docker compose ps
```

Wait for both services to be healthy.

```sh
.venv/bin/python manage.py migrate
.venv/bin/python -m uvicorn config.asgi:application --reload --no-access-log
```

Start ``.venv/bin/python manage.py process_outbox`` and ``.venv/bin/python manage.py process_account_mail`` in separate terminals. The account mail worker prints signup links when you use the local console backend. Both shell examples use the virtual environment directly. For runtime dependencies alone, the project also supports ``python -m pip install -r requirements.txt``.

## Routine checks

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py migrate --plan
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test core.test_ordering_builder --noinput
.\.venv\Scripts\python.exe manage.py test --noinput
.\.venv\Scripts\python.exe manage.py check_realtime
```

Run ``manage.py makemigrations core`` when intentionally creating a schema change. Inspect the migration and test forward application before applying it to existing data. Preserve committed history. Use ``manage.py createsuperuser`` for optional local Django admin access. Production administrators must satisfy the configured MFA policy.

Use business-scoped lookups and the existing permissions and service layers. Verify new write behavior through HTTP or ASGI and add focused transactional coverage for concurrency and idempotency. See [testing](TESTING.md).

## Diagnose local startup

| Symptom | Check |
| --- | --- |
| PostgreSQL connection error | Check ``docker compose ps``, ``docker compose exec -T postgres pg_isready -U veyra -d veyra``, the database URL, and port availability. |
| Live state stays stale | Check ``/readyz/``, ``docker compose exec -T redis redis-cli ping``, the outbox worker, and the browser WebSocket. Run ``manage.py check_realtime`` to verify Channels fan-out. |
| No account email | Check the local server terminal. A configured Resend key overrides console mail. |
| Missing static assets | Start the actual ASGI entrypoint in debug or verify the container collected static assets. Check permissions separately for uploaded media. |
| Test database creation fails | Use a local PostgreSQL role allowed to create a separate test database. Check the connection target before rerunning. |
| Payment method or feature is unavailable | Check the business plan, location settings, connection eligibility, and supported payment entry point. |

[Deployment](DEPLOYMENT.md) · [Operations](OPERATIONS.md)
