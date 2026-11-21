# Security architecture and verification

## Google and MFA implementation evidence — 2026-11-17

Google OIDC and TOTP MFA are locally integrated and tested. Authlib validates provider signatures, issuer, audience, expiry and nonce; state/S256 PKCE protect code flow. Verified email and subject binding are required. Existing emails never automatically merge accounts; linking requires an authenticated reauthentication step. No provider tokens are persisted. Tests sign RSA tokens and mock only provider HTTP; live Google consent remains unverified.

TOTP secrets use dedicated Fernet encryption. Acceptance permits one interval of clock skew, atomically rejecting counters at or below the last accepted. Ten random 80-bit recovery codes are hashed, consumed under lock and displayed once. Setup and primary-to-MFA challenge expire after ten minutes. Per-account cache throttles allow eight security/challenge attempts per ten minutes; shared production Redis is needed for distributed limits. HTTP and ASGI enforce factor-version proofs; stream delivery checks current active user, server session and factor state. Password and challenge rotate session keys. Owner/platform-staff enrollment defaults on outside debug. Keep MFA_REQUIRE_PRIVILEGED true in production.

Password reset does not remove MFA. Passkeys, assisted recovery after total factor loss, Google disconnection and session inventory remain missing. The ASVS register remains unverified; tests do not establish compliance.

Updated 2026-11-17. Target: OWASP ASVS 5.0.0 Level 2. No compliance or penetration-test certification is claimed. [ASVS-VERIFICATION.md](ASVS-VERIFICATION.md) records the requirement-level assessment backlog using the pinned official release. A passing unit/integration test does not establish an entire requirement.

## Trust boundaries

Public guests hold signed table links and separate private order cookies. Management users hold Django sessions and tenant membership permissions. Paired stations hold hashed/revocable bearer credentials with explicit capabilities. Platform Django admin is separate from merchant ownership. Provider callbacks are untrusted until signature and business transaction checks pass. PostgreSQL is authoritative; Redis carries channel events and production cache state. TLS proxy, DB, Redis, SMTP and external payment providers are distinct operational boundaries.

## Threat model

| Threat / asset | Current mitigation and evidence | Residual risk / required work |
|---|---|---|
| Cross-tenant orders, stock, customer data | Tenant-scoped views/services, membership checks, foreign-object tests and authorized Channels groups | Complete location/workspace grants and reassessment of every endpoint remain |
| Verification replay reactivates disabled account | Locked unverified/inactive predicate; consumed link fails; HTTP regression proves disabled account stays inaccessible | GET verification can be consumed by link scanners; admin-disabled never-verified state is not separately modeled |
| Account enumeration and resend spam | Uniform valid-request confirmation, hashed address/IP cache keys, one eligible send/address/10 minutes, eight IP attempts/window, CSRF test | Timing enumeration, distributed abuse, durable email retries and registration/reset-specific abuse review remain |
| Credential stuffing/account takeover | Django password hashing/validation, login cache throttle, verification registration flow, secure production cookies | TOTP and Google OIDC/linking now exist; passkeys, assisted recovery and session inventory remain missing |
| Guest order disclosure | Per-order signed HttpOnly tracking cookie distinct from table link; guest stream tests | Device compromise, cookie theft, rate limiting and multi-device attack assessment remain |
| Forged or repeated payment callback | Provider signatures, request digests, idempotent state transitions and encrypted tenant credentials | Real provider tests, refunds/reconciliation and complete key lifecycle remain |
| Overselling/double deduction | Transactional order services, row-locked stock balances, unique traceable sale/return movements | Load/concurrency tests and purchasing/transfer workflows remain |
| XSS, CSRF, query injection | Template escaping, CSP, CSRF middleware and ORM queries; resend missing-CSRF test | Full stored/reflected/DOM XSS, SQL injection and upload review is not complete |
| Secrets in telemetry | Allowlisted JSON, route patterns, exception class only, no raw request/body/header/message output; formatter regression | Proxy/ASGI access logs must exclude token URLs; external dependencies may have separate loggers; debug console email intentionally shows links |
| Lost events or unauthorized streams | Durable outbox retry, Redis Channels, per-stream authorization and reconnect snapshots where implemented | No client delivery acknowledgement; remaining domains lack streams; membership revocation during active sockets needs review |
| Data loss and secret-key loss | Additive migrations, protected histories, documented encrypted backups/key backup | Restore drills, production RPO/RTO and DB-level history immutability remain unverified |

## Implemented controls and limits

Registration creates inactive accounts; verified accounts can sign in. Verification messages use timestamped signatures with 24-hour age. The callback locks and consumes unverified account state. Resend ignores active/already-verified accounts and audits eligible sends. Login rotates sessions through Django. Old accounts created outside registration may be active without an email-verification timestamp; mandatory verification across all account-creation/admin paths remains incomplete.

