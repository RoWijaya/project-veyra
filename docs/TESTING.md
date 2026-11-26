# Testing and verification

## Automated tests

Run development services from [the quick start](../README.md#run-locally-on-windows). Use a local PostgreSQL role allowed to create test databases and a Redis instance isolated from staging/production. Django creates a separate ``test_<database-name>``, applies migrations to it, and normally removes it after the suite. Confirm the target environment before running tests.

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test --noinput
.\.venv\Scripts\python.exe manage.py test core.test_ordering_builder --noinput
.\.venv\Scripts\python.exe manage.py test core.test_audit_remediation --noinput
.\.venv\Scripts\python.exe manage.py check_realtime
```

Use ``--keepdb`` only when intentionally reusing an isolated test database. Test modules are discovered across core. Targeting ``core.tests`` alone omits the separate test modules. The optional SQLite test switch uses in-memory database and Channels implementations and is appropriate only for quick checks without PostgreSQL or Redis requirements.

Use public HTTP and ASGI workflows for regressions where practical. Service-level tests cover domain invariants. PostgreSQL is important for real transaction and constraint behavior. Redis tests cover group delivery and stream authorization. The suite simulates remote providers or controlled failure conditions. Its results do not establish live provider acceptance.

## Browser workflow

Use the Uvicorn server, local PostgreSQL and Redis, and an active outbox worker. Use a test tenant with a product, signed ordering entry point, and a browser-accessible owner account. Use separate browser contexts for owner and guest to avoid sharing cookies. Exercise Chrome, Firefox and Playwright WebKit when a change affects rendering, focus or browser behavior. The optional Ordering Page Builder browser regression and disposable fixture launcher are checked in. The app has no npm runtime step. [Builder browser commands](ORDERING-PAGE-BUILDER.md#reproduce-the-browser-regression)

1. For a responsive audit, use widths 320, 375, 430, 768, 834, 1024, 1200 and 1440 CSS pixels. Check page-level overflow, table regions, client errors and keyboard focus. Inspect meaningful empty, validation, and success states.
2. Open a signed guest menu in a separate context. Place an order and keep its tracking page open. Verify the independent Management queue and authorized guest update after transition or tender. Check reconnect and stock/purchase changes for workflows affected by the patch.
3. For the builder, confirm unsaved edits appear only in the private preview. Save a draft, reload the editor and verify the guest page stays on the published design. Publish as owner, then open the real public link in a fresh context with no editor session and check computed styles again after reload. Check the signed table and takeaway links, live delivery, unpublish and restore. The browser regression also adds a second self-only stylesheet policy to public navigation and verifies a second business keeps its separate design.
4. Distinguish local behavior, simulated transport, and actual hosted-provider acceptance. Record exact browser, width, route, action, result, and data or service limit. Check reduced motion and verify visible focus and meaningful status updates.

## Ordering Page Builder publication regression

The regression verifies the actual signed table and takeaway links in four stages. It checks a saved private draft while the public design stays unchanged, a fresh anonymous visit after Publish, reload and real-time updates on a previously opened guest page, and unpublish followed by a separate fresh visit after restoring and republishing version 1. A second tenant publishes a different palette and has its own private draft. A failed cross-tenant preview request and repeated reads of the second tenant's guest menu confirm isolation.

On 2026-11-26, 253 Django tests passed on PostgreSQL and Redis. The 33-case focused builder, cart and catalog run passed. Django's system and migration drift checks passed. The tests used isolated databases.

The HTTP test deliberately diverges the old public fields from the active version and verifies that the public menu, logo and cover still resolve to that version. It checks the stylesheet's version, MIME type and cache policy, verifies that outdated URLs cannot cache refreshed CSS under the old key, and verifies that unpublishing removes the custom assets. The Playwright workflow asserts actual computed surfaces under a second content security policy that allows same-origin stylesheets. It also checks guest HTML and cart responses through their normal routes. Chrome, Firefox and Playwright WebKit passed the full regression and fit checks at 320, 375, 768, 1024 and 1440 CSS pixels. The fixture records actual numbered snapshots and four computed colors for each guest flow. No production proxy, CDN or hosted media service was exercised. [HTTP coverage](../core/test_ordering_builder.py) · [Browser fixture and commands](ORDERING-PAGE-BUILDER.md#reproduce-the-browser-regression)

## Evidence

| Verification | Result |
| --- | --- |
| 2026-11-25 Ordering Page Builder functional audit | The PostgreSQL/Redis suite passed 231 tests before repair and 234 afterward. The 26-case focused suite covered builder, catalog and storage workflows. Chrome, Firefox and Playwright WebKit passed computed colors, media, responsive builder interactions, live publication, restoration and both signed checkout paths. Django checks and migration drift checks passed. [Builder evidence](ORDERING-PAGE-BUILDER.md#functional-audit-and-repair-2026-11-25) |
| 2026-11-25 High-priority remediation | The clean pre-change baseline passed 215 tests. The final Windows suite passed 231 tests in 241.296 seconds, including 16 new cases, with PostgreSQL and Redis. Django checks and migration drift checks passed on isolated databases. Migration 0038 applied to the fixtures. |
| Recovery and concurrency checks | A disposable Redis container was stopped and restarted between separate outbox worker processes. The failed event was retried and two guest sockets received the current order and payment state. Eight PostgreSQL tests raced staff completion with tender in an isolated fixture. A separate in-flight station revocation withheld the QR while preserving its attempt. |
| Targeted remediation browser checks | Chrome, Firefox and Playwright WebKit passed independent guest and staff checks for responsive QR menu and checkout, POS tender, the kitchen completion gate, live handoff, guest reconnect, receipt, and report. Viewport checks sampled 320 to 1440 CSS pixels with no console errors in the final runs. Chrome also checked live stock after partial receiving and generated 58/80 mm receipt PDFs. A separate Chrome check completed cross-browser signup verification and password validation at 320 CSS pixels using local console mail. |
| Rebuilt Linux image | Python 3.12.16, Django 6.1.3, cryptography 50.0.2 and pip 26.2.1. Package consistency, Django checks, real Channels group delivery, and 22 targeted checkout, refund and account tests passed. OSV matched zero among 39 PyPI and GSAP queries for the final image inventory and zero among 78 for the Windows environment. The packages scan did not assess the Linux OS. |
| 2026-11-25 documentation review | 215 tests passed in 280.438 seconds against PostgreSQL 17.12 and Redis 7.4.12, using Python 3.14.7 and Django 6.1.3. The separately named Django test database was created for the run and removed by Django. |
| Setup and runtime checks | Clean editable installs with the development extra on Windows and in the Linux release image passed. Django checks, migration drift and plan, local PostgreSQL/Redis, and Channels group delivery passed. An isolated test database exercised migration, admin creation and all three worker commands. The production-mode local image served manifest CSS, readiness and HSTS. Django reported the HSTS preload advisory (`security.W021`). |
| Current public ASGI smoke | Chrome, Firefox, and Playwright WebKit each checked the landing, sign-in, and registration pages at 375 CSS pixels. All three rendered the expected heading with no page overflow or uncaught page errors. The HTTP smoke also checked liveness, readiness and development static CSS. |
| 2026-11-25 UI polish handoff | 215 tests before and after. Chrome, Firefox, and Playwright WebKit passed 912 settled checks across 38 routes and eight widths. Recorded browser workflows included publishing, live orders, payment, refund and kitchen state. This is dated browser evidence for the UI polish pass. |
| Browser and accessibility acceptance | Targeted keyboard, text contrast, touch targets, and reduced-motion checks exist. Full screen-reader sessions, physical devices, printers, hosted providers, and independent assurance remain open. |

[UI handoff](UI-DESIGN-HANDOFF.md) · [Release evidence history](RELEASE-HISTORY.md) · [Feature inventory](FEATURE-STATUS.md)

## Priority 0 implementation 2026-11-26

Baseline on `update` at `698ea190a7c799821d5c46bd0502893d5dd2194d` passed 234 tests in 177.853 seconds before code edits. The first launch reached database setup before Docker was available. The clean rerun used PostgreSQL 17.12 and Redis 7.4.12. The same services, with verified Windows loopback bindings, ran the final suite against a disposable database. Existing regression coverage was retained. The format-check test now asserts the intentionally corrected eligibility behavior.

| Area | Verified locally |
| --- | --- |
| Final Django suite | **252 passed in 212.609 seconds**, with zero failures or skips, using PostgreSQL 17.12, Redis 7.4.12 and Django 6.1.3. |
| Authorization | HTTP branch and tenant scope, POS submitted IDs, role and workspace restrictions, tender, station screen, cross-location event delivery and owner offboarding. Idle staff and guest WebSockets close after revocation or expiry. [Regressions](../core/test_priority_location_access.py) |
| Midtrans and merchant gating | Tenant and platform callbacks use separate signing credentials. Tests cover missing, invalid and mismatched signatures, an unknown reference, duplicate delivery and stale payment transitions. Read-only verification checks tenant merchant, environment, QRIS and signature evidence. Core API Write and hosted callback approval are separate. BCA stays gated. [Signature regressions](../core/test_priority_payment_signatures.py) · [Readiness regressions](../core/test_priority_merchant_readiness.py) |
| Account recovery | Provider timeout, durable retries, an interrupted sending lease, permanent rejection, uniform signup and resend responses, duplicate prevention, expiry and one-time activation. Resend API requests are simulated at the transport boundary. [Mail regressions](../core/test_priority_account_mail.py) |
| Historical sales | PostgreSQL tests cover recategorization, rename, deletion, SKU preservation, date filtering and the legacy unknown bucket. Paid totals remain intact. [Report regressions](../core/test_priority_category_history.py) |
| Browser | Installed Chrome, Firefox and Playwright WebKit passed signup, resend, merchant configuration and responsive width checks at 320, 390, 768 and 1440 CSS pixels. All three engines passed the Ordering Page Builder workflow with live publication, media, restore and signed table and pickup checkout. [Priority workflows](../tests/browser/priority-workflows.cjs) · [Builder workflow](../tests/browser/ordering-builder.cjs) |
| Disposable Linux staging | PostgreSQL 17.12, Redis 7.4.12, Python 3.12.16 and the non-debug ASGI image. The migration service and four workers started under supervision. Dependency and operational checks passed. A stopped outbox heartbeat expired and returned after restart. A queued event committed while it was stopped was published after restart. Test load exposed false health failures at eight seconds. With an 18-second timeout all services stayed healthy during subsequent 51-second observation. The forwarded HTTPS, HSTS, secure session and CSRF cookies, Host, Origin and hashed static responses were exercised. `check --deploy` reported W021 for HSTS preload only. |
| Recovery | Disposable snapshot and restore passed in 11.42 seconds including synthetic seeding. Restored order total and normalized private media digest matched. Owner media access worked and a different tenant was denied. One payment bundle and two MFA secrets decrypted with retained keys. Removing the old payment key caused the expected check failure. Hosted storage replication and RPO/RTO require external acceptance. [Fixture](../tests/recovery/staging_fixture.py) · [Procedure](DEPLOYMENT.md#backup-and-restore) |
| Release image | Linux amd64 candidate `sha256:cf9015529eef47e2d7a850e72b2f892e0f579088ed19df515f97618b52de1ae9`. Package integrity passed. OSV matched zero of 39 PyPI packages and 20 of 61 Debian source packages. Applicability and available OS patches remain release review items. [OSV query API](https://google.github.io/osv.dev/post-v1-querybatch/) |

Migrations `0039_merchant_readiness_and_verification_mail` and `0040_historical_order_line_classification` applied in disposable PostgreSQL and passed migration drift checks. The first retains the timestamps of prior format checks while clearing legacy QRIS eligibility. The second keeps genuine unknowns in historical lines without inventing categories.

Provider sandbox and live settlement, hosted TLS/WSS and alert delivery, approved SMTP/Resend delivery, production backup retention, hosted Supabase and physical printer/device acceptance remain separate. The candidate image and recovery tests used synthetic data. The normal development database was neither migrated nor reset during this work.

### Files changed in Priority 0

Compared with the intake working tree on `update`, which had 156 existing changed or untracked paths. The migration records and the following paths are the only changes made in this pass. No files were staged or committed.

**Modified (49)**

- `.env.example`
- `README.md`
- `compose.yaml`
- `config/settings.py`
- `core/billing_gateway.py`
- `core/catalog_views.py`
- `core/consumers.py`
- `core/device_views.py`
- `core/email_backend.py`
- `core/forms.py`
- `core/identity.py`
- `core/management/commands/process_billing.py`
- `core/management/commands/process_outbox.py`
- `core/management/commands/process_refunds.py`
- `core/media_views.py`
- `core/models.py`
- `core/modifier_views.py`
- `core/ordering_views.py`
- `core/payments/midtrans.py`
- `core/permissions.py`
- `core/pricing.py`
- `core/purchasing.py`
- `core/purchasing_views.py`
- `core/receipt_views.py`
- `core/receipts.py`
- `core/services.py`
- `core/templatetags/navigation.py`
- `core/tests.py`
- `core/views.py`
- `core/voucher_views.py`
- `docs/AUDIT-REMEDIATION.md`
- `docs/DEPLOYMENT.md`
- `docs/DEVELOPMENT.md`
- `docs/ENVIRONMENT.md`
- `docs/FEATURE-IMPLEMENTATION-REGISTER.md`
- `docs/FEATURE-STATUS.md`
- `docs/OPERATIONS.md`
- `docs/PAYMENTS.md`
- `docs/PRODUCT-FEATURE-AUDIT.md`
- `docs/PRODUCT-ROADMAP.md`
- `docs/SECURITY.md`
- `docs/TESTING.md`
- `templates/core/catalog.html`
- `templates/core/inventory_access.html`
- `templates/core/locations.html`
- `templates/core/payments.html`
- `templates/core/registered.html`
- `templates/core/reports.html`
- `templates/core/team.html`

**Added (18)**

- `compose.staging.yaml`
- `core/account_mail.py`
- `core/management/commands/approve_tenant_qris.py`
- `core/management/commands/check_operational_queues.py`
- `core/management/commands/check_recovery_keys.py`
- `core/management/commands/check_staging_web.py`
- `core/management/commands/check_worker.py`
- `core/management/commands/process_account_mail.py`
- `core/migrations/0039_merchant_readiness_and_verification_mail.py`
- `core/migrations/0040_historical_order_line_classification.py`
- `core/test_priority_account_mail.py`
- `core/test_priority_category_history.py`
- `core/test_priority_location_access.py`
- `core/test_priority_merchant_readiness.py`
- `core/test_priority_payment_signatures.py`
- `core/workers.py`
- `tests/browser/priority-workflows.cjs`
- `tests/recovery/staging_fixture.py`


## Stage 1 release candidate

The final run at baseline 5dec2ca with the uncommitted Stage 1 changes passed **271 tests in 170.115 seconds**, with zero failures and zero skips. The run used PostgreSQL 17.12, Redis 7.4.12, Python 3.14.7 and Django 6.1.3 on an isolated test database. External socket connections were fenced and provider boundaries were controlled in tests. Django system checks, migration consistency, JavaScript syntax and the applicable staging dependency checks passed.

| Verification | Observed result |
| --- | --- |
| Authorization | PostgreSQL and real Channels/Redis tests exercised branch filtering, permission and membership revocation, reconnect denial, independent users and order/stock stream isolation. A stale inventory event displayed the current authorized balance. |
| Payment | Wrong and missing keys, explicit legacy gating, replaced tenant credentials, revoked historical keys, prior platform attempts versus post-cutover attempts, unbound merchant verification, replay and independent readiness passed. The voucher regression uses a distinct notification secret and still exercises failure, expiry and settlement. Provider traffic remains unverified. |
| Account and reports | Signup retry, one-time and superseded links, queued reset, identical public destinations, refunds, and sale-time category snapshots through guest checkout and POS passed. |
| Browser | Installed Chrome, Firefox and Playwright WebKit each completed signup, resend, password reset request, merchant configuration, and POS and dashboard fit checks at 320, 390, 768 and 1440 CSS pixels. Each passed the published ordering menu workflow, including anonymous table and takeaway reloads, live updates, media, isolation, unpublish/restore and counter checkout at 320, 375, 768, 1024 and 1440 CSS pixels. |
| Disposable staging | Linux amd64 image sha256:70312e21be1b6c8f31bdab384d7268bdd81e5167a4c139b28dee76f57ee55a1c with Python 3.12.16 ran without published dependency ports or external network access. Migrations 0041 through 0043 applied. A separate upgrade probe from 0040 preserved an existing queued verification generation and required a generation claim for new jobs. ASGI readiness and Redis group delivery passed. A trusted peer set the ASGI scheme and received HSTS and a secure cookie. An unapproved peer's forwarded header did not bypass HTTPS and a hostile Host was rejected. Production-mode WebSocket delivery reached a scoped guest and rejected a foreign origin. One of two outbox replicas lost progress during a stall while the peer and web stayed healthy. The crashed replica restarted with a new generation. Redis interruption was detected and group delivery recovered after restart. Package consistency passed. Django deployment checks reported the HSTS preload advisory W021. |
| Encrypted restoration | GnuPG authenticated decryption produced identical PostgreSQL archive bytes. The isolated restore preserved the 6,000 IDR order total, private media SHA-256 and tenant denial. Recovered one current payment bundle, one historical signing bundle, one queued verification redirect, one queued password recovery recipient and two MFA secrets. The original Django signing marker verified under the retained key. Omitting the retained payment or Django key produced the expected failure. |
| Hosted limits | This round did not exercise hosted TLS/WSS, issued provider credentials or real transactions, delivered alerts, inbox delivery, hosted archival or production RPO/RTO. The release register records exact acceptance evidence required. |

[Stage 1 changes and open gates](STAGE-1-RELEASE-READINESS.md) · [Browser fixture](../tests/browser/serve_ordering_builder.py) · [Hosted and restore procedures](DEPLOYMENT.md)
