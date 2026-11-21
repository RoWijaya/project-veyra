# Veyra feature status

This inventory follows the supplied Veyra product development brief (sections 3–28) and was checked against the current Django codebase. “Implemented” means the user flow and supporting code exist; it does not by itself claim production readiness. Partial systems remain partial.

## Scope inventory

| Brief area | Current status | Evidence / remaining work |
|---|---|---|
| Multi-tenant businesses (3) | Implemented baseline | Business ownership, memberships, tenant-filtered services and tenant-isolation tests. Workspace/location grants are not yet part of tenant authorization. |
| Accounts and permissions (4) | Partial | Email verification, secure throttled resend, single-use locked verification, disabled-account replay protection, password reset, login throttling, hashed invitations, permission strings and station tokens exist. Google OIDC subject binding/explicit linking, encrypted TOTP, recovery codes, production privileged-enrollment policy and responsive account-security UI are integrated and locally tested. Live Google consent, passkeys, assisted factor recovery, session/device inventory and Google disconnection remain missing/unverified. |
| Locations, departments, workspaces (5) | Partial | Location management and workspace/department models exist. Workspace assignment does not yet authorize access; location-level permission boundaries and ownership transfer are missing. |
| Table management and guest ordering (6) | Partial, integrated | Active/revocable signed table QR, server-priced cart quantities, notes, counter or verified QRIS selection, private per-order HttpOnly tracking cookie, order summary, QRIS display and guest Channels updates exist. HTTP flow, payment retries/idempotency, PostgreSQL, Redis and real browser status updates were verified. Separate Menu and Your Order support independent guest carts, quantity editing and checkout retries. Guest takeaway is available at enabled locations through a shareable link or the table checkout. Guests can select product options and apply vouchers in Your Order. Customer notifications remain. |
| POS, dine-in and takeaway (7) | Partial, integrated | Tenant-scoped product selection, order type/location/customer, server pricing, snapshots, idempotent creation and status workflow exist. Cashiers can record cash with server-calculated change or manually confirm card terminal acceptance; tender records are idempotent and audited, and payment state streams live. Search and category controls, order history and paid receipt previews are integrated. Cashiers can preview and redeem vouchers using the shared checkout service. Held orders, POS QRIS, refunds, taxes/service charges, accounting workflows and split payments remain. |
| Catalog and menu management (8) | Partial | Catalog managers can manage tenant categories and edit product assignment, descriptions, prices, availability, station routing and photos. The guest menu and POS receive live catalog updates. Product options include reusable groups, selection limits, surcharges, location scope and ordering. Stocked variants, bulk import/export and richer merchandising remain. |
| Scheduled prices and promotions (9) | Partial, integrated | Merchant promotion editor supports product/category/location targeting, business-local dates/times/weekdays, fixed/percent/amount discounts, priority and stacking. One server pricing engine feeds guest menu and POS; order lines preserve base/effective prices and discount snapshots. Redis menu WebSockets update open menus on saves and schedule boundaries through `process_outbox`. Voucher management and scheduled-promotion stacking are integrated. Fuller promotion reporting remains. |
| Vouchers and discounts (10) | Implemented and locally tested | Scoped fixed and percentage vouchers, business-local windows, POS and guest checkout, transactional limits and sales reporting. See the voucher verification entry below for external acceptance and refund gaps. |
| Banners and announcements (11) | Implemented baseline | Owners can customize the general ordering page banner, display name and introduction. Campaign banners separately target a product, category or promotion and retain previews, active windows, locations and priority. Local HTTP and browser verification passed. Broader announcements remain open. |
| Kitchen, bar, stations and printing (12) | Partial | Station pairing/capabilities, KDS, product routing, print queue, ticket formatting, acknowledgement and reprint controls exist. Hardware-specific print delivery/recovery and complete operational workflows need deployment/hardware verification. |
| Inventory and purchasing (13) | Partial, integrated | Location balances, append-only movements, opening/adjustment flows, tenant-scoped recipe editing, locked stock deduction when an order enters preparation, cancellation returns and Redis inventory update events exist. Supplier management, location-scoped purchase orders, partial and full receiving, purchase cost history and live stock updates are integrated. Physical counts, supplier returns, low-stock alerts and accounting valuation remain. |
| Customers (14) | Partial | Tenant-scoped customer records and order snapshots exist. Customer account/consent, history, segmentation and communications are missing. |
| Payments and QRIS (15) | Partial | Encrypted provider credentials, verification, Midtrans and BCA SNAP adapters, dynamic QR payload, expiry, signed/replay-protected webhooks and payment-driven order release exist. Guest/counter selection, QRIS tracking/retry and POS cash/card tender with change are integrated. Provider calls are mocked in tests; external merchant credentials and real settlement/refund/reconciliation were unavailable. POS QRIS checkout and refunds remain. |
| Real-time synchronization (16) | Partial | PostgreSQL outbox → Redis Channels streams business order/payment events, private guest order/payment status, guest menu promotion changes and authorized location inventory balances. Browser verified live menu repricing; PostgreSQL/Redis tests cover payment, order, menu boundary and recipe stock events. Catalog administration, staff, workspace and notification synchronization remain incomplete. |
| Analytics and reporting (17) | Partial | Permission-gated reports query PostgreSQL for paid revenue, unpaid/pending order value, cancellations, averages, daily paid sales, product/category/payment/location breakdowns, and dine-in/takeaway counts, with date/location/category filters. Voucher redemptions, discounts and paid-order metrics are integrated. Employee/station, inventory, fuller promotion reporting, tax, refunds, exports and scheduled reports remain. |
| SaaS subscriptions/platform admin (18) | Monthly manual-payment billing implemented and locally tested | Owner billing, trials, central feature and usage entitlements, plan changes, grace and recovery, period-end cancellation, durable invoices, platform checkout, Django administration, and post-commit live updates. Live merchant settlement, tax accounting, automatic debit and platform support impersonation remain open. |
| Notifications and audit (19) | Partial | Append-only audit events and invitation/account email paths exist. Notification preferences, delivery channels, templates, retries and in-product notification center are missing. |
| Observability and incident response (20) | Partial | Liveness plus PostgreSQL/Redis readiness, request correlation IDs, allowlisted structured JSON, redacted outbox failure logs and an incident runbook exist. HTTP/fault-injection/log tests pass. External exception tracking, metrics, alerts, worker heartbeat and WebSocket-specific failure visibility remain. |
| Security and ASVS (21) | Partial; not audited | Tenant permissions, CSRF, signed guest/station credentials, encrypted payment secrets, webhook signature/replay checks and audit records exist. A threat model and 253-row ASVS 5.0.0 Level 1/2 assessment register now exist; all requirements remain unverified pending assessment. TOTP MFA is locally tested; penetration testing and production security review remain required. |
| Landing page / visual design (22) | Partial | Responsive landing page with film/poster and GSAP/ScrollTrigger motion exists. Cross-browser/performance review and full visual acceptance remain. |
| Accessibility / responsive quality (23) | Partial | Semantic labels, status roles and reduced-motion handling exist. No full keyboard/screen-reader and viewport audit has been completed. |
| Data integrity and performance (24) | Partial | PostgreSQL constraints, tenant filters, transactions, order/payment idempotency and append-only records exist. Load/concurrency testing, query review and operational backup/restore verification remain. |
| Testing and E2E (25) | Partial | 158 Django tests passed against PostgreSQL and Redis on 2026-11-21; Redis-backed Channels tests cover private guest snapshots/order/payment updates, live promotion edits, scheduled pricing boundaries and authorized inventory updates. Browser verified QR checkout/tracking/status and live repricing of an open menu. Provider callbacks, broader tenant attack, observability and formal security scenarios remain. |
| Development/acceptance/deliverables (26–28) | In progress | Django/PostgreSQL/Redis/Channels/ASGI architecture is retained. Overall product acceptance is not met while the rows above remain incomplete. |

