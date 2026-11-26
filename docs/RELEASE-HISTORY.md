# Release evidence history

These are dated implementation notes and QA results. Older results describe the checkout and environment at that checkpoint. See [feature status](FEATURE-STATUS.md), [testing](TESTING.md), and [deployment](DEPLOYMENT.md) for current guidance.

[Product milestones](#product-milestones) · [Test and browser milestones](#test-and-browser-milestones)

## Product milestones

### Initial verification on 2026-11-16

- PostgreSQL 17 and Redis 7 local services were healthy.
- Applied migrations through `core.0018_subscription_foundation` to the local development database. Existing tenants were assigned grandfathered access and an append-only migration event; new business creation starts a 14-day trial.
- `manage.py check` passed; `makemigrations --check --dry-run` reported no changes. `check --deploy` with temporary production-mode settings had only the optional HSTS preload recommendation; production SMTP, domain, TLS proxy and secret values were unavailable.
- `manage.py test core.tests --keepdb` passed all 71 tests using PostgreSQL; the suite includes Redis-backed guest order/payment, menu update, scheduled-boundary and inventory WebSocket tests, plus SaaS trial/admin HTTP workflows. The final fail-closed/no-subscription and append-only-history changes also passed focused PostgreSQL HTTP tests.
- Browser smoke test ran the app through Daphne (`config.asgi:application`): a guest submitted a server-priced table order, saw a private tracking page, and observed the order status change live after the owner advanced it from the POS. A second browser check confirmed live menu repricing after a merchant saved a promotion. Browser QA accounts/catalog/table/promotion were deactivated afterward; append-only QA audit history remains in the local PostgreSQL database.
- QRIS HTTP creation is covered with a mocked provider API response and checks verified-connection gating, pending transaction idempotency and protected QR rendering. No live Midtrans/BCA credentials or production merchant account were available.
- Promotion HTTP workflow and repricing of an already-open guest menu were exercised in the browser. `process_outbox` must run continuously for scheduled boundary events and durable event delivery.

### Limits recorded on 2026-11-16

- Passing tests and the browser smoke test do not constitute full feature acceptance, ASVS certification, penetration testing or production readiness.
- Real payment initiation, signed production webhook delivery, refunds/reconciliation and physical printer output require provider accounts/secrets and hardware that were not available.
- Production monitoring/alerting, transactional email delivery, media storage, backup/restore and deployment infrastructure have not been verified.
- At this checkpoint subscription administration recorded plan and status only. See the later platform subscription billing entry for the current implementation.
- Reported revenue currently means orders explicitly marked paid. Card tender is a staff confirmation after terminal acceptance and is not provider-integrated; refunds, staff/station analytics, detailed inventory valuation and report exports are not implemented; per-location report grants are not modeled.
- A test-only `daphne` dependency was added to the development extra to run the real ASGI WebSocket test. The production container continues to serve `config.asgi:application` with Uvicorn.

### Implementation cycle verified on 2026-11-17

- Restored the existing Docker dependency containers without resetting PostgreSQL/Redis data. Saved a source-only recovery archive outside the repository before edits; Git still has no HEAD commit.
- Added verification resend with CSRF, hashed IP/address throttles, uniform confirmation and audit events. Verification consumes locked unverified/inactive state; an old link cannot reactivate a previously verified disabled account. SMTP retry/delivery monitoring remains missing.
- Added safe JSON/request correlation, actual PostgreSQL/Redis readiness, safe outbox failure visibility, and disabled raw URL access logs in the production container command. Proxy logging still requires deployment configuration.
- `manage.py test core --keepdb` passed 79 tests in 131.267s. New modules must be included by discovery. The final run includes row-lock/logging hardening and safe local-return/open-redirect coverage.
- Real browser: login resend link, form submission for a synthetic unknown address, and uniform confirmation passed through Uvicorn. Eligible delivery is covered by test mail backend; production SMTP is unverified.
- Added PRD, canonical architecture/security, integrations, testing, operations, development, threat model, ASVS register and ADRs. Corrected stale recipe/subscription database documentation.

### Google OIDC/MFA and interface cycle — 2026-11-17

- Applied additive migration 0019 for GoogleIdentity and Authenticator. Existing tenant/order/subscription data was preserved.
- Authlib code flow validates state, nonce, PKCE and RSA-signed tokens. Existing email matches require explicit authenticated linking. Tests mock Google HTTP; live credentials/consent remain unavailable.
- MFA integrates encrypted setup/QR, locked TOTP counters, single-use recovery codes, challenge, privileged production enrollment and HTTP/ASGI session enforcement. Local dedicated encryption key was configured without displaying it.
- Full PostgreSQL/Redis suite: 87 tests passed in 107.970s. Final focused stream test also verified partial-login rejection and logout revocation on the next data delivery.
- Actual browser verified MFA failure/success, account security, phone/tablet reflow, keyboard focus and 48px inputs. This is not full accessibility acceptance or pixel-exact reproduction of the generated design target.
- Synthetic browser QA account was deactivated; audit history remains. Passkeys, assisted total-loss recovery, session inventory and Google disconnection remain missing. No production-verification claim is made.

### Figma implementation cycle — 2026-11-18

The existing [Veyra Figma file](https://www.figma.com/design/6tzk8TqeeQMTAkWFZ45JP3) contains a design system and 50 editable workflow frames across six pages. The approved management, customer ordering, POS/receipt and campaign designs informed the Django implementation.

| Workflow | Implemented and automated-test verified | Browser evidence | Remaining |
|---|---|---|---|
| Management shell and dashboard | Settings near the top, permission-filtered destinations, larger typography, operational counts and recent orders, catalog search and pagination, date filter errors in context. | Chrome verified Settings navigation, keyboard activation, the compact sidebar and catalog search. Checked 1200, 834 and 390px layouts. | Complete screen-reader and cross-browser review, richer analytics and operational alerts. |
| QR Menu and Your Order | Separate pages, session-isolated carts, quantity changes, removal, order-level notes, signed server-priced checkout and retry to the same private order. Existing signed table QR and QRIS tracking paths remain. | Chrome verified add, navigate, edit, reload, isolation across two browser contexts, counter checkout and denial of tracking to the other context. Checked the cart at 1180, 768 and 390px. | Product descriptions, vouchers and item-level notes. Live QRIS provider processing remains unverified. |
| Product images and menu banners | Tenant-scoped upload, image validation and WebP conversion, product replacement/removal, banner targets and schedule, active storefront placement, and server-side outbox boundary events. | Chrome verified photo selection preview, save and display, banner save and live banner edit in an open guest menu. | Product image presentation in POS, richer banner preview, formal visual acceptance. |
| POS, orders and receipt printing | Paginated order history, paid order receipt links, historical line pricing and tender amounts, thermal 58/80mm styles, browser print and device queue/retry/reprint with status and history. | Chrome verified the incoming order via WebSocket without losing the existing draft, recording synthetic cash tender, opening receipt preview and history, switching thermal width and measuring Chromium-generated print PDFs at 58mm and 80mm. | Physical printer output, location-level human access grants, receipt branding/footer options, and tax/refund/payment integrations. |
| Operations and inventory | Browser station queue uses authorized WebSocket events to reconcile the visible queue. Read-only staff cannot use mutation controls. | Automated station authorization tests passed. Device and station screen acceptance remains open. | Purchasing and supplier work, fuller location and workspace-scoped operational flows. |

**Verification** — 104 Django tests passed against local PostgreSQL and Redis. Django checks and migration consistency passed. Additive migrations 0020 and 0021 were applied. Production services, real QRIS settlement and printer hardware remain unverified.

**Initial visual comparison** — Compared management, mobile menu, cart and receipt against the Figma frames. Django retains the account header and live connection feedback. The application uses uploaded images and live merchant data, a native category select, an editable quantity field with Update, and the selected thermal width. Those differences are visible in the browser captures.

### Visual parity and guest takeaway — 2026-11-18

| Workflow | Implementation and automated evidence | Browser evidence and remaining verification |
|---|---|---|
| Menu, cart and POS controls | The storefront uses category buttons linked to real catalog filters. Menu, cart and POS share the stepper style. The cart's server-validated quantity actions preserve checkout input and update totals. HTTP tests cover stale revisions, minimum quantity and category promotion pricing. | Approved Figma frames reviewed. Browser checks covered keyboard selection, focus, touch targets, totals, checkout notes and the POS draft. Real product data and the additional pickup context remain visible. |
| Authenticated workspaces | The espresso workspace navigation now includes the brand, business identity, location where selected and an account disclosure. Settings stays near the top. Existing permission-filtered navigation and CSRF-protected sign-out remain. | Keyboard checks exercised the account disclosure, focus return and compact business menu across desktop, tablet and phone. Full screen-reader and visual acceptance remain open. |
| Guest takeaway | Managers can enable and share a signed location menu. Eligible table guests can choose takeaway from their existing cart. Both use the shared order service, payment options, recipe consumption and private tracking. HTTP tests cover both entrances, replay, scoped management, revocation, station completion, receipt and inventory movements. | Chrome, Firefox and Playwright WebKit exercised live POS, counter tender, kitchen completion and receipt at 320–1200px. The kitchen queue now keeps existing forms in place when a payment-only event or unchanged reconnect snapshot arrives. Chrome verified a fresh server snapshot after tracking reconnected. |
| QRIS and printing | A mocked Midtrans test covers takeaway QRIS generation, private QR access, blocked preparation while pending, signed settlement and duplicate notifications. Existing payment and print-queue tests passed. | Chromium measured 163.92 pt and 227.04 pt for 58 mm and 80 mm receipt PDFs. Local environment had zero verified sandbox merchant connections and zero device print queues. Hardware, external settlement and production evidence remain open. |

Migration `0022_guest_takeaway` was applied to the local PostgreSQL database. Django checks and migration consistency passed. The full PostgreSQL and Redis run passed 110 tests. The current Git branch still has no commit baseline.

The next business feature at this checkpoint was product modifiers. See the current product customization evidence below. Vouchers, suppliers and purchasing, subscription billing, live QRIS reconciliation, physical printing and a full accessibility review remain open.

### Product modifiers — 2026-11-20

| Workflow | Implementation | Verification |
|---|---|---|
| Catalog management | Catalog managers can create and edit reusable groups, assign products and locations, set minimum and maximum selections, and configure ordering, availability and option surcharges. | HTTP tests cover manager authorization and foreign product validation. Browser runs exercised the editor and catalog entry point. |
| Guest and POS | Single- and multiple-choice groups share one location-aware pricing and validation service. Guests can edit selections in the cart. Both flows accept several configurations of the same product. | The browser workflow passed in Chrome, Firefox and Playwright WebKit on Windows. It covered keyboard selection, cart editing, multiple POS configurations, draft preservation, kitchen handoff, receipts and a 320–1200 px storefront layout. Chrome also observed a merchant price change update an already-open menu and POS draft over Redis and Channels, and verified that resetting a removed option preserves its draft quantity. |
| Historical orders | Order lines preserve option names, group names and unit surcharges. Kitchen queues, station payloads, prep print jobs and customer receipts use those snapshots. | HTTP tests cover stale quotes, location scope, selection limits and receipt history after an option edit. Chromium printed customized receipts to PDF at both 58 mm and 80 mm. |

Migration `0023_product_modifiers` is applied locally. The full PostgreSQL and Redis suite passed 117 tests. Chrome also passed the plain-product takeaway regression. Production verification, an assistive-technology audit, live QRIS settlement and physical printing remain open. The local environment has zero verified sandbox merchant connections and zero device print queues.

Voucher, supplier purchasing, stock receiving and subscription billing work followed this checkpoint.

### Design freeze and vouchers — 2026-11-20

| Area | Implemented | Verification |
|---|---|---|
| UI polish | Reviewed approved Figma frames for the authenticated header, category controls and quantity steppers. Improved context visibility, menu focus return, category wrapping and stepper sizing. The current visual direction is frozen in the PRD. | Chrome exercised header navigation, checkout, focus, quantity limits, live tracking and printing at desktop, tablet and phone widths. A full screen-reader audit remains open. |
| Voucher management | Promotions → Vouchers covers searchable listings, discounts, status, validity, eligibility, usage, remaining capacity and recent changes. Catalog managers control creation, activation and edits. | HTTP tests cover authorized edits, duplicate codes, business-local dates, read-only and cross-tenant denials, and foreign product selection. |
| Pricing and checkout | Your Order and POS use the same eligibility and Decimal arithmetic. Checkout revalidates the confirmed price and commits one redemption with the order. Cashier-selected saved customers support per-customer limits. | HTTP tests cover modified draft and stale quotes, price tampering, stacking, modifier surcharges, category/location/order type, minimum/cap/rounding, replay and a two-transaction race for the last use. |
| Payment and records | Signed payment callbacks and cash tender use the reduced order total. Historical order data feeds tracking, receipts and reprints. Reports group redemptions and actual paid sales by voucher and location. | Automated coverage includes cash, mocked QRIS failure/expiry/settlement and duplicate callbacks, receipt snapshots, authorized device print and reprint commands, and report arithmetic. Production provider settlement and physical printing remain unverified. |

Chrome, Firefox and Playwright WebKit passed voucher management, guest/POS checkout, live invalidation, cash payment and receipt previews at 320, 834 and 1200 px. Chromium PDFs measured 163.92 pt at 58 mm and 227.04 pt at 80 mm. The development database has zero verified sandbox merchant connections and zero active printers.

Migration `0024_vouchers` was applied locally. The full PostgreSQL and Redis suite passed 125 tests. The application uses one committed use per created order, including unpaid, failed, expired or cancelled orders. Refund and partial-refund execution remain unimplemented, and a later refund workflow must preserve that original redemption. Anonymous first-order and per-person limits are unavailable.

Supplier purchasing followed this checkpoint. See the current implementation and verification below.

### Supplier purchasing — 2026-11-20

| Workflow | Implemented | Local verification |
|---|---|---|
| Suppliers | Tenant-owned contacts, status, search, filtering, edit, and paginated purchase history. Inactive suppliers stay on existing purchases. | HTTP tests and Chrome, Firefox, and WebKit exercised creation, edits, deactivation, and authorization. |
| Purchase orders | Location-scoped multi-item drafts, server-priced order review, submission, cancellation, snapshots, expected delivery and filters by status, supplier, item, location, date and reference. | HTTP tests verify draft edits, lifecycle checks, server totals, history and cross-tenant and location denials. Three browsers exercised the main draft and order workflow. |
| Delivery and stock | Partial and complete receipts link accepted quantities and historical unit cost to the existing stock ledger. Rejected and damaged units remain outstanding. PostgreSQL locks and receipt keys protect against replay and over-receipt. | HTTP tests cover repeated and concurrent receiving, exact-once stock changes, stock overflow rollback, and preserved history. Browser tests verify invalid delivery feedback, two deliveries, live balances and receipt history. |
| Permissions and live updates | Existing inventory permissions, owner-managed location limits, and scope-checked purchase and inventory WebSockets. Purchase list, detail and supplier history fetch authorized snapshots when events arrive. | PostgreSQL and Redis tests cover authorized delivery and denial for an unrelated location. Live list, detail and stock updates passed in three browsers without manual reload. |
| Interface | Supplier, purchase and receiving pages extend the frozen inventory UI and existing controls. | Receiving passed at 320, 375, 834, and 1200 px in Chrome, Firefox, and WebKit. Reviewed Chrome captures at phone and tablet widths against the approved Inventory frame. |

Additive migrations 0025_suppliers and 0026_purchase_orders are applied locally. The final PostgreSQL and Redis suite passed 133 tests, including eight purchasing tests. The stock cost field shows the latest accepted purchase cost. Accounting valuation, purchase returns, and count-based reconciliation are separate unfinished work. Production deployment and assistive-technology acceptance remain open.

### Soft Premium Veyra and platform billing — 2026-11-20

| Area | Implemented | Local verification |
|---|---|---|
| Soft Premium Veyra | Refined the shared 6/10/16/20 px radius scale, warm surfaces, navigation states, operational tables and storefront components. Nested purchasing content uses separators and spacing. Focus styling and reduced-motion behavior are shared. | Reviewed the approved Figma workspace reference and app screenshots. Chrome, Firefox and WebKit exercised representative workspaces and controls at phone, tablet and desktop widths. Chrome rechecked receiving, live stock, takeaway and receipt PDFs. |
| Owner billing | Subscription and usage overview, plan comparison, scheduling, cancellation and resume, invoices, checkout and recovery. Restricted owners can read plans and billing history with recovery guidance. | Three browsers verified the trial and expired flows at 320, 834 and 1200 px, including an invoice redirect, failure and retry, paid history and live updates. Chrome verified restricted billing and invoice access at phone width. |
| Lifecycle and entitlements | Default 14-day starter trial, configurable eligibility for new businesses, saved trial and paid terms, locked monthly renewal, seven-day grace, safe downgrade limits and end-of-period cancellation. Expiry and reconciliation run independently of page visits. | PostgreSQL HTTP tests cover trial and expiry boundaries, admin plan and subscription controls, renewal, recovery, historical terms, and concurrent invoice requests. |
| Platform checkout and audit | Dedicated Midtrans platform boundary, verified signed notifications, authoritative status lookups, retry-safe attempts, immutable invoice snapshots, admin audit history and Redis owner updates after commit. | Automated tests cover cross-tenant denial, CSRF, webhook integrity and replay. The local three-browser flow used a mocked provider through Uvicorn and the Redis outbox worker. |

The full PostgreSQL and Redis suite passed 143 tests, including ten billing tests and the previous 133-test baseline. Both additive billing migrations are applied locally. Production verification requires published paid plans, a dedicated platform merchant account, a public HTTPS notification endpoint and supervised billing and outbox workers. The current scope is monthly owner-initiated payment. Automatic debit, annual billing, proration, immediate cancellation, tax invoices and billing reminder email are outside the implemented scope.

### Promotion target selection — 2026-11-21

Promotions & pricing uses searchable Products and Categories pickers with named options, removable selections, counts, and clear-selection controls. Existing selections return on edit and after validation errors. Previously linked inactive products can be retained or removed when editing that promotion. Django retains the tenant-scoped ID validation and CSRF checks.

Chrome, Firefox and WebKit verified selection and saving at phone, tablet and desktop widths. The full PostgreSQL and Redis suite passed 146 tests.

### Product-wide UI polish — 2026-11-21

- Marketing gives the POS and business operations purpose in the hero, shows a browser capture from an example workspace, and keeps the restaurant tour with motion preferences. The footer and primary links use actual destinations.
- Account screens share a focused card for sign-in, registration, verification, password reset and MFA. Expired links offer recovery and completed resets lead to sign-in. Password visibility and related error messages are keyboard-accessible. The explicit favicon keeps browser icon requests on the static path during MFA.
- Workspace and customer screens share stronger field borders, spacing, accessible status colors and control geometry. The management dashboard uses two columns on wider screens and a single column on smaller screens. Recent orders link to order history.
- Promotions and vouchers share a reusable searchable picker. Order history and sales reporting support labeled table scrolling. Paid revenue comparisons use real daily report values and keep the amounts visible as text.
- Dismissing a purchase or billing cancellation confirmation keeps its live connection open. Chrome verified that another tab’s partial delivery, subscription cancellation, and renewal resumption arrived without a reload.

#### Audit coverage and files

| Area | Change | Browser coverage |
| --- | --- | --- |
| Landing | Product-focused hero, capture of the actual POS running with sample catalog data, real product and account destinations, reduced-motion cinematic tour | Route matrix, selected screenshots, and interaction checks |
| Account | One sign-in and recovery treatment, meaningful expired-link actions, linked validation errors, password visibility, and explicit favicon during MFA | Route matrix, selected screenshots, and interaction checks |
| Shell, dashboard, and control system | Shared ivory and espresso surfaces, a 6/10/16/20 px radius scale, clearer control boundaries, keyboard focus, selected navigation, mobile menus, and delayed form status | Route matrix, selected screenshots, and interaction checks |
| Orders, POS, customer ordering, reports, and campaign forms | More compact order history with local table scroll, real paid-sales comparisons, stronger checkout control styles, and the same searchable target picker in both promotions and vouchers | Route matrix, selected screenshots, and interaction checks |
| Catalog, settings, customers, table management, team, billing, inventory, suppliers, purchasing, and operational stations | Reviewed routes kept their existing workflows and inherit the shared shell and control changes | Route matrix and selected captures. Interactive checks covered representative controls and workflows as recorded in TESTING.md. |

Source changes are in `templates/core/` (layout, account, landing, dashboard, reports, pagination, order history, and campaign templates), `core/static/core/` (site and app styles, shared interaction scripts, picker, favicon, and POS preview), and `core/forms.py` and `core/views.py` (presentation data). Regression coverage lives in `core/test_account_recovery.py` and `core/tests.py`. The PRD, cinematic workflow, and testing documentation record design and verification decisions.

Provider and physical-device acceptance and a complete screen-reader audit remain open. Route coverage records rendering and responsive checks. The testing log identifies the separate, narrower interaction coverage.

### Catalog and ordering page — 2026-11-21

| Area | Implemented |
| --- | --- |
| Category management | Product catalog → Categories supports search, details, display order, visibility and deletion. Assignment validates against the active business during creation and editing. Hiding a category removes the guest filter while its active products remain orderable under All. Deletion is blocked while products, promotions, vouchers or campaign banners refer to the category. |
| Product details and images | Product creation and editing support guest descriptions and image upload, preview, retention, replacement and removal. Catalog, POS, table menu, takeaway menu and cart use the same saved image. A failed image load leaves the product text and ordering controls usable. |
| Ordering page | Settings → Ordering page gives the owner control of the guest display name, introduction and general banner. Guests see saved appearance on their signed table and pickup links. Owners can preview an existing signed menu and change or remove the banner independently of campaign banners. Campaigns keep their own schedule, location eligibility and CTA. |
| Live propagation | Product, category and appearance changes queue an outbox event in the same transaction. Redis and Channels deliver changes to open menus. Authorized POS sessions also receive catalog events and preserve eligible quantities and selections. A cart can refresh a changed photo without replacing checkout inputs. |
| Storage and isolation | Merchant images live as normalized WebP bytes in PostgreSQL. The existing Docker PostgreSQL volume persists the data with the business and catalog. Uploads are decoded as JPEG, PNG or WebP, limited to 5 MB and 16 million pixels, restricted to a single frame and re-encoded with metadata removed. Media responses have a fixed MIME type and filename, `nosniff` and private no-store headers. Management uses server-side tenant membership and mutation permissions. Guest assets and visibility are resolved from their current signed link. |

Migrations 0029–0031 add category details and visibility, product descriptions, and an optional one-to-one ordering page. Existing categories stay visible and products keep their assignments and images. The default guest display name still comes from the business. A custom logo, color palette and layout editor are outside this scope. Deployment-specific PostgreSQL storage, backup and restore remain production acceptance items.

Main changes are in [catalog views](../core/catalog_views.py), [media views](../core/media_views.py), [image forms](../core/media_forms.py), [storefront rendering](../core/customer_views.py), the catalog and ordering page templates, and the existing storefront and POS scripts. See [Testing](TESTING.md) for HTTP and browser evidence.

### Landing and account redesign — 2026-11-22

| Area | Implemented |
| --- | --- |
| Landing | An editorial masthead pairs the product message with the working POS. Product and Operations sections lead into the cinematic tour with actual Kitchen Display and inventory captures. Navigation uses real section and account destinations. The desktop timeline remains scroll-controlled. Compact, reduced-motion and Data Saver modes keep the poster and readable chapters. |
| Account entry and outcomes | Sign-in, registration, resend, reset, verification, MFA challenge and invitations share account navigation, form controls and contextual states. A missing invitation returns a 404 with a recovery action. Valid invitation destinations survive the sign-in and registration transition. |
| Account security | Authenticator setup separates scan or manual entry from code confirmation. Recovery codes appear once when setup is confirmed. The QR endpoint now passes the account name as a keyword to PyOTP and returns an SVG only within the authenticated setup window. |

This work adds no schema changes. The active checkout retains the separate promotion migration 0032 and catalog migrations 0029–0031. See [Testing](TESTING.md) for local workflow evidence and external acceptance limits.

**Browser-tested locally.** Chrome, Firefox and Playwright WebKit completed registration and verification, password reset, invitation acceptance, authenticator enrollment and recovery sign-in. The landing route and 11 account routes fit all five test widths. Chrome also exercised forward and backward desktop tour scrubbing and the return to the static layout when reduced motion changes. Selected phone, tablet and desktop captures were visually reviewed.

**Catalog rechecked.** All three engines used the actual catalog and ordering forms, received live updates in open guest and POS windows, and completed counter checkout from a signed table menu. PostgreSQL/Redis tests covered tenant permissions, CSRF and media validation. The release remains unverified against live Google, external email, production database backups and physical devices.

### Shared component cleanup — 2026-11-24

| Area | Implemented |
| --- | --- |
| Forms and validation | Shared management fields render visible labels, help, errors and grouped native choices. Hidden purchase formset controls stay outside the visual grid. Schedule fields use bound Django date and time widgets with a stable native format. Invalid responses return focus to the visible error summary or first invalid control. |
| Selections | Campaign banners reuse the searchable picker for single targets and multiple locations. Modifier targets use the same multi-select. The real scoped select submits the saved identifiers, including in the native fallback. |
| Team and stations | Permission and capability editors now expose actual checkboxes. Saved grants display plain-language labels. Product and printer choice fields display record names. Station actions wrap below the station summary and retain their button treatments. |
| Media and payments | Shared file fields show previews and announce selection feedback. Payment settings replace the visible credential bundle with masked provider-specific inputs. Merchant credentials still use the existing owner-scoped encrypted storage. |
| Data and recovery | Voucher, purchasing, order and invoice tables use shared status and numeric treatments. Reports display payment method names. Branded templates provide recovery for permission, CSRF, not-found and server error states. |

**Reviewed.** Public landing and account routes, management navigation, settings, catalog and media, promotions and vouchers, station and POS workspaces, guest menu and cart, inventory and purchasing, orders, reports, billing and payments.

**Browser-tested locally.** Chrome, Firefox and Playwright WebKit checked 45 routes at six widths. Separate interaction runs exercised permission choices, empty picker results, save and reopen, uploads, payment errors, native fallback and receipt layout. Selected before-and-after captures were visually inspected.

**Remaining verification.** Physical devices, full screen-reader testing, printer hardware, production provider connections and production deployment. The local route matrix and interaction checks are recorded in [Testing](TESTING.md).

### Pre-deployment release evidence — 2026-11-24

The work stayed on `update` in the active checkout. The release includes additive migrations 0033–0036. The existing repository contains no Git remote. Hosted resources, real providers and printing hardware were outside local verification.

| Area | Implemented and evidence | Severity of remaining work |
|---|---|---|
| Product and category management | Tenant-scoped catalog, categories, product availability and modifiers. Existing HTTP and cross-browser catalog workflows remain in the verification record. | P2 for bulk catalog import/export and stocked variants if required by the pilot |
| Product images and media | Django normalizes uploads and enforces scoped access. The outbox copies current normalized assets to a private Supabase bucket when configured. Product UUID and logo archive delivery use controlled transport tests. PostgreSQL recovery preserved uploaded media bytes. | P1 for actual Supabase credentials, private bucket and staging upload/recovery check |
| Restaurant ordering customization | Owner-only independent logo, banner, copy and preview. Guest asset routes use existing signed storefront authorization. Browser test exercises updating the open QR menu without a refresh. | P3 for additional branding controls |
| Promotion scheduling | Calendar limits, weekdays and recurring times apply independently. Permanent mode retains existing calendar dates for editing. POS and QR menu pricing use the same server logic. | P2 for expanded promotion analytics |
| Landing page | Existing approved cinematic design, GSAP/ScrollTrigger and reduced-motion handling are covered by the previous browser pass. | P2 for staging-device performance, visual acceptance and assistive-technology checks |
| Authentication | Account verification, reset, explicit Google linking, TOTP and recovery codes have local regressions. Resend transport has a local boundary test. | P1 for hosted Google consent, email receipt and account recovery rehearsal |
| Shared UI | The previous 45-route browser matrix covers shared components. This release adds count, supplier return, refund and logo controls to existing layouts and fixes cancelled live fetch handling. | P2 for full assistive-technology review |
| Subscription billing | Monthly invoices, renewal, plan changes, grace, cancellation and entitlements use the separate platform merchant. Existing automated billing coverage uses controlled provider responses. | P1 for real sandbox billing settlement and renewal |
| Refunds and partial refunds | Owners record cash paid back and terminal-confirmed card refunds. Midtrans uses linked capture, local reservation, stable provider key and verified bank confirmation. Confirmation updates receipt and guest tracking. | P1 for real Midtrans and BCA refund acceptance, and explicit capture selection for multiple settlements |
| Stock counts | Location-scoped expected and counted amounts, reason, manager reconciliation, history and optional variance movement. Transaction tests cover competing submissions. | P2 for offline and bulk counting if pilot requires them |
| Supplier returns | Returned line quantities link to accepted purchase stock and the original cost. Records represent completed hand-back and preserve receipt history. | P2 for return approval, supplier credit and accounting if pilot requires them |
| Inventory costing | Current quantity × latest accepted unit cost is shown as an operational estimate. Receipt and return history retain their own original costs. | P2 for accounting valuation and historical costing methods |
| Payment integrity | Authoritative Midtrans status, amount/reference matching, cancellation before unpaid closure, original-capture preservation, webhook replay, concurrent limits and audit events. | P1 for credential-to-merchant authorization and production reconciliation across providers |
| Live QRIS | Midtrans and BCA paths have local controlled charge and callback coverage. The real browser workflow uses counter payment. | P1 for real merchant credentials, sandbox charge, actual settlement, cancellation, failure and refund evidence |
| Printing | Browser receipt, thermal preview, device queue and acknowledgement have local verification. | P1 if pilot needs physical printing. Requires ESC/POS device client, 58/80 mm hardware and fault acceptance |
| Security | Defensive review checked permission and payment boundaries. Redis test keeps a socket open through revocation and verifies closure. No independent assurance or production proxy assessment is recorded. | P1 for hosted authorization and operational security acceptance |
| Real-time reliability | Local PostgreSQL outbox, Redis Channels group probe and browser streams exercised menu, orders, refunds and stock. | P1 for hosted Valkey, supervised workers, WSS reconnect, backlog and recovery |
| Database and media recovery | Isolated PostgreSQL 17 dump/restore matched 53 migrations, one count, one movement, Rp 5,000 refunded and the logo SHA-256. The private bucket can be rebuilt from normalized database bytes. | P1 for hosted backup retention, key recovery and restore evidence |
| Production settings | The Python 3.12 release container started on the configured port and passed readiness, static serving, forwarded-HTTPS, HSTS and Channels checks. The only deployment warning was HSTS preload, deferred for the final hostname. | P1 for staging connection, proxy headers, actual TLS and worker deployment |
| Documentation | [PRD](PRD.md), [architecture](ARCHITECTURE.md), [testing](TESTING.md), [security](SECURITY.md), [database](DATABASE.md), [integrations](INTEGRATIONS.md) and [runbook](DEVELOPMENT-AND-DEPLOYMENT.md) document the current results and limits. | P3 for recording subsequent hosted evidence |

P1 items gate production. A staging deployment needs an authorized GitHub remote, Render web and supervised workers, private Supabase and Aiven services, a verified email sender, and authorized merchant credentials. No hosted environment was changed.

The local release suite passed **206 tests** with PostgreSQL 17 and Redis 7. Chrome, Firefox and WebKit completed the live ordering, tender, refund, count and supplier-return flows and responsive checks at six widths. [Testing evidence](TESTING.md)

#### Standards review

The release review found a UUID media queue serialization bug, a refund replay that could suppress later bank confirmation, a refund-first callback that could leave capture pending, and a production setting that accepted disabled HSTS. The implementation now stringifies asset IDs, re-verifies repeated refund notifications, records authoritative captures discovered through refunds and requires a positive production HSTS duration. A shared Midtrans identity check is used by notifications, cancellation and reconciliation.

#### Specification review

The review also found stale order-stream authorization, pending QRIS cancellation without provider evidence, discarded promotion dates and a destructive example restore command. Those paths were corrected. Merchant authorization, BCA and multiple-capture refunds, accounting-grade inventory valuation, actual physical printing and hosted recovery remain open in the table above. The private media archive can be rebuilt from restored PostgreSQL assets.

### Figma-led UI refinement on 2026-11-24

- **Improved and browser-tested** — The landing and account layouts, overview and workspace navigation, POS order access, guest menu, promotion controls and responsive stock-count layout. The 32-route matrix covered 864 combinations across Chrome, Firefox and WebKit. Interactive checks covered sign-in and registration, catalog edits, live pricing and ordering, voucher and cash checkout, refunds, stock counts, receiving and supplier returns.
- **Reviewed** — Billing, reports, locations, team, tables, stations and devices, and account security were included in the responsive matrix. This evidence covers route rendering and shared UI behavior. Screen-reader, physical device and hosted acceptance remain open.
- The existing Figma file contains 18 editable desktop and phone frames and shared component states. All 206 automated tests passed before and after on PostgreSQL and Redis. [Design and verification handoff](UI-DESIGN-HANDOFF.md)


### Ordering Page Builder on 2026-11-24

- **Implemented.** Branding, cover, theme, layout, catalog presentation, and intentionally public restaurant information. The same guest renderer powers the private responsive preview and live menu. The owner publishes, unpublishes, and restores designs. Draft editors use an explicit business permission.
- **Locally verified.** PostgreSQL and Redis baseline and final suites passed 206 and 215 tests. Chrome, Firefox, and Playwright WebKit passed 105 combined responsive checks and the publish, QR checkout, unpublish, and restore workflow. The local additive migration preserved the baseline business, catalog, order, and storefront data.
- **Production verification pending.** Hosted media delivery, live QRIS acceptance, and physical-device and screen-reader testing remain open. [Implementation and test evidence](ORDERING-PAGE-BUILDER.md)

### Product UI polish on 2026-11-25

- **UI improved and browser-tested.** Landing page and account hierarchy, workspace navigation and overview, POS ordering controls, guest category navigation, responsive builder preview, physical count summary, sales reporting and billing layout. Shared spacing and control styling apply throughout management.
- **UI reviewed across the browser matrix.** Catalog, categories, modifiers, promotions, vouchers, banners, suppliers, purchasing, stations, settings, locations, team, order history, refunds, payment settings, and other app routes received responsive checks. Workflow-level verification was recorded separately from layout checks.
- **Verification.** The 215-test baseline and final runs used PostgreSQL and Redis. The 38-route matrix passed 912 checks in Chrome, Firefox, and Playwright WebKit. Live browser flows included guest QR checkout, payment, refund, kitchen order progression, and owner publishing. The existing Figma file’s priority frames and shared controls were updated. [UI design and verification handoff](UI-DESIGN-HANDOFF.md)
- **Still needs acceptance.** Full screen-reader sessions, physical devices, printing, hosted providers, and visual coverage of uncommon permission and error states.

## Test and browser milestones

### Identity cycle — 2026-11-17

`manage.py test core --keepdb` passed **87 tests in 107.970 seconds** against PostgreSQL and Redis after migration 0019. Eight identity tests cover RSA/Authlib validation, PKCE/state replay, invalid signature/claims, explicit linking, enrollment/TOTP replay, recovery reuse, privileged enrollment, CSRF and Redis stream gating. The final full run includes session hardening: logout closed an existing stream on its next delivery.

Actual Uvicorn/Chromium browser verification used a synthetic, nonprivileged account with an existing authenticator. Password sign-in reached MFA; invalid code showed an error; valid TOTP reached businesses. Account-security UI was inspected at desktop, 375px phone and 768px tablet. Expanded removal controls supported keyboard Tab; inputs measured 48px and phone content did not overflow horizontally. The account security screen was captured during the original QA session. The QA account was deactivated afterward. Live Google consent, physical authenticator scanning, screen-reader audit and production deployment remain unverified.

### Figma implementation cycle — 2026-11-18

- Django system check and migration consistency passed. Additive migrations `0020_product_image` and `0021_guest_cart` are applied locally.
- `manage.py test core --keepdb` passed 104 tests in 56.616 seconds with PostgreSQL and Redis. HTTP tests cover session-isolated carts, checkout replay, QR links across locations, upload validation and guest revocation, banner boundaries, receipt snapshots and print job permissions and retry. Existing OIDC/MFA and authorized Channels regressions ran in the same suite.
- Chrome with Playwright used a synthetic business against local Uvicorn and the outbox worker. Checks covered Settings keyboard navigation and responsive disclosure, catalog filtering, image preview/upload, banner editing via WebSocket, separate cart navigation and reload, a second independent guest context, checkout and private tracking, POS draft preservation on a live order event, synthetic cash tender, receipt preview, the 58mm selector, print-media output and Order History.
- Chromium print-to-PDF produced page widths of 163.92pt and 227.04pt for 58mm and 80mm respectively. Physical print output, cross-browser assistive technology, and live payment provider processing remain unverified.

### Visual parity and guest takeaway — 2026-11-18

- The original 104-test baseline passed before changes. The final `manage.py test core --keepdb` run passed 110 tests in 74.217 seconds on PostgreSQL and Redis. Django system and migration consistency checks passed. Migration `0022_guest_takeaway` was applied locally.
- New HTTP coverage exercises bounded cart edits and stale revisions, category promotion pricing in storefront and POS, permission-scoped enablement, dine-in QR to takeaway, no-table pickup, replay, private tracking, recipe stock consumption, station completion, paid receipts, signed mocked QRIS settlement and duplicate notification handling.
- Chrome, Firefox and Playwright WebKit on Windows passed the synthetic workflow on local Uvicorn with the Redis outbox worker. Checks covered keyboard focus and Escape, pressed category buttons, 44px quantity controls, reduced-motion preference, no horizontal overflow at 320–1200px, checkout notes, the cashier's existing draft, payment and kitchen status over WebSockets, and receipt preview. Chrome also covered a deliberately disconnected guest tracking socket and measured both thermal print PDF widths.
- Assistive technology, physical hardware, external QRIS sandbox credentials and production verification remain open.

### Product modifiers — 2026-11-20

- The pre-change PostgreSQL and Redis baseline passed 110 tests. The full suite after the order and API changes passed 117 tests in 135.418 seconds. The modifier regressions use the HTTP seam for catalog permissions, location and product scope, required and repeated choices, cart edits, stale quotes, multiple POS configurations, paired station use and historical receipts.
- Chrome, Firefox and Playwright WebKit passed the synthetic workflow against local Uvicorn, PostgreSQL, Redis and the outbox worker. Chrome additionally verified live modifier repricing with active selections in the guest menu and POS, resetting a removed option without clearing the draft quantity, and produced customized receipt PDFs measuring 163.92 pt at 58 mm and 227.04 pt at 80 mm. The existing plain-product takeaway workflow also passed in Chrome, including checkout, a live tracking reconnect and payment through kitchen completion.
- Standard Django and migration consistency checks passed, and `0023_product_modifiers` is applied locally. Django's deployment check with debugging disabled reported `security.W021` for HSTS preload.
- Zero verified sandbox merchant connections and zero physical device print queues were available. The browser and local API evidence cover the exercised workflows. Assistive-technology, physical device and external provider acceptance remain open.

### Design freeze and vouchers — 2026-11-20

- The baseline was 117 tests against PostgreSQL and Redis. The final full suite passed 125 tests in 86.474 seconds, including eight voucher tests. Django checks and migration consistency passed. Migration 0024_vouchers is applied locally.
- The voucher suite exercises public cart, POS, manager, payment, report and receipt routes. It checks authorization, cross-tenant isolation, Redis code rate limits, monetary calculations, checkout replay and two actual PostgreSQL transactions competing for one redemption. A mocked Midtrans transport covers pending, failed, expired and settled amounts and duplicate callbacks. The paired station API test verifies acknowledged receipt reprints after a voucher edit.
- Browser QA uses a synthetic merchant with local Uvicorn, PostgreSQL, Redis and the outbox worker. The design polish check used keyboard input and desktop, tablet and phone viewports.
- The production-mode Django check with an ephemeral secret reported security.W021 for HSTS preload. Production security acceptance remains open.

Chrome, Firefox and Playwright WebKit passed the voucher flow at 320, 834 and 1200 px. Checks included creation, activation, invalid-code feedback, apply/remove, stepper changes without losing checkout instructions, live merchant changes in guest and POS drafts, guest counter payment over WebSockets, and both receipt preview widths. Chrome generated PDFs with measured widths of 163.92 pt and 227.04 pt. The local database had no verified sandbox merchant connections or active printers.

Chrome also passed the plain-price takeaway regression with keyboard navigation, live pickup tracking, tender, kitchen completion and receipt preview. The synthetic browser user, location, table and QA vouchers were deactivated after checks. Historical orders and audit records were retained.

### Supplier purchasing — 2026-11-20

- The final PostgreSQL 17 and Redis 7 suite passed 133 tests in 147.499 seconds. Eight purchasing tests cover supplier access and history, draft pricing and transitions, transactional receiving, cancellation, exact replay, over-receipt and overflow protection, concurrent deliveries and authorized Channels delivery. Django reported no issues or migration drift. Local PostgreSQL has both additive migrations applied.
- Chrome, Firefox and Playwright WebKit passed the supplier and purchase workflow through local Uvicorn, PostgreSQL, Redis and the outbox worker. Checks exercised supplier add/edit/deactivate and purchase history, multi-line draft creation and editing, submission, rejected over-receipts, partial and full receiving, and automatic stock, order-list and detail updates. The purchasing routes denied cross-tenant purchase IDs.
- Receiving fit 320, 375, 834 and 1200 px. Keyboard activation added and focused a draft item field. Chrome handset and tablet captures were reviewed against the existing [Figma Inventory reference](https://www.figma.com/design/6tzk8TqeeQMTAkWFZ45JP3?node-id=3-2186). Production and assistive-technology verification remain open.

Chrome also verified the owner-managed inventory location grant, the restricted stock list, denial of a direct purchase URL, and destination options limited to the granted location. Supplier list, purchase list, draft and purchase detail fit 320, 834 and 1200 px. Purchase tables scroll inside a labeled region on smaller screens.

### Soft Premium Veyra and platform billing — 2026-11-20

- The full suite passed **143 tests in 78.897 seconds** against PostgreSQL 17 and Redis 7. It includes the existing 133-test baseline and ten billing tests. Django system checks passed, no migration drift was detected, and migrations `0027_platform_billing` and `0028_bill_checkout_anchor` are applied to the local development database.
- Billing tests exercise owner and cross-tenant access checks, CSRF, Django admin plan publication and retirement, audited restriction and restoration, trial eligibility and expiry, original price and trial snapshots, renewal and grace, downgrade limits, cancellation, and recovery. The suite tests forged and replayed notifications, authoritative amount verification, a lost checkout response, PostgreSQL concurrent invoice creation, and owner-only ASGI delivery through Redis.
- Chrome, Firefox and Playwright WebKit verified the refined app shell, Overview, POS, Inventory, purchase details, Vouchers and Settings at 320, 834 and 1200 px. The checks covered keyboard focus and Escape, a reduced-motion preference, quantity changes and responsive layout. Chrome rechecked partial receiving, validation, live stock, plain-product takeaway, kitchen status and receipt PDF widths after the shared CSS changes.
- All three engines exercised new trial and expired-subscription conversion, plan comparison, invoice history, hosted checkout redirect, a failed payment and retry, live payment updates in two open tabs, upgrade and downgrade scheduling, and cancellation and resumption. Billing and invoices fit at 320, 834 and 1200 px. Chrome also exercised read-only billing and recovery guidance under a platform restriction at 320 px. Phone and desktop billing captures were reviewed. Full assistive-technology acceptance remains open.
- Checkout used real local ASGI, PostgreSQL, Redis and the outbox worker with a mocked Midtrans transport. No dedicated platform merchant credentials or published non-QA paid plans were available locally. Merchant sandbox acceptance, public HTTPS webhook delivery, live settlement and production worker deployment remain unverified. The production-mode Django check reported the HSTS preload setting warning (security.W021). Synthetic QA accounts and paid plans were deactivated and their history was retained.

### Promotion target selection — 2026-11-21

The full suite passed **146 tests** on PostgreSQL and Redis. The three new HTTP regressions cover name rendering, multiple saved IDs, removal and clearing, bound form recovery after an invalid percentage, existing inactive targets, invalid and cross-tenant IDs, catalog permissions, and CSRF. Django system checks passed and the migration check reported no drift.

Chrome, Firefox and Playwright WebKit passed the promotion picker workflow at 320, 834 and 1200 px against local ASGI, PostgreSQL and Redis. Checks covered case- and accent-insensitive search, a 165-product catalog, empty catalogs and results, arrow keys, Home, End, Enter, Space, Tab, Escape, click-away, chip removal, clear all, posted IDs, validation recovery and edit persistence. Chrome also verified the native named-select fallback with JavaScript disabled. Desktop and phone captures were reviewed against the field mockup and the existing Veyra form. Screen-reader device testing remains open.

### Product-wide UI polish — 2026-11-21

- PostgreSQL 17 and Redis 7 baseline passed 146 tests. The final `manage.py test core --keepdb` run passed **148 tests in 129.802 seconds**, including two HTTP regressions for expired and completed password resets. Django system checks passed and `makemigrations --check --dry-run` reported no changes.
- Chrome, Firefox, and Playwright WebKit checked **42 representative routes per engine** at 320, 834, and 1200 CSS px against local Uvicorn ASGI, PostgreSQL, and Redis. Checks verified expected HTTP responses, layout fit, and JavaScript page errors. They found no page-level horizontal overflow or uncaught page errors in the final matrix. Intended horizontal scrolling stays inside labeled tables. Selected Chrome captures from before and after the fixes were reviewed at phone and desktop widths, including landing, account, workspace, POS, inventory, purchases, campaign forms, and the storefront.
- Keyboard and pointer checks in all three engines exercised password visibility, registration error focus, expired reset recovery, pending submit feedback, MFA recovery, mobile navigation and order table focus, POS search and quantities, promotion and voucher target pickers, report filters, the receiving shortcut, Kitchen and Bar synchronization, billing navigation, and guest checkout through placing an order. Chrome also completed email verification and a password change from a valid reset link, then verified desktop GSAP enhancement and in-session reduced-motion cleanup. Narrow reduced-motion checks confirmed the landing video was not requested.
- At 375 px, Chrome dismissed a purchase cancellation and then received a partial-delivery update from another tab without reloading. A separate two-tab Chrome check dismissed the billing confirmation and then observed both cancellation and renewal resumption over Redis-backed WebSockets. At 1440 px Chrome scrubbed the paused, muted landing film forward and backward, then removed the active video source when reduced motion was enabled. The synthetic accounts and guest entry points were deactivated after verification and their history was retained.
- Checked the shared input boundary and hint colors against warm surfaces at 3.37:1 and 6.01:1 respectively. These checks cover selected states. A complete assistive-technology pass, browser testing on physical devices, and production Google, SMTP, payment provider, printer and deployment checks remain open.

### Catalog and ordering page — 2026-11-21

- The pre-change baseline passed **148 tests in 206.356 seconds** on PostgreSQL 17 and Redis 7. The full regression suite then passed **158 tests in 227.049 seconds**. Django system and migration consistency checks passed. Migrations 0029–0031 applied to the local PostgreSQL development database.
- Ten new HTTP tests cover categorized product creation, category search and ordering, reassignment and guest visibility, safe deletion, cross-business IDs, denied staff mutations and CSRF. Media cases exercise upload, replacement, removal, retention, invalid and oversized assets, guest image rendering and banner changes through a current signed QR link.
- Chrome, Firefox and Playwright WebKit exercised the catalog and ordering page at 320, 834 and 1200 CSS px against local ASGI, PostgreSQL, Redis and the outbox worker. Checks used the actual management forms for category changes, photo and banner previews, replacement, deletion and removal. The browser runs also checked a failed image load, a draft quantity in live POS, the cart photo changing without losing its instructions, isolated table and takeaway carts, and counter checkout. Table and takeaway menus received appearance changes without a reload.
- Reviewed selected 320 and 834 px Chrome captures. File previews were constrained to their form after the initial responsive run found overflow. After restarting the local ASGI process, new HTTP clients received the same WebP bytes from both media routes for all three browser fixtures. The database and Docker volumes stayed in place.
- Chrome confirmed a campaign banner could be previewed, activated, used to locate its target product, and deactivated without changing the general ordering page banner. The final 320 px check kept the currency symbol and amount together. After verification, six isolated QA accounts and their ordering links were deactivated. Order and audit history were retained.
- Production storage, backup and restore, and assistive-technology testing remain open.

### Landing, account, and catalog regression — 2026-11-22

- The pre-redesign baseline passed 158 tests on PostgreSQL and Redis. The current local ASGI stack passed readiness with PostgreSQL 17 and Redis 7. The final `manage.py test core --keepdb --noinput` run passed **187 tests in 213.878 seconds**. The current count includes separate promotion tests. Added HTTP checks for landing destinations, a missing invitation with no-store, and the private authenticator SVG. The SVG test reproduced the PyOTP exception before the fix, and the final run confirmed the valid SVG and the anonymous, pre-setup and other-session access checks. The security suite also covers tenant scoping, CSRF, signed ordering links, Google OIDC validation with a simulated provider, and one-time MFA recovery.
- Chrome, Firefox and Playwright WebKit checked the landing page and 11 account routes at 320, 375, 768, 834 and 1200 CSS px. Each completed registration validation with focus to the error summary, valid local verification, password reset, invitation acceptance after sign-in, authenticator enrollment, invalid MFA input and recovery-code sign-in. The browser decoded the actual private setup QR. Synthetic local tokens exercised verification and reset routes.
- All three browsers opened landing navigation with the keyboard, restored focus after Escape, followed section and registration links, decoded the POS image and verified the narrow reduced-motion fallback made no film request. At 1440 px Chrome scrubbed forward and backward and confirmed that switching to reduced motion removed the film source. Reviewed the actual Chrome landing, registration, invitation, account security, authenticator and tour screenshots. MFA captures mask secrets.
- All three browsers created, searched and reassigned categories, previewed, replaced and removed photos and banners, exercised the missing-image fallback, kept POS selections and guest checkout inputs through Redis-backed live updates, and placed a table order using counter payment. Signed table and pickup sessions kept separate carts. The catalog interaction pass covered 320, 834 and 1200 px and included selected Chrome screenshots. A follow-up pass checked category management, ordering settings, POS, signed table and pickup menus, and MFA challenge layout at 375 and 768 px in each engine.
- After restarting local ASGI, new HTTP clients fetched byte-identical WebP product photos and ordering banners for all three browser fixtures. The test left both Docker volumes in place. Synthetic accounts and guest entry points were then deactivated with their records retained.
- Live Google consent, external email delivery, production storage and disaster recovery, physical devices and a full screen-reader audit require separate verification.

### Shared component cleanup — 2026-11-24

- The pre-change baseline passed **187 tests** on PostgreSQL and Redis. The reviewed version passed **190 tests in 223.713 seconds**. Three HTTP regressions check actual checkbox values and named choices, branded forbidden and missing-page responses, and the owner-scoped payment form with encrypted, masked credentials. Django and migration consistency checks, JavaScript syntax checks and `git diff --check` passed.
- Against local Uvicorn ASGI, PostgreSQL and Redis, Chrome, Firefox and Playwright WebKit checked **45 routes per engine** at **320, 375, 768, 834, 1200 and 1440 CSS px**. The matrix covered landing, sign-in and account states, dashboard and workspace, settings and catalog, POS, orders and receipt, Kitchen and Bar, team, stations, table QR and guest cart, suppliers and purchasing, promotions and vouchers, reports, billing and payment accounts. The final matrix reported no page-level horizontal overflow or uncaught JavaScript errors. Wide data tables scroll inside their labeled regions.
- In each engine, separate browser tests operated permission and station checkboxes, searched single and multiple selections, checked empty results and focus return, selected and cleared a target, previewed an accepted image after a rejected file, saved and reopened a campaign banner, retained payment input after validation, and updated a paid order's receipt to 58 mm. The checks used the real submitted field values and verified that printer labels and campaign choices worked with JavaScript disabled.
- Reviewed Chrome captures before and after the changes, including station action wrapping and contrast, mobile permission choices, modifier targets, file selection and BCA validation. Saving a banner exposed overflow in Firefox and WebKit that was absent with an empty listing. All three engines decoded the saved artwork at the end of the run, verified its layout at all six widths, and moved focus to the editor with the Create banner shortcut. The synthetic account and QR fixture were restored to their original state. Test drafts were retained.
- Exercised branded 403 CSRF, 404, 500 and Google 503 templates with production-style Django settings. The Google fallback kept its no-store response. Existing tests cover authorization, CSRF, purchasing, payment, identity and live update behavior. Live Google and payment-provider acceptance, external email, physical printers and devices, and full screen-reader verification remain open.

### Pre-deployment verification — 2026-11-24

- **Regression suite.** `python manage.py test core --keepdb --noinput` passed 206 tests on PostgreSQL 17 and Redis 7. New HTTP coverage exercises independent logo upload, count reconciliation with stale and competing submissions, supplier returns, refund replay and limits, payment cancellation, approved bank confirmation, and retention of original payments. The Redis regression verifies that a revoked live order stream closes before delivering a later event. The storage and Resend tests use controlled transport responses. Eighteen refund and purchasing tests passed again after final layout adjustments.
- **Browser interactions.** Real Chrome, Firefox, and WebKit on Windows ran against local Uvicorn, PostgreSQL, Redis and the outbox worker with separate synthetic tenant fixtures. Each engine verified that a saved restaurant logo appears on an already open guest menu, a signed QR cart places an order, a cash tender and a partial refund appear live in guest tracking, and a stock count and supplier return update another open inventory tab. Layouts were checked at 320, 375, 768, 834, 1200 and 1600 CSS pixels. Chrome captures were used to review ordering, count and refund controls. WebKit emitted a cancelled-fetch error during an early test that left the POS before its post-tender synchronization finished. The complete path through the paid receipt link and synchronized queue passed.
- **Deployment checks.** `check --deploy --fail-level ERROR`, `makemigrations --check --dry-run`, `collectstatic --noinput` and the real Channels group probe passed. HSTS preload was the one deployment warning. The Python 3.12 release image built and served on the configured PORT in a local container. WhiteNoise assets, PostgreSQL and Redis readiness, HTTPS redirect handling, HSTS headers and Channels group delivery passed. No hosted runtime or public TLS termination was used.
- **Isolated recovery.** A PostgreSQL custom-format dump of a synthetic database restored into another empty database. A physical count, its movement, the Rp 5,000 refund and a normalized logo digest matched. The regular development database was outside this rehearsal.
- **Provider and hardware acceptance.** Real QRIS settlement, cancellation and refund confirmation, SaaS billing settlement, Google consent, Resend receipt, hosted Supabase and Valkey operation, physical ESC/POS output, and hosted security review still require their actual environments.


### Ordering Page Builder regression

Run `python manage.py test core.test_ordering_builder core.test_catalog_restore core.test_staging_adapters --keepdb` on PostgreSQL and Redis for draft, publication, media, permission, and concurrency coverage. The simultaneous save test requires a database with row locks. Run the outbox worker alongside the ASGI server for browser tests of an open guest menu. [Verified workflows and limits](ORDERING-PAGE-BUILDER.md)
