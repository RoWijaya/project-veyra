# Testing

## Identity cycle — 2026-11-17

`manage.py test core --keepdb` passed **87 tests in 107.970 seconds** against PostgreSQL and Redis after migration 0019. Eight identity tests cover RSA/Authlib validation, PKCE/state replay, invalid signature/claims, explicit linking, enrollment/TOTP replay, recovery reuse, privileged enrollment, CSRF and Redis stream gating. The final full run includes session hardening: logout closed an existing stream on its next delivery.

Actual Uvicorn/Chromium browser verification used a synthetic, nonprivileged account with an existing authenticator. Password sign-in reached MFA; invalid code showed an error; valid TOTP reached businesses. Account-security UI was inspected at desktop, 375px phone and 768px tablet. Expanded removal controls supported keyboard Tab; inputs measured 48px and phone content did not overflow horizontally. Local screenshot: `.scratch/account-security-browser.png`. The QA account was deactivated afterward. Live Google consent, physical authenticator scanning, screen-reader audit and production deployment remain unverified.

## Environment and commands

Use the project virtual environment, PostgreSQL 17 and Redis 7. Preserve development DB and named Docker volumes. Django creates/retains the separate `test_veyra` database with `--keepdb`.

```powershell
docker compose up -d postgres redis
.venv\Scripts\python.exe manage.py check
.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.venv\Scripts\python.exe manage.py test core --keepdb
.venv\Scripts\python.exe manage.py test core.test_account_recovery core.test_observability --keepdb
```

Use `test core`, not only `test core.tests`: new modules are discovered alongside the original suite. SQLite mode is not verification for PostgreSQL/Redis production behavior.

## Strategy and evidence

HTTP workflows are the agreed public seam for new regression coverage. Existing service-level tests remain. Channels communicators exercise ASGI/Redis authorization and event delivery. Mock only unavailable external boundaries or explicit fault injection; live readiness tests connect to actual PostgreSQL and Redis.

On 2026-11-17, the full discovered suite passed 79 tests in 131.267 seconds. Eight new tests cover resend/verification recovery, disabled-account replay, resend throttle/CSRF, real dependency readiness, simulated Redis failure, correlation headers and safe log formatting. A later focused run is required whenever these paths change. Earlier browser evidence on 2026-11-16 covers QR checkout/tracking and live repricing.

Browser verification on 2026-11-17 used Uvicorn and the in-app Chromium browser. The login link opened the resend form; submitting an unknown synthetic address reached uniform confirmation without creating an account. The browser did not verify SMTP delivery or the eligible-account email path; automated HTTP tests cover the latter using Django's test mail backend.

## Gaps

No full accessibility, load/concurrency, cross-browser, formal penetration, backup/restore or production deployment certification. Payments are mocked, physical printing is unverified, live Google consent and full factor-recovery acceptance are unverified, and full multi-device/domain synchronization remains incomplete. [FEATURE-STATUS.md](FEATURE-STATUS.md) tracks remaining product acceptance. A green suite cannot replace these checks.

## Figma implementation cycle — 2026-11-18

- Django system check and migration consistency passed. Additive migrations `0020_product_image` and `0021_guest_cart` are applied locally.
- `manage.py test core --keepdb` passed 104 tests in 56.616 seconds with PostgreSQL and Redis. HTTP tests cover session-isolated carts, checkout replay, QR links across locations, upload validation and guest revocation, banner boundaries, receipt snapshots and print job permissions and retry. Existing OIDC/MFA and authorized Channels regressions ran in the same suite.
- Chrome with Playwright used a synthetic business against local Uvicorn and the outbox worker. Checks covered Settings keyboard navigation and responsive disclosure, catalog filtering, image preview/upload, banner editing via WebSocket, separate cart navigation and reload, a second independent guest context, checkout and private tracking, POS draft preservation on a live order event, synthetic cash tender, receipt preview, the 58mm selector, print-media output and Order History.
- Chromium print-to-PDF produced page widths of 163.92pt and 227.04pt for 58mm and 80mm respectively. Physical print output, cross-browser assistive technology, and live payment provider processing remain unverified.