## Verification completed on 2026-11-16

- PostgreSQL 17 and Redis 7 local services were healthy.
- Applied migrations through `core.0018_subscription_foundation` to the local development database. Existing tenants were assigned grandfathered access and an append-only migration event; new business creation starts a 14-day trial.
- `manage.py check` passed; `makemigrations --check --dry-run` reported no changes. `check --deploy` with temporary production-mode settings had only the optional HSTS preload recommendation; production SMTP, domain, TLS proxy and secret values were unavailable.
- `manage.py test core.tests --keepdb` passed all 71 tests using PostgreSQL; the suite includes Redis-backed guest order/payment, menu update, scheduled-boundary and inventory WebSocket tests, plus SaaS trial/admin HTTP workflows. The final fail-closed/no-subscription and append-only-history changes also passed focused PostgreSQL HTTP tests.
- Browser smoke test ran the app through Daphne (`config.asgi:application`): a guest submitted a server-priced table order, saw a private tracking page, and observed the order status change live after the owner advanced it from the POS. A second browser check confirmed live menu repricing after a merchant saved a promotion. Browser QA accounts/catalog/table/promotion were deactivated afterward; append-only QA audit history remains in the local PostgreSQL database.
- QRIS HTTP creation is covered with a mocked provider API response and checks verified-connection gating, pending transaction idempotency and protected QR rendering. No live Midtrans/BCA credentials or production merchant account were available.
- Promotion HTTP workflow and repricing of an already-open guest menu were exercised in the browser. `process_outbox` must run continuously for scheduled boundary events and durable event delivery.

