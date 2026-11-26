# Configuration reference

Copy [the sanitized example](../.env.example) into a private ``.env`` at the repository root. [Django settings](../config/settings.py) load it without overriding variables already set in the process. Use a secret store in hosted environments. [Compose](../compose.yaml) has separate local-only PostgreSQL credentials.

For boolean settings, ``1``, ``true``, and ``yes`` enable the setting, case insensitive. An empty environment variable overrides a fallback. Leave optional credential groups blank until configured.

## Runtime

| Variable | When needed | Format and local default | Sensitive |
| --- | --- | --- | --- |
| ``DJANGO_DEBUG`` | Set for hosted environments | Boolean, defaults true. Set false for hosted services. | No |
| ``DJANGO_SECRET_KEY`` | Required outside debug | Random string of at least 40 characters in non-debug mode. The built-in fallback and example are for local development only. | Yes |
| ``DJANGO_SECRET_KEY_FALLBACKS`` | Signing-key rotation and recovery | Comma-separated previous secrets retained for signatures and queued verification redirects. | Yes |
| ``DJANGO_ALLOWED_HOSTS`` | Set outside localhost | Comma-separated hostnames, no schemes. Default ``localhost,127.0.0.1``. | No |
| ``DJANGO_CSRF_TRUSTED_ORIGINS`` | Optional trusted requesting origins | Comma-separated origins with schemes. Default blank. Use HTTPS for hosted origins. | No |
| ``DJANGO_HSTS_SECONDS`` | Non-debug mode | Positive integer seconds, default 3600. Includes subdomains. | No |
| ``DATABASE_URL`` | PostgreSQL for normal operation and integration tests | ``postgresql://`` or ``postgres://`` URI. Local default matches Compose. Supports ``sslmode``, ``sslrootcert`` and ``connect_timeout``. Default connect timeout 5 seconds. Percent-encode delimiter characters in credentials. | Yes when authenticated |
| ``REDIS_URL`` | Channels, readiness, and non-debug cache | ``redis://`` locally, authenticated ``rediss://`` with TLS for hosting. Channels/readiness default to Redis database 0. The non-debug cache falls back to database 1 if unset. An explicit URL applies to both. | Yes when authenticated |
| ``DJANGO_SQLITE_TEST`` | Optional test-only shortcut | Boolean, default false. Uses in-memory SQLite and in-memory Channels. Use in debug mode for quick checks. | No |
| ``DJANGO_SETTINGS_MODULE`` | Django framework override | Defaults to ``config.settings`` in project entrypoints. | No |
| ``PORT`` | Docker listener override | Integer TCP port, default 8000 in the container command. | No |

### Private staging Compose

| Variable | Value |
| --- | --- |
| `VEYRA_STAGE_IMAGE` | Built staging image reference. Use the reviewed digest when publishing to an approved private registry. |
| `VEYRA_STAGE_ENV_FILE` | Path to the protected staging app environment file. |
| `VEYRA_STAGE_POSTGRES_PASSWORD` | Separate staging database password. Keep the app `DATABASE_URL` consistent with the internal `postgres` service. |
| `VEYRA_STAGE_PORT` | Host loopback listener for the trusted HTTPS proxy. Defaults to 8010. |

The staging manifest injects non-debug mode and `redis://redis:6379/0` for internal Compose services. The development Compose file publishes only IPv4 loopback addresses.

## Identity and email

| Variable | When needed | Format and local default | Sensitive |
| --- | --- | --- | --- |
| ``GOOGLE_CLIENT_ID`` | Google OIDC with both values below | OAuth web-client identifier. Default blank. | Public identifier |
| ``GOOGLE_CLIENT_SECRET`` | Google OIDC | OAuth client secret. Default blank. | Yes |
| ``GOOGLE_REDIRECT_URI`` | Google OIDC | Exact registered callback. Default blank. The example uses ``http://127.0.0.1:8000/auth/google/callback/``. | No |
| ``ACCOUNT_SECURITY_ENCRYPTION_KEY`` | Authenticator setup and verification | Stable Fernet key. Default blank. Use a separate key from payment encryption. | Yes |
| ``ACCOUNT_SECURITY_ENCRYPTION_FALLBACK_KEYS`` | MFA key rotation and recovery | Comma-separated keys that can decrypt records written by retired primary keys. | Yes |
| ``MFA_REQUIRE_PRIVILEGED`` | To override privileged enrollment behavior | Boolean. Defaults true outside debug, false in debug. Enrolled users always require MFA. | No |
| ``DEFAULT_FROM_EMAIL`` | Authorized account email sender | Address or display name and address. Default ``Veyra <accounts@veyra.app>``. | No |
| ``RESEND_API_KEY`` | Optional HTTPS mail provider | Default blank. A configured key selects Resend even in debug. | Yes |
| ``EMAIL_HOST`` | SMTP in non-debug mode when Resend is unset | Hostname. Default localhost. | Deployment detail |
| ``EMAIL_PORT`` | SMTP | Integer. Default 1025. | No |
| ``EMAIL_HOST_USER`` | Authenticated SMTP | Username. Default blank. | Yes |
| ``EMAIL_HOST_PASSWORD`` | Authenticated SMTP | Password or token. Default blank. | Yes |
| ``EMAIL_USE_TLS`` | SMTP STARTTLS | Boolean, default false. Mutually exclusive with implicit TLS. | No |
| ``EMAIL_USE_SSL`` | SMTP implicit TLS | Boolean, default false. Mutually exclusive with STARTTLS. | No |