## Visual parity and guest takeaway — 2026-11-18

- The original 104-test baseline passed before changes. The final `manage.py test core --keepdb` run passed 110 tests in 74.217 seconds on PostgreSQL and Redis. Django system and migration consistency checks passed. Migration `0022_guest_takeaway` was applied locally.
- New HTTP coverage exercises bounded cart edits and stale revisions, category promotion pricing in storefront and POS, permission-scoped enablement, dine-in QR to takeaway, no-table pickup, replay, private tracking, recipe stock consumption, station completion, paid receipts, signed mocked QRIS settlement and duplicate notification handling.
- Chrome, Firefox and Playwright WebKit on Windows passed the synthetic workflow on local Uvicorn with the Redis outbox worker. Checks covered keyboard focus and Escape, pressed category buttons, 44px quantity controls, reduced-motion preference, no horizontal overflow at 320–1200px, checkout notes, the cashier's existing draft, payment and kitchen status over WebSockets, and receipt preview. Chrome also covered a deliberately disconnected guest tracking socket and measured both thermal print PDF widths.
- Assistive technology, physical hardware, external QRIS sandbox credentials and production verification remain open.

## Product modifiers — 2026-11-20

- The pre-change PostgreSQL and Redis baseline passed 110 tests. The full suite after the order and API changes passed 117 tests in 135.418 seconds. The modifier regressions use the HTTP seam for catalog permissions, location and product scope, required and repeated choices, cart edits, stale quotes, multiple POS configurations, paired station use and historical receipts.
- Chrome, Firefox and Playwright WebKit passed the synthetic workflow against local Uvicorn, PostgreSQL, Redis and the outbox worker. Chrome additionally verified live modifier repricing with active selections in the guest menu and POS, resetting a removed option without clearing the draft quantity, and produced customized receipt PDFs measuring 163.92 pt at 58 mm and 227.04 pt at 80 mm. The existing plain-product takeaway workflow also passed in Chrome, including checkout, a live tracking reconnect and payment through kitchen completion.
- Standard Django and migration consistency checks passed, and `0023_product_modifiers` is applied locally. Django's deployment check with debugging disabled reported `security.W021` for HSTS preload.
- Zero verified sandbox merchant connections and zero physical device print queues were available. The browser and local API evidence cover the exercised workflows. Assistive-technology, physical device and external provider acceptance remain open.

## Design freeze and vouchers — 2026-11-20

- The baseline was 117 tests against PostgreSQL and Redis. The final full suite passed 125 tests in 86.474 seconds, including eight voucher tests. Django checks and migration consistency passed. Migration 0024_vouchers is applied locally.
- The voucher suite exercises public cart, POS, manager, payment, report and receipt routes. It checks authorization, cross-tenant isolation, Redis code rate limits, monetary calculations, checkout replay and two actual PostgreSQL transactions competing for one redemption. A mocked Midtrans transport covers pending, failed, expired and settled amounts and duplicate callbacks. The paired station API test verifies acknowledged receipt reprints after a voucher edit.
- Browser QA uses a synthetic merchant with local Uvicorn, PostgreSQL, Redis and the outbox worker. The design polish check used keyboard input and desktop, tablet and phone viewports.
- The production-mode Django check with an ephemeral secret reported security.W021 for HSTS preload. Production security acceptance remains open.

Chrome, Firefox and Playwright WebKit passed the voucher flow at 320, 834 and 1200 px. Checks included creation, activation, invalid-code feedback, apply/remove, stepper changes without losing checkout instructions, live merchant changes in guest and POS drafts, guest counter payment over WebSockets, and both receipt preview widths. Chrome generated PDFs with measured widths of 163.92 pt and 227.04 pt. The local database had no verified sandbox merchant connections or active printers.

Chrome also passed the plain-price takeaway regression with keyboard navigation, live pickup tracking, tender, kitchen completion and receipt preview. The synthetic browser user, location, table and QA vouchers were deactivated after checks. Historical orders and audit records were retained.

## Supplier purchasing — 2026-11-20