## Limits that remain

- Passing tests and the browser smoke test do not constitute full feature acceptance, ASVS certification, penetration testing or production readiness.
- Real payment initiation, signed production webhook delivery, refunds/reconciliation and physical printer output require provider accounts/secrets and hardware that were not available.
- Production monitoring/alerting, transactional email delivery, media storage, backup/restore and deployment infrastructure have not been verified.
- At this checkpoint subscription administration recorded plan and status only. See the later platform subscription billing entry for the current implementation.
- Reported revenue currently means orders explicitly marked paid. Card tender is a staff confirmation after terminal acceptance and is not provider-integrated; refunds, staff/station analytics, detailed inventory valuation and report exports are not implemented; per-location report grants are not modeled.
- A test-only `daphne` dependency was added to the development extra to run the real ASGI WebSocket test. The production container continues to serve `config.asgi:application` with Uvicorn.

## Implementation cycle verified on 2026-11-17

- Restored the existing Docker dependency containers without resetting PostgreSQL/Redis data. Saved a source-only recovery archive outside the repository before edits; Git still has no HEAD commit.
- Added verification resend with CSRF, hashed IP/address throttles, uniform confirmation and audit events. Verification consumes locked unverified/inactive state; an old link cannot reactivate a previously verified disabled account. SMTP retry/delivery monitoring remains missing.
- Added safe JSON/request correlation, actual PostgreSQL/Redis readiness, safe outbox failure visibility, and disabled raw URL access logs in the production container command. Proxy logging still requires deployment configuration.
- `manage.py test core --keepdb` passed 79 tests in 131.267s. New modules must be included by discovery. The final run includes row-lock/logging hardening and safe local-return/open-redirect coverage.
- Real browser: login resend link, form submission for a synthetic unknown address, and uniform confirmation passed through Uvicorn. Eligible delivery is covered by test mail backend; production SMTP is unverified.
- Added PRD, canonical architecture/security, integrations, testing, operations, development, threat model, ASVS register and ADRs. Corrected stale recipe/subscription database documentation.

## Google OIDC/MFA and interface cycle — 2026-11-17

- Applied additive migration 0019 for GoogleIdentity and Authenticator. Existing tenant/order/subscription data was preserved.
- Authlib code flow validates state, nonce, PKCE and RSA-signed tokens. Existing email matches require explicit authenticated linking. Tests mock Google HTTP; live credentials/consent remain unavailable.
- MFA integrates encrypted setup/QR, locked TOTP counters, single-use recovery codes, challenge, privileged production enrollment and HTTP/ASGI session enforcement. Local dedicated encryption key was configured without displaying it.
- Full PostgreSQL/Redis suite: 87 tests passed in 107.970s. Final focused stream test also verified partial-login rejection and logout revocation on the next data delivery.
- Actual browser verified MFA failure/success, account security, phone/tablet reflow, keyboard focus and 48px inputs. This is not full accessibility acceptance or pixel-exact reproduction of the generated design target.
- Synthetic browser QA account was deactivated; audit history remains. Passkeys, assisted total-loss recovery, session inventory and Google disconnection remain missing. No production-verification claim is made.

