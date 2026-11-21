# Development and deployment

## Requirements

- Python 3.12 or newer
- Docker Desktop / Docker Engine with Compose
- PostgreSQL 17-compatible service (Compose file uses `postgres:17-alpine`)
- Redis 7-compatible service (Compose file uses `redis:7-alpine`)
- Git

The project dependencies are declared in `pyproject.toml`. The package allows Django `>=5.2,<6.2`, Channels 4.x, `channels-redis` 4.x, psycopg 3.x, cryptography, Uvicorn, Gunicorn, and WhiteNoise.

## Local setup (Windows PowerShell)

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

Start the outbox worker in another terminal:

```powershell
python manage.py process_outbox
```

The Compose file exposes PostgreSQL on port `5432` and Redis on `6379` for local development. It defines persistent volumes and health checks. Do not expose these development ports publicly in production.

## Environment variables

| Variable | Purpose |
|---|---|
| `DJANGO_DEBUG` | Enables/disables debug mode |
| `DJANGO_SECRET_KEY` | Django secret key; use a long random secret in production |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated allowed host names |
| `DATABASE_URL` | PostgreSQL connection URL |
| `REDIS_URL` | Redis URL used for Channels and, in production, cache |
| `DJANGO_SQLITE_TEST` | Explicit test-only switch to in-memory SQLite and in-memory Channels layer |
| `PAYMENT_CREDENTIALS_ENCRYPTION_KEY` | Fernet key used to encrypt payment credential bundles |
| `DEFAULT_FROM_EMAIL` | Sender address for account email |
| `EMAIL_HOST` | SMTP host |
| `EMAIL_PORT` | SMTP port |
| `EMAIL_HOST_USER` | SMTP username |
| `EMAIL_HOST_PASSWORD` | SMTP password |
| `EMAIL_USE_TLS` | Enable SMTP STARTTLS |
| `EMAIL_USE_SSL` | Enable SMTP implicit TLS; do not enable together with TLS |

The `.env.example` file is the authoritative template for the current variable names and defaults. Never commit real `.env` values.

## Payment encryption key

Generate a Fernet key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Store it in a deployment secret manager. Keep a protected backup. If the key is lost, existing credential ciphertext cannot be decrypted. Key rotation requires a deliberate decrypt/re-encrypt migration procedure; replacing the key directly will make existing credentials unreadable.

## Production checklist

1. Set `DJANGO_DEBUG=false`.
2. Set a random `DJANGO_SECRET_KEY` (the settings require at least 40 characters).
3. Set `DJANGO_ALLOWED_HOSTS`.
4. Set a production PostgreSQL `DATABASE_URL`.
5. Set a private `REDIS_URL`.
6. Set `PAYMENT_CREDENTIALS_ENCRYPTION_KEY`.
7. Configure SMTP and choose either TLS or SSL, not both.
8. Terminate HTTPS at a trusted reverse proxy and preserve the forwarded HTTPS scheme configuration.
9. Run migrations as a release step.
10. Collect static assets as required by the deployment platform.
11. Run `uvicorn config.asgi:application` behind the trusted proxy.
12. Run `python manage.py process_outbox` as a separate supervised worker.
13. Configure encrypted off-site backups and test restore procedures.
14. Monitor application errors, database health, Redis, outbox backlog, and worker liveness.

## Tests and checks

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py migrate
python manage.py test
```

For isolated local tests:

```powershell
$env:DJANGO_SQLITE_TEST = "1"
python manage.py test
```

Use PostgreSQL and Redis integration tests for deployment confidence. Passing SQLite tests does not establish production database or distributed delivery behavior.

## Backups

Backup:

```bash
pg_dump --format=custom --file=veyra.dump "$DATABASE_URL"
```

Restore to a designated recovery database:

```bash
pg_restore --clean --if-exists --dbname="$DATABASE_URL" veyra.dump
```

Use encrypted off-site storage. Practice restores in an isolated environment and never point a recovery command at production unless the incident runbook explicitly requires it.
