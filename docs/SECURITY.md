# Security architecture

This page describes implemented protections and remaining assurance work. [Access model](permissions.md) · [ASVS verification register](ASVS-VERIFICATION.md)

## Identity and sessions

- Account registration persists an inactive user and a verification delivery in the same transaction. SMTP and Resend run in a worker with a lease, bounded retries and terminal failure state. The public response remains uniform. Verification uses an expiring signed link plus a locked eligibility check. The rotated registration session can verify the chosen password. Other browsers must set a password through the CSRF-protected verification form. Replaying an old link cannot reactivate a previously verified disabled user. Resend uses uniform responses and rate limits. Password recovery uses Django tokens. Invitation access uses a hashed, expiring token bound to the business and intended identity.
- The Google flow uses Authlib for OIDC, discovery, state, nonce, S256 PKCE, and signed token validation. A verified Google email and unique provider subject are required. An existing local account requires explicit linking after sign-in. Provider tokens are not persisted.
- Password confirmation or recent Google authentication protects authenticator setup. TOTP secrets are encrypted with a dedicated Fernet key. Locked factor verification prevents reused counters, and recovery codes are hashed and consumed once. Enrolled accounts always need a factor. Outside debug, privileged owner and admin enrollment is required by default. Admin uses the shared sign-in. Password reset does not remove MFA.
- Django sessions are HttpOnly and SameSite Lax. Production enables Secure session and CSRF cookies, HTTPS redirection and positive HSTS. The session is browser-close scoped with a configured 12-hour age. Protected streams recheck current permissions, session, and factor proof before sending affected data. Passkeys, total-loss factor recovery, session inventory and Google unlinking are absent.

[Identity implementation](../core/identity.py) · [Dated OIDC and MFA decision](decisions/0003-google-and-authenticator.md)

## Tenant, guest and device access

The business slug is only a selector. The server resolves ownership or an active membership and checks its explicit permissions, subscription entitlements, and applicable location grants. Submitted IDs are looked up within that scope. The owner's Team location grant applies across location, stock, purchase, order, tender, receipt, table, station, report and builder-preview workflows. A restricted member sees only enumerated locations. Unlocated legacy records and unassigned stations are business-wide. Tenant-wide campaign, modifier and voucher editors require unrestricted location scope. Owners manage merchant configuration, publication and billing. Department and workspace assignments are context. Owners can disable memberships and pending invitations while retaining audit history.

Guests receive revocable signed table or pickup links. Each placed order relies on a separate signed HttpOnly tracking cookie. The order UUID does not grant access by itself. Guest tracking checks the signed order cookie at connection and before each snapshot. Admin and guest streams check current authorization every 30 seconds while idle and close with 4403 when access expires. Authenticated streams check the active Django session and MFA proof on delivery. Order event delivery resolves the current authorized order and location in PostgreSQL. Station pairing uses a short-lived single-use code. Subsequent API requests check the SHA-256 digest of the presented bearer token and the station's current capabilities, location, and revocation state. Browser session and paired-device authorization are separate. [Permission reference](permissions.md) · [Real-time authorization](REALTIME.md)

| Surface | Authorization boundary |
| --- | --- |
| Management orders, tenders, receipts and reports | Active membership and relevant action permission intersect with the owner's allowed locations. Views scope lists and details. Mutation services resolve the membership again before locking the order or stock record. |
| Locations, tables and stations | A table mutation checks the existing location and any destination. Pickup toggles check the active authorized location. Station screens scope the station and order. A restricted member cannot create business-wide locations or employee invitations. |
| Procurement and inventory | Supplier purchasing and stock records are location-filtered on read and mutation. Shared tenant catalog and supplier metadata retain their existing separate permissions. Location-targeted campaign configuration needs business-wide scope. |
| Browser WebSockets | Routes derive channel groups from the authenticated tenant or verified guest capability. Incoming messages cannot add subscriptions. Order events use a fresh authorized snapshot. Inventory and purchase events check location before forwarding. |
| Device and provider endpoints | Paired devices use current station, capability, token and location checks. Merchant callbacks derive the tenant from the original payment attempt, require signature verification and preserve authoritative provider reconciliation. |
| Owner and platform operations | Owners control credentials, published settings, refunds, station provisioning, subscription billing and offboarding. Django's privileged MFA check applies to the platform administration paths. |

## HTTP and data handling

| Risk | Implemented control |
| --- | --- |
| CSRF | Django's middleware and template tokens protect browser write forms. Provider callbacks and the bearer-token device API use distinct server-side authentication. |
| XSS and framing | Django templates autoescape by default. The CSP uses same-origin sources with a jsDelivr script exception for the landing dependency. The builder accepts constrained presentation values, uses a scoped style nonce in storefront responses, and allows private same-origin framing with form submission blocked in preview. Other pages restrict framing. Camera, microphone, and geolocation are disabled by the policy. |
| SQL injection and wrong IDs | ORM queries and bound parameters are used. Django forms and service validation check ranges, enums, and relationships. Custom SQL, including readiness queries, needs the same review standards when changed. |
| Uploads | Pillow decodes allowed still images, rejects invalid, animated, or oversized files, and writes WebP bytes. Business, preview and guest routes check access before serving bytes. The optional Supabase bucket is private and the server keeps its service-role key. |
| Replay and concurrent writes | Unique keys, row locks, and state checks protect selected order, tender, voucher, payment, stock, purchase, refund, and publication workflows. The financial and stock ledgers preserve earlier records. |
| Logging | Structured events include sanitized route patterns and request identifiers. Request bodies, cookie values, signed paths, credentials, and arbitrary exception text are excluded from the normal application log. Proxy access logs and console email need separate configuration. |

## Payment and recovery boundaries

Only the tenant owner manages encrypted merchant credentials. Platform checkout uses its own merchant. The two Midtrans routes verify separate signing credentials before status reconciliation. Tenant callback lookup binds the order, connection, provider, environment and business. A distinct signing key disables legacy Server Key verification for that connection. Adapter and callback code checks provider signatures, status and identity before accepting payment evidence. The outbound QRIS charge follows a committed attempt reservation. Tender and callback settlement use the same order lock. Replays use request digests and state guards, and unexpected late captures enter reconciliation. Cash recording and terminal-card confirmation are staff actions. Important changes generate audit events. [Payment lifecycle](PAYMENTS.md)

Back up PostgreSQL and the separate encryption and application signing keys through protected channels. Recovery uses an isolated target first and verifies tenant boundaries, stock, payment and media integrity. Redis data is transient. An optional private media archive can be rebuilt from PostgreSQL. [Recovery procedure](DEPLOYMENT.md#backup-and-restore)

## Open assurance

The test suite covers scoped HTTP routes, MFA and replay, signed guest and device access, CSRF, uploads, and payment invariants in local or simulated environments. The ASVS register has no formal acceptance. Hosted review still needs a trusted TLS proxy and log collector, secrets and recovery controls, live provider callback verification, abuse-control and capacity testing, and independent security review. [Test evidence](TESTING.md) · [Incident procedures](OPERATIONS.md)
