# Integrations

Use [.env.example](../.env.example) for configuration names. Never commit secrets.

## PostgreSQL, Redis and ASGI

`DATABASE_URL` selects PostgreSQL. `REDIS_URL` selects Redis for Channels and production cache. Start existing containers with `docker compose up -d postgres redis`; keep their named volumes. `/healthz/` checks HTTP liveness. `/readyz/` executes PostgreSQL `SELECT 1` and Redis PING, returning 200/503 without dependency details. It does not verify payment/SMTP or worker health.

Run `uvicorn config.asgi:application --no-access-log` and supervise `python manage.py process_outbox` separately. The worker retries unpublished outbox rows and checks promotion schedule transitions. See [REALTIME.md](REALTIME.md) and [STATIONS-AND-DEVICE-API.md](STATIONS-AND-DEVICE-API.md) for actual event/device contracts.

## Account email

Debug mode uses console mail. Verification messages intentionally contain bearer links in the local development terminal: never ship this output to production logs. Non-debug mode uses SMTP configured by `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` or `EMAIL_USE_SSL` and `DEFAULT_FROM_EMAIL`. TLS and SSL are mutually exclusive. Django 6.1 uses `MAILERS`; older supported versions use `EMAIL_BACKEND` settings.

Registration creates an inactive account. `POST /verification/resend/` accepts `email` and CSRF token. Eligible inactive/unverified accounts receive a 24-hour verification link. Successful verification consumes eligibility under a PostgreSQL row lock. Unknown addresses and throttled sends receive the same public confirmation. SMTP failures still propagate; durable email retries and delivery monitoring remain required.

## Google identity

Protocol references: [Google OIDC](https://developers.google.com/identity/openid-connect/openid-connect), [Authlib Django client source documentation](https://github.com/authlib/authlib/blob/main/docs/oauth2/client/web/django.rst), [PyOTP security and replay guidance](https://pyauth.github.io/pyotp/).

Configure `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and `GOOGLE_REDIRECT_URI`. Create a Google OAuth web client and register the exact callback, for example `http://127.0.0.1:8000/auth/google/callback/` locally and HTTPS in production. CSRF-protected `POST /auth/google/start/` initiates Authlib discovery-backed code flow with state, nonce and S256 PKCE. The callback validates signed tokens and verified email, then binds the provider subject. Existing email matches require sign-in and explicit Connect Google from account security; there is no automatic merging. Provider access/refresh/ID tokens are not stored. Google buttons appear only when configuration is complete.

Tests mock provider HTTP endpoints and issue RSA-signed test tokens through real Authlib validation. Valid flow, PKCE, state replay, invalid signature/issuer/audience/nonce/expiry, unverified email and explicit linking are covered. Live Google consent remains unverified because credentials are unavailable.

## Authenticator MFA

Configure `ACCOUNT_SECURITY_ENCRYPTION_KEY` with a stable Fernet key, separate from merchant encryption. Generate it using `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` and keep it in secret storage/backups. A local key was configured without displaying it. Changing or losing the key prevents verification of existing encrypted TOTP secrets.

`MFA_REQUIRE_PRIVILEGED` defaults true outside debug and false in debug. Keep it true in production; set true locally for owner/admin enrollment checks. Enrolled accounts always require MFA. `/account/security/` offers password/recent-Google reauthentication, encrypted TOTP setup, private QR, ten single-use recovery codes and removal/replacement. `/account/mfa/` challenges sign-in. `/admin/login/` uses shared primary login. Passkeys, assisted recovery after total factor loss, Google disconnection and session inventory remain missing.

## Merchant payments

Midtrans and BCA SNAP QRIS adapters live in `core/payments.py`. Each tenant owns a `PaymentConnection`; provider bundles are encrypted with `PAYMENT_CREDENTIALS_ENCRYPTION_KEY`. The stable Fernet key must survive redeployments and be backed up securely. Callbacks enter `POST /api/payments/webhooks/<provider>/`; signatures, request digests and state transitions are checked server-side. Payment creation targets ten-minute QRIS expiry subject to provider behavior.

Provider initiation and callback tests use mocked provider responses. The guest takeaway HTTP test covers dynamic QRIS generation, private QR access, preparation gating and idempotent release from a signed Midtrans settlement notification. Real merchant onboarding, settlement, reconciliation and refunds need provider access and further implementation. Do not interpret a stored connection or format verification as proof of live settlement. SaaS billing is separate and not integrated with a billing provider.

## Printing and monitoring

Stations pair with one-time codes, then use hashed bearer credentials. Print jobs are durable requests; physical delivery and recovery require a compatible station agent/printer. No hardware was tested in this environment.

Structured JSON goes to stderr through Django logging. No Sentry, metrics collector or alert destination is configured. Deploy a log collector with retention/access controls and alerts; see [OPERATIONS.md](OPERATIONS.md). Configure proxy and ASGI access logs to exclude full URLs, tokens and query strings.