Without Resend, debug mode uses the console backend and non-debug mode uses SMTP. Run the account mail worker to deliver queued verification messages. Local console mail contains verification and recovery links. Keep that output private.

## Payments and media

| Variable | When needed | Format and local default | Sensitive |
| --- | --- | --- | --- |
| ``PAYMENT_CREDENTIALS_ENCRYPTION_KEY`` | Tenant merchant connection storage | Stable Fernet key. Default blank. Tenant merchant credentials are entered per business and stored encrypted. | Yes |
| ``PAYMENT_CREDENTIALS_ENCRYPTION_FALLBACK_KEYS`` | Payment key rotation and recovery | Comma-separated decryption keys retained while old records or backups need them. | Yes |
| ``VEYRA_BILLING_MIDTRANS_ENV`` | Platform checkout | ``sandbox`` or ``production``. Default sandbox. | No |
| ``VEYRA_BILLING_MIDTRANS_SERVER_KEY`` | Platform checkout and reconciliation | Separate Veyra merchant server key. Default blank. | Yes |
| ``VEYRA_BILLING_MIDTRANS_SIGNATURE_SECRET_KEY`` | Platform Midtrans callback | The Signature Secret Key for Veyra’s subscription merchant and the selected environment. A blank value blocks new checkout and fails closed unless the platform legacy drain is explicitly enabled. | Yes |
| ``VEYRA_BILLING_PUBLIC_ORIGIN`` | Platform checkout | Public origin, no path, HTTPS for hosting. Default blank. | No |
| ``SUPABASE_MEDIA_URL`` | Private archive, with both values below | HTTPS Supabase project origin. Default blank. | Deployment detail |
| ``SUPABASE_MEDIA_BUCKET`` | Private archive | Private bucket name. Default blank. | Deployment detail |
| ``SUPABASE_SERVICE_ROLE_KEY`` | Private archive | Server-only Supabase key. Default blank. | Yes |

Generate a distinct key for each Fernet setting. Copy it directly into the private secret store and retain a protected backup.

```powershell
.\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

New writes use the primary key. Previously written records can use any configured fallback key. Re-encrypt and verify old records before retiring keys, and retain keys separately for any backups still within retention. [Deployment](DEPLOYMENT.md) · [Security](SECURITY.md) · [Payments](PAYMENTS.md)


## Stage 1 payment and worker configuration

| Variable | Purpose | Secret |
| --- | --- | --- |
| VEYRA_TRUSTED_PROXY_IPS | Exact trusted direct proxy peers or dedicated proxy networks. Defaults to loopback. No all-address entries. Include the container loopback health-probe peer. | No |
| VEYRA_WORKER_INSTANCE_ID | Unique per worker replica of the same type. Defaults to the container hostname. | No |
| MIDTRANS_LEGACY_SIGNING_CONNECTIONS | Comma-separated exact IDs for authorized legacy tenant connections. Blank disables fallback. | No |
| VEYRA_BILLING_MIDTRANS_ALLOW_LEGACY_SIGNATURE | Opt in to legacy platform notifications for a drain, separate from tenant compatibility. Default false. | No |
| VEYRA_BILLING_MIDTRANS_APPROVAL_REFERENCE | Independent operator evidence reference for new platform checkout. Use printable ASCII up to 120 characters. | No |
| VEYRA_BILLING_MIDTRANS_APPROVED_AT | Approval time with an RFC 3339 timezone offset. Future approvals are rejected. | No |
| VEYRA_BILLING_MIDTRANS_PREVIOUS_SIGNATURE_SECRET_KEY | Prior platform signing material for an approved cutover. | Yes |
| VEYRA_BILLING_MIDTRANS_ROTATED_AT | Platform cutover time with a timezone offset. | No |
| VEYRA_BILLING_MIDTRANS_PREVIOUS_SIGNATURE_UNTIL | Prior signing key deadline with a timezone offset. Maximum window is 30 days. | No |