## Figma implementation cycle — 2026-11-18

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

## Visual parity and guest takeaway — 2026-11-18

| Workflow | Implementation and automated evidence | Browser evidence and remaining verification |
|---|---|---|
| Menu, cart and POS controls | The storefront uses category buttons linked to real catalog filters. Menu, cart and POS share the stepper style. The cart's server-validated quantity actions preserve checkout input and update totals. HTTP tests cover stale revisions, minimum quantity and category promotion pricing. | Approved Figma frames reviewed. Browser checks covered keyboard selection, focus, touch targets, totals, checkout notes and the POS draft. Real product data and the additional pickup context remain visible. |
| Authenticated workspaces | The espresso workspace navigation now includes the brand, business identity, location where selected and an account disclosure. Settings stays near the top. Existing permission-filtered navigation and CSRF-protected sign-out remain. | Keyboard checks exercised the account disclosure, focus return and compact business menu across desktop, tablet and phone. Full screen-reader and visual acceptance remain open. |
| Guest takeaway | Managers can enable and share a signed location menu. Eligible table guests can choose takeaway from their existing cart. Both use the shared order service, payment options, recipe consumption and private tracking. HTTP tests cover both entrances, replay, scoped management, revocation, station completion, receipt and inventory movements. | Chrome, Firefox and Playwright WebKit exercised live POS, counter tender, kitchen completion and receipt at 320–1200px. The kitchen queue now keeps existing forms in place when a payment-only event or unchanged reconnect snapshot arrives. Chrome verified a fresh server snapshot after tracking reconnected. |
| QRIS and printing | A mocked Midtrans test covers takeaway QRIS generation, private QR access, blocked preparation while pending, signed settlement and duplicate notifications. Existing payment and print-queue tests passed. | Chromium measured 163.92 pt and 227.04 pt for 58 mm and 80 mm receipt PDFs. Local environment had zero verified sandbox merchant connections and zero device print queues. Hardware, external settlement and production evidence remain open. |

Migration `0022_guest_takeaway` was applied to the local PostgreSQL database. Django checks and migration consistency passed. The full PostgreSQL and Redis run passed 110 tests. The current Git branch still has no commit baseline.

The next business feature at this checkpoint was product modifiers. See the current product customization evidence below. Vouchers, suppliers and purchasing, subscription billing, live QRIS reconciliation, physical printing and a full accessibility review remain open.

## Product modifiers — 2026-11-20

| Workflow | Implementation | Verification |
|---|---|---|
| Catalog management | Catalog managers can create and edit reusable groups, assign products and locations, set minimum and maximum selections, and configure ordering, availability and option surcharges. | HTTP tests cover manager authorization and foreign product validation. Browser runs exercised the editor and catalog entry point. |
| Guest and POS | Single- and multiple-choice groups share one location-aware pricing and validation service. Guests can edit selections in the cart. Both flows accept several configurations of the same product. | The browser workflow passed in Chrome, Firefox and Playwright WebKit on Windows. It covered keyboard selection, cart editing, multiple POS configurations, draft preservation, kitchen handoff, receipts and a 320–1200 px storefront layout. Chrome also observed a merchant price change update an already-open menu and POS draft over Redis and Channels, and verified that resetting a removed option preserves its draft quantity. |
| Historical orders | Order lines preserve option names, group names and unit surcharges. Kitchen queues, station payloads, prep print jobs and customer receipts use those snapshots. | HTTP tests cover stale quotes, location scope, selection limits and receipt history after an option edit. Chromium printed customized receipts to PDF at both 58 mm and 80 mm. |