Canonical ownership and active membership permissions authorize Management. Workspace selection never grants access. Station capabilities and device identity are checked server-side. Secrets are encrypted with a deployment-provided stable Fernet key; do not commit `.env`. Audit, stock, webhook and subscription-history protections exist at application/queryset level; DB administrators remain trusted and DB-level append-only enforcement is not proven.

Production settings require a long secret, secure cookies, HTTPS redirection and HSTS. Trust `X-Forwarded-Proto` only from the configured reverse proxy; prevent direct external access to ASGI. Debug must remain false in production. Security headers are defense in depth, not evidence of ASVS completion.

## Incident response

Use [OPERATIONS.md](OPERATIONS.md) for request-ID investigation and recovery. Revoke compromised devices/sessions/merchant keys, preserve audit history, assess tenant impact and restore from isolated verified backups if needed. Never place bearer links or payment secrets in incident reports. No external monitoring destination or incident notification service is configured.

## Storefront and receipt boundaries — 2026-11-18

- Cart identity includes the browser session, signed table token and current QR revision. Mutations use CSRF and submitted revisions. Checkout validates a signed quote against current server pricing and uses the existing idempotent order service and private tracking cookie.
- Media writes require catalog management. Staff image reads require catalog access. Guest image reads revalidate the table token, tenant and product or campaign visibility. Pillow verifies type, bounds, and single-frame content before resizing to WebP and stripping metadata. Preview uses local file data with the existing CSP.
- Receipt reads require an authorized business membership and order permission. Queueing, retries and intentional reprints check order, payment, station/location, printer and mutation authorization. Browser printing uses historical order lines and recorded tender information.

## Guest takeaway boundaries

Location settings require active business membership and location management permission. The pickup link binds the location and its current revision. Revocation invalidates menu, cart, media and WebSocket authorization for that link. A guest may still use their separate private cookie to view orders already placed. Checkout derives table and location assignment from the signed menu link. Payment QR access is scoped to the private tracking cookie, and a pending QRIS order cannot enter preparation before provider confirmation. Test evidence covers another tenant's location, a read-only member, anonymous tracking and QR access, and duplicate settlement notifications.

## Product options

Only catalog managers may save product option definitions. Group, product and location lookups are scoped to the current business. Checkout resolves option identifiers from the selected product and location and enforces group limits. Draft prices are informational. Guest checkout validates the signed order quote again against prices and selected options under the business lock. Audit events record changes to modifier groups. Historical receipt and preparation payloads use order-line snapshots.

## Voucher authorization and abuse controls

Voucher management requires tenant membership with catalog.manage and the promotions entitlement. Scope selections are filtered to the business. Order references in voucher history follow orders.view. POS previews require orders.create and catalog.view, plus customers.view when a saved customer is selected. Guest voucher actions use the existing signed storefront, session cart revision and CSRF protections.

Coupon attempts are limited in Redis to 24 per five minutes per business and authenticated user or immediate network address. Deployment must supply the actual client address at the ASGI boundary for per-origin guest limits. Unknown and foreign-tenant codes receive the same response. POS checkout validates a signed, tenant-and-code-bound approval before looking up its voucher. The approval and guest cart quote never authorize a browser-supplied discount amount. Parallel transactions re-check capacity under PostgreSQL locks and the one-to-one redemption constraint prevents duplicate usage.

The voucher HTTP tests check cross-tenant ID and code access, unauthorized management, invalid scope, code limits, stale approvals, decimal amount tampering, concurrent capacity and replay. QRIS verification uses mocked provider transport and signed callbacks. Production security acceptance remains open.

## Supplier purchasing boundaries

Purchase routes enforce current tenant membership and existing inventory permissions. Order lookups, receiving, stock adjustments, supplier purchase histories, and stock balance reads check the member's allowed inventory locations. Owner-only location grants select records from the same tenant. URL objects and form selections must pass the same scope checks. Supplier contacts are tenant-scoped and accessible with inventory.view. CSRF remains enabled on all mutations.

Receiving uses a single PostgreSQL transaction and an idempotency key scoped to a purchase. A digest rejects attempts to reuse the key for different quantities or delivery notes. Balance overflow and invalid quantities leave stock, purchase state and outbox records unchanged. Inventory and purchasing WebSocket consumers recheck location access before sending each event.

## Platform billing boundaries

Subscription checkout uses its own platform environment variables and endpoints. Tenant merchant connections, order transactions, vouchers and tenders never supply credentials or payment references for subscription billing. Tenant invoice and subscription routes require current ownership. POST requests use the existing Django CSRF protection. Authorized platform staff manage plan terms and audited restrictions in Django administration. Invoice and attempt administration is read-only.

The webhook verifies the Midtrans signature using constant-time comparison, then obtains authoritative payment status from the same platform account. The service matches the saved payment reference, currency, amount, and transaction identifier. Browser return parameters cannot activate a subscription. Replay and cross-tenant attempts are covered by transaction locks, authoritative object scoping and uniqueness constraints. Redirects must use the configured Midtrans hosted checkout origin.