- The final PostgreSQL 17 and Redis 7 suite passed 133 tests in 147.499 seconds. Eight purchasing tests cover supplier access and history, draft pricing and transitions, transactional receiving, cancellation, exact replay, over-receipt and overflow protection, concurrent deliveries and authorized Channels delivery. Django reported no issues or migration drift. Local PostgreSQL has both additive migrations applied.
- Chrome, Firefox and Playwright WebKit passed the supplier and purchase workflow through local Uvicorn, PostgreSQL, Redis and the outbox worker. Checks exercised supplier add/edit/deactivate and purchase history, multi-line draft creation and editing, submission, rejected over-receipts, partial and full receiving, and automatic stock, order-list and detail updates. The purchasing routes denied cross-tenant purchase IDs.
- Receiving fit 320, 375, 834 and 1200 px. Keyboard activation added and focused a draft item field. Chrome handset and tablet captures were reviewed against the existing [Figma Inventory reference](https://www.figma.com/design/6tzk8TqeeQMTAkWFZ45JP3?node-id=3-2186). Production and assistive-technology verification remain open.

Chrome also verified the owner-managed inventory location grant, the restricted stock list, denial of a direct purchase URL, and destination options limited to the granted location. Supplier list, purchase list, draft and purchase detail fit 320, 834 and 1200 px. Purchase tables scroll inside a labeled region on smaller screens.

## Soft Premium Veyra and platform billing — 2026-11-20

- The full suite passed **143 tests in 78.897 seconds** against PostgreSQL 17 and Redis 7. It includes the existing 133-test baseline and ten billing tests. Django system checks passed, no migration drift was detected, and migrations `0027_platform_billing` and `0028_bill_checkout_anchor` are applied to the local development database.
- Billing tests exercise owner and cross-tenant access checks, CSRF, Django admin plan publication and retirement, audited restriction and restoration, trial eligibility and expiry, original price and trial snapshots, renewal and grace, downgrade limits, cancellation, and recovery. The suite tests forged and replayed notifications, authoritative amount verification, a lost checkout response, PostgreSQL concurrent invoice creation, and owner-only ASGI delivery through Redis.
- Chrome, Firefox and Playwright WebKit verified the refined app shell, Overview, POS, Inventory, purchase details, Vouchers and Settings at 320, 834 and 1200 px. The checks covered keyboard focus and Escape, a reduced-motion preference, quantity changes and responsive layout. Chrome rechecked partial receiving, validation, live stock, plain-product takeaway, kitchen status and receipt PDF widths after the shared CSS changes.
- All three engines exercised new trial and expired-subscription conversion, plan comparison, invoice history, hosted checkout redirect, a failed payment and retry, live payment updates in two open tabs, upgrade and downgrade scheduling, and cancellation and resumption. Billing and invoices fit at 320, 834 and 1200 px. Chrome also exercised read-only billing and recovery guidance under a platform restriction at 320 px. Phone and desktop billing captures were reviewed. Full assistive-technology acceptance remains open.
- Checkout used real local ASGI, PostgreSQL, Redis and the outbox worker with a mocked Midtrans transport. No dedicated platform merchant credentials or published non-QA paid plans were available locally. Merchant sandbox acceptance, public HTTPS webhook delivery, live settlement and production worker deployment remain unverified. The production-mode Django check reported the HSTS preload setting warning (security.W021). Synthetic QA accounts and paid plans were deactivated and their history was retained.

## Promotion target selection — 2026-11-21

The full suite passed **146 tests** on PostgreSQL and Redis. The three new HTTP regressions cover name rendering, multiple saved IDs, removal and clearing, bound form recovery after an invalid percentage, existing inactive targets, invalid and cross-tenant IDs, catalog permissions, and CSRF. Django system checks passed and the migration check reported no drift.

Chrome, Firefox and Playwright WebKit passed the promotion picker workflow at 320, 834 and 1200 px against local ASGI, PostgreSQL and Redis. Checks covered case- and accent-insensitive search, a 165-product catalog, empty catalogs and results, arrow keys, Home, End, Enter, Space, Tab, Escape, click-away, chip removal, clear all, posted IDs, validation recovery and edit persistence. Chrome also verified the native named-select fallback with JavaScript disabled. Desktop and phone captures were reviewed against the field mockup and the existing Veyra form. Screen-reader device testing remains open.

## Product-wide UI polish — 2026-11-21

- PostgreSQL 17 and Redis 7 baseline passed 146 tests. The final `manage.py test core --keepdb` run passed **148 tests in 129.802 seconds**, including two HTTP regressions for expired and completed password resets. Django system checks passed and `makemigrations --check --dry-run` reported no changes.
- Chrome, Firefox, and Playwright WebKit checked **42 representative routes per engine** at 320, 834, and 1200 CSS px against local Uvicorn ASGI, PostgreSQL, and Redis. Checks verified expected HTTP responses, layout fit, and JavaScript page errors. They found no page-level horizontal overflow or uncaught page errors in the final matrix. Intended horizontal scrolling stays inside labeled tables. Selected Chrome captures from before and after the fixes were reviewed at phone and desktop widths, including landing, account, workspace, POS, inventory, purchases, campaign forms, and the storefront.
- Keyboard and pointer checks in all three engines exercised password visibility, registration error focus, expired reset recovery, pending submit feedback, MFA recovery, mobile navigation and order table focus, POS search and quantities, promotion and voucher target pickers, report filters, the receiving shortcut, Kitchen and Bar synchronization, billing navigation, and guest checkout through placing an order. Chrome also completed email verification and a password change from a valid reset link, then verified desktop GSAP enhancement and in-session reduced-motion cleanup. Narrow reduced-motion checks confirmed the landing video was not requested.
- At 375 px, Chrome dismissed a purchase cancellation and then received a partial-delivery update from another tab without reloading. A separate two-tab Chrome check dismissed the billing confirmation and then observed both cancellation and renewal resumption over Redis-backed WebSockets. At 1440 px Chrome scrubbed the paused, muted landing film forward and backward, then removed the active video source when reduced motion was enabled. The synthetic accounts and guest entry points were deactivated after verification and their history was retained.
- Checked the shared input boundary and hint colors against warm surfaces at 3.37:1 and 6.01:1 respectively. These checks cover selected states. A complete assistive-technology pass, browser testing on physical devices, and production Google, SMTP, payment provider, printer and deployment checks remain open.

## Catalog and ordering page — 2026-11-21

- The pre-change baseline passed **148 tests in 206.356 seconds** on PostgreSQL 17 and Redis 7. The full regression suite then passed **158 tests in 227.049 seconds**. Django system and migration consistency checks passed. Migrations 0029–0031 applied to the local PostgreSQL development database.
- Ten new HTTP tests cover categorized product creation, category search and ordering, reassignment and guest visibility, safe deletion, cross-business IDs, denied staff mutations and CSRF. Media cases exercise upload, replacement, removal, retention, invalid and oversized assets, guest image rendering and banner changes through a current signed QR link.
- Chrome, Firefox and Playwright WebKit exercised the catalog and ordering page at 320, 834 and 1200 CSS px against local ASGI, PostgreSQL, Redis and the outbox worker. Checks used the actual management forms for category changes, photo and banner previews, replacement, deletion and removal. The browser runs also checked a failed image load, a draft quantity in live POS, the cart photo changing without losing its instructions, isolated table and takeaway carts, and counter checkout. Table and takeaway menus received appearance changes without a reload.
- Reviewed selected 320 and 834 px Chrome captures. File previews were constrained to their form after the initial responsive run found overflow. After restarting the local ASGI process, new HTTP clients received the same WebP bytes from both media routes for all three browser fixtures. The database and Docker volumes stayed in place.
- Chrome confirmed a campaign banner could be previewed, activated, used to locate its target product, and deactivated without changing the general ordering page banner. The final 320 px check kept the currency symbol and amount together. After verification, six isolated QA accounts and their ordering links were deactivated. Order and audit history were retained.
- Production storage, backup and restore, and assistive-technology testing remain open.