Migration `0023_product_modifiers` is applied locally. The full PostgreSQL and Redis suite passed 117 tests. Chrome also passed the plain-product takeaway regression. Production verification, an assistive-technology audit, live QRIS settlement and physical printing remain open. The local environment has zero verified sandbox merchant connections and zero device print queues.

Voucher, supplier purchasing, stock receiving and subscription billing work followed this checkpoint.

## Design freeze and vouchers — 2026-11-20

| Area | Implemented | Verification |
|---|---|---|
| UI polish | Reviewed approved Figma frames for the authenticated header, category controls and quantity steppers. Improved context visibility, menu focus return, category wrapping and stepper sizing. The current visual direction is frozen in the PRD. | Chrome exercised header navigation, checkout, focus, quantity limits, live tracking and printing at desktop, tablet and phone widths. A full screen-reader audit remains open. |
| Voucher management | Promotions → Vouchers covers searchable listings, discounts, status, validity, eligibility, usage, remaining capacity and recent changes. Catalog managers control creation, activation and edits. | HTTP tests cover authorized edits, duplicate codes, business-local dates, read-only and cross-tenant denials, and foreign product selection. |
| Pricing and checkout | Your Order and POS use the same eligibility and Decimal arithmetic. Checkout revalidates the confirmed price and commits one redemption with the order. Cashier-selected saved customers support per-customer limits. | HTTP tests cover modified draft and stale quotes, price tampering, stacking, modifier surcharges, category/location/order type, minimum/cap/rounding, replay and a two-transaction race for the last use. |
| Payment and records | Signed payment callbacks and cash tender use the reduced order total. Historical order data feeds tracking, receipts and reprints. Reports group redemptions and actual paid sales by voucher and location. | Automated coverage includes cash, mocked QRIS failure/expiry/settlement and duplicate callbacks, receipt snapshots, authorized device print and reprint commands, and report arithmetic. Production provider settlement and physical printing remain unverified. |

Chrome, Firefox and Playwright WebKit passed voucher management, guest/POS checkout, live invalidation, cash payment and receipt previews at 320, 834 and 1200 px. Chromium PDFs measured 163.92 pt at 58 mm and 227.04 pt at 80 mm. The development database has zero verified sandbox merchant connections and zero active printers.

Migration `0024_vouchers` was applied locally. The full PostgreSQL and Redis suite passed 125 tests. The application uses one committed use per created order, including unpaid, failed, expired or cancelled orders. Refund and partial-refund execution remain unimplemented, and a later refund workflow must preserve that original redemption. Anonymous first-order and per-person limits are unavailable.

Supplier purchasing followed this checkpoint. See the current implementation and verification below.

## Supplier purchasing — 2026-11-20

| Workflow | Implemented | Local verification |
|---|---|---|
| Suppliers | Tenant-owned contacts, status, search, filtering, edit, and paginated purchase history. Inactive suppliers stay on existing purchases. | HTTP tests and Chrome, Firefox, and WebKit exercised creation, edits, deactivation, and authorization. |
| Purchase orders | Location-scoped multi-item drafts, server-priced order review, submission, cancellation, snapshots, expected delivery and filters by status, supplier, item, location, date and reference. | HTTP tests verify draft edits, lifecycle checks, server totals, history and cross-tenant and location denials. Three browsers exercised the main draft and order workflow. |
| Delivery and stock | Partial and complete receipts link accepted quantities and historical unit cost to the existing stock ledger. Rejected and damaged units remain outstanding. PostgreSQL locks and receipt keys protect against replay and over-receipt. | HTTP tests cover repeated and concurrent receiving, exact-once stock changes, stock overflow rollback, and preserved history. Browser tests verify invalid delivery feedback, two deliveries, live balances and receipt history. |
| Permissions and live updates | Existing inventory permissions, owner-managed location limits, and scope-checked purchase and inventory WebSockets. Purchase list, detail and supplier history fetch authorized snapshots when events arrive. | PostgreSQL and Redis tests cover authorized delivery and denial for an unrelated location. Live list, detail and stock updates passed in three browsers without manual reload. |
| Interface | Supplier, purchase and receiving pages extend the frozen inventory UI and existing controls. | Receiving passed at 320, 375, 834, and 1200 px in Chrome, Firefox, and WebKit. Reviewed Chrome captures at phone and tablet widths against the approved Inventory frame. |

