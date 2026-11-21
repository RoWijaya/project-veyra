# Security and authorization

## Security model summary

Veyra has three distinct access contexts:

1. **Canonical business owner** — the user referenced by `Business.owner`.
2. **Management collaborator** — an optional authenticated user with explicit business-level permission strings in `Membership.permissions`.
3. **Paired station device** — a device authenticated by a bearer token and limited by the station's capability allowlist and business/location scope.

A workspace selection or workspace assignment is not an authorization grant.

## User authentication

- Custom `User` uses unique email as `USERNAME_FIELD`.
- Django password validators are configured: similarity, minimum length, common password, and numeric password checks.
- Registration email verification is represented by `email_verified_at`.
- Django password-reset views are configured.
- Session cookies are HttpOnly and SameSite=Lax; in non-debug mode, session and CSRF cookies are Secure and HTTPS redirect is enabled.
- MFA/passkeys are not implemented in the inspected code.

## Management permissions

`Membership.permissions` is an explicit JSON list. `Membership.allows()` grants canonical owners access and checks exact permission strings for collaborators.

Known permission strings include:

- `catalog.view`, `catalog.manage`
- `orders.view`, `orders.create`, `orders.manage`
- `locations.view`, `locations.manage`
- `customers.view`, `customers.manage`
- `inventory.view`, `inventory.manage`
- `employees.view`, `employees.manage`

Collaborators cannot grant permissions they do not themselves hold; the owner can grant supported permissions. The current model is business-wide, not location- or workspace-scoped.

## Station authentication

- Pairing codes are random and stored as SHA-256 digests.
- Codes expire after five minutes and are single-use.
- Pairing returns a random bearer token once; only its digest is stored.
- Pairing attempts are rate-limited by source IP through Django cache.
- Device requests must include an Authorization Bearer token.
- The device lookup binds the token to the station ID in the URL and requires a non-revoked device and active station.
- Capability checks happen server-side.
- Owner can revoke paired devices; disabled stations stop authenticating.
- Device fingerprinting is not used as an authentication factor.

The current implementation does not expose a token-renewal/rotation flow. Treat bearer tokens as secrets and use TLS for all device traffic.

## Payment credential storage

- Payment connections are unique per business/provider.
- Only the canonical owner can manage the connection through service-level checks.
- Credential bundles are encrypted using Fernet authenticated encryption.
- A stable `PAYMENT_CREDENTIALS_ENCRYPTION_KEY` is required.
- Credentials should never be logged, placed in audit metadata, committed to Git, or sent to the browser.
- `verified_at` remains unset because real provider verification is not implemented.

The presence of ciphertext is not proof of valid credentials, provider authorization, or payment readiness.

## HTTP security controls

The settings include Django CSRF middleware, clickjacking protection, security middleware, and a custom middleware that sets Content-Security-Policy, Permissions-Policy, and Referrer-Policy. In non-debug mode, the configuration enables HTTPS redirect, secure cookies, HSTS for one hour with subdomains, content-type nosniff, and WhiteNoise.

The CSP currently allows scripts from `self` and `cdn.jsdelivr.net`, permits same-origin and `ws:`/`wss:` connections, and disallows objects and framing. Review the policy against the deployed asset and script requirements before production.

## Security gaps / cautions

- MFA/passkeys are not implemented.
- No formal security assessment or penetration test is evidenced by this archive.
- Workspace/location-specific collaborator authorization is not implemented.
- Invitation revocation is not implemented in the current permissions note.
- Station token rotation/renewal is not implemented.
- Payment-provider API authentication, webhook signatures, replay protection, idempotent payment processing, reconciliation, and refunds are not implemented.
- Rate limiting depends on cache configuration; verify the production Redis cache is active and correctly protected.
- Production secrets and Redis/PostgreSQL network exposure must be controlled by deployment configuration.
- The project should undergo security review and end-to-end tests against production-like PostgreSQL and Redis before handling real transactions.