Additive migrations 0025_suppliers and 0026_purchase_orders are applied locally. The final PostgreSQL and Redis suite passed 133 tests, including eight purchasing tests. The stock cost field shows the latest accepted purchase cost. Accounting valuation, purchase returns, and count-based reconciliation are separate unfinished work. Production deployment and assistive-technology acceptance remain open.

## Soft Premium Veyra and platform billing — 2026-11-20

| Area | Implemented | Local verification |
|---|---|---|
| Soft Premium Veyra | Refined the shared 6/10/16/20 px radius scale, warm surfaces, navigation states, operational tables and storefront components. Nested purchasing content uses separators and spacing. Focus styling and reduced-motion behavior are shared. | Reviewed the approved Figma workspace reference and app screenshots. Chrome, Firefox and WebKit exercised representative workspaces and controls at phone, tablet and desktop widths. Chrome rechecked receiving, live stock, takeaway and receipt PDFs. |
| Owner billing | Subscription and usage overview, plan comparison, scheduling, cancellation and resume, invoices, checkout and recovery. Restricted owners can read plans and billing history with recovery guidance. | Three browsers verified the trial and expired flows at 320, 834 and 1200 px, including an invoice redirect, failure and retry, paid history and live updates. Chrome verified restricted billing and invoice access at phone width. |
| Lifecycle and entitlements | Default 14-day starter trial, configurable eligibility for new businesses, saved trial and paid terms, locked monthly renewal, seven-day grace, safe downgrade limits and end-of-period cancellation. Expiry and reconciliation run independently of page visits. | PostgreSQL HTTP tests cover trial and expiry boundaries, admin plan and subscription controls, renewal, recovery, historical terms, and concurrent invoice requests. |
| Platform checkout and audit | Dedicated Midtrans platform boundary, verified signed notifications, authoritative status lookups, retry-safe attempts, immutable invoice snapshots, admin audit history and Redis owner updates after commit. | Automated tests cover cross-tenant denial, CSRF, webhook integrity and replay. The local three-browser flow used a mocked provider through Uvicorn and the Redis outbox worker. |

The full PostgreSQL and Redis suite passed 143 tests, including ten billing tests and the previous 133-test baseline. Both additive billing migrations are applied locally. Production verification requires published paid plans, a dedicated platform merchant account, a public HTTPS notification endpoint and supervised billing and outbox workers. The current scope is monthly owner-initiated payment. Automatic debit, annual billing, proration, immediate cancellation, tax invoices and billing reminder email are outside the implemented scope.

## Promotion target selection — 2026-11-21

Promotions & pricing uses searchable Products and Categories pickers with named options, removable selections, counts, and clear-selection controls. Existing selections return on edit and after validation errors. Previously linked inactive products can be retained or removed when editing that promotion. Django retains the tenant-scoped ID validation and CSRF checks.

Chrome, Firefox and WebKit verified selection and saving at phone, tablet and desktop widths. The full PostgreSQL and Redis suite passed 146 tests.

## Product-wide UI polish — 2026-11-21

- Marketing gives the POS and business operations purpose in the hero, shows a browser capture from an example workspace, and keeps the restaurant tour with motion preferences. The footer and primary links use actual destinations.
- Account screens share a focused card for sign-in, registration, verification, password reset and MFA. Expired links offer recovery and completed resets lead to sign-in. Password visibility and related error messages are keyboard-accessible. The explicit favicon keeps browser icon requests on the static path during MFA.
- Workspace and customer screens share stronger field borders, spacing, accessible status colors and control geometry. The management dashboard uses two columns on wider screens and a single column on smaller screens. Recent orders link to order history.
- Promotions and vouchers share a reusable searchable picker. Order history and sales reporting support labeled table scrolling. Paid revenue comparisons use real daily report values and keep the amounts visible as text.
- Dismissing a purchase or billing cancellation confirmation keeps its live connection open. Chrome verified that another tab’s partial delivery, subscription cancellation, and renewal resumption arrived without a reload.

### Audit coverage and files

| Area | Change | Browser coverage |
| --- | --- | --- |
| Landing | Product-focused hero, capture of the actual POS running with sample catalog data, real product and account destinations, reduced-motion cinematic tour | Route matrix, selected screenshots, and interaction checks |
| Account | One sign-in and recovery treatment, meaningful expired-link actions, linked validation errors, password visibility, and explicit favicon during MFA | Route matrix, selected screenshots, and interaction checks |
| Shell, dashboard, and control system | Shared ivory and espresso surfaces, a 6/10/16/20 px radius scale, clearer control boundaries, keyboard focus, selected navigation, mobile menus, and delayed form status | Route matrix, selected screenshots, and interaction checks |
| Orders, POS, customer ordering, reports, and campaign forms | More compact order history with local table scroll, real paid-sales comparisons, stronger checkout control styles, and the same searchable target picker in both promotions and vouchers | Route matrix, selected screenshots, and interaction checks |
| Catalog, settings, customers, table management, team, billing, inventory, suppliers, purchasing, and operational stations | Reviewed routes kept their existing workflows and inherit the shared shell and control changes | Route matrix and selected captures. Interactive checks covered representative controls and workflows as recorded in TESTING.md. |

Source changes are in `templates/core/` (layout, account, landing, dashboard, reports, pagination, order history, and campaign templates), `core/static/core/` (site and app styles, shared interaction scripts, picker, favicon, and POS preview), and `core/forms.py` and `core/views.py` (presentation data). Regression coverage lives in `core/test_account_recovery.py` and `core/tests.py`. The PRD, cinematic workflow, and testing documentation record design and verification decisions.

Provider and physical-device acceptance and a complete screen-reader audit remain open. Route coverage records rendering and responsive checks. The testing log identifies the separate, narrower interaction coverage.

## Catalog and ordering page — 2026-11-21

| Area | Implemented |
| --- | --- |
| Category management | Product catalog → Categories supports search, details, display order, visibility and deletion. Assignment validates against the active business during creation and editing. Hiding a category removes the guest filter while its active products remain orderable under All. Deletion is blocked while products, promotions, vouchers or campaign banners refer to the category. |
| Product details and images | Product creation and editing support guest descriptions and image upload, preview, retention, replacement and removal. Catalog, POS, table menu, takeaway menu and cart use the same saved image. A failed image load leaves the product text and ordering controls usable. |
| Ordering page | Settings → Ordering page gives the owner control of the guest display name, introduction and general banner. Guests see saved appearance on their signed table and pickup links. Owners can preview an existing signed menu and change or remove the banner independently of campaign banners. Campaigns keep their own schedule, location eligibility and CTA. |
| Live propagation | Product, category and appearance changes queue an outbox event in the same transaction. Redis and Channels deliver changes to open menus. Authorized POS sessions also receive catalog events and preserve eligible quantities and selections. A cart can refresh a changed photo without replacing checkout inputs. |
| Storage and isolation | Merchant images live as normalized WebP bytes in PostgreSQL. The existing Docker PostgreSQL volume persists the data with the business and catalog. Uploads are decoded as JPEG, PNG or WebP, limited to 5 MB and 16 million pixels, restricted to a single frame and re-encoded with metadata removed. Media responses have a fixed MIME type and filename, `nosniff` and private no-store headers. Management uses server-side tenant membership and mutation permissions. Guest assets and visibility are resolved from their current signed link. |

Migrations 0029–0031 add category details and visibility, product descriptions, and an optional one-to-one ordering page. Existing categories stay visible and products keep their assignments and images. The default guest display name still comes from the business. A custom logo, color palette and layout editor are outside this scope. Deployment-specific PostgreSQL storage, backup and restore remain production acceptance items.

Main changes are in [catalog views](../core/catalog_views.py), [media views](../core/media_views.py), [image forms](../core/media_forms.py), [storefront rendering](../core/customer_views.py), the catalog and ordering page templates, and the existing storefront and POS scripts. See [Testing](TESTING.md) for HTTP and browser evidence.
