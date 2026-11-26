# Veyra product requirements

These requirements describe intended product behavior. Dated implementation notes below are historical. For the current state and evidence, see [feature status](FEATURE-STATUS.md) and [release history](RELEASE-HISTORY.md).

## Identity implementation update — 2026-11-17

Google authorization-code OIDC now uses Authlib state, nonce, S256 PKCE and signed-token validation before subject binding. Existing email matches require explicit authenticated linking. Live Google consent is unverified without credentials.

Authenticator enrollment requires password confirmation or recent Google authentication, then a valid TOTP. Secrets use a dedicated Fernet key. Locked counter/recovery updates prevent reuse; ten recovery codes are displayed once. Enrolled accounts always require MFA. Owner/platform-admin enrollment defaults mandatory outside debug through MFA_REQUIRE_PRIVILEGED. HTTP and ASGI enforce partial-session restrictions, including current-session checks before stream data delivery.

The new account-security interface uses shared ivory/espresso tokens, section navigation, progressive disclosure and native keyboard controls. Browser checks covered MFA errors/success, desktop/phone/tablet layouts and 48px inputs. Passkeys, assisted total-loss recovery, session inventory and Google disconnection remain missing. These changes are implemented and locally tested, not production-verified.

The identity note above was recorded on 2026-11-17. Veyra's tagline is **Everything in sync.**

## Vision and users

Veyra connects sales, customer ordering, preparation, stock, payments and management for cafes, restaurants, bakeries, retail and other businesses. Owners administer one or more businesses. Collaborators receive explicit permissions. Paired stations run constrained operational tasks. Guests order without creating accounts. Platform administrators manage Veyra subscriptions separately from merchant payment accounts.

The goal is an integrated, secure, production-quality SaaS. Replacing Django templates, merging tenant merchant accounts, fabricated revenue, destructive database resets and claiming compliance without evidence are outside the product direction.

## Requirements and current evidence

[FEATURE-STATUS.md](FEATURE-STATUS.md) is the canonical implementation inventory for every original brief area. It distinguishes implemented paths, partial features, missing work and external verification. Requirements below remain required even when implementation is partial.

| Area | Required behavior | Acceptance boundary |
|---|---|---|
| Accounts | Verified registration, resend, password recovery, Google OIDC/linking, sessions, owner/admin MFA, invitations and abuse controls | Unverified and disabled accounts cannot gain access; linking requires verified identity and explicit authorization |
| Tenant access | Distinct Business, Location, Department, Workspace and assignments; explicit permissions | Foreign tenant objects are rejected; choosing a workspace never grants access |
| Guest ordering | Signed table menu, variants/modifiers, cart, instructions, independent orders, confirmation and private live tracking | Shared table does not disclose another guest's order; prices are authoritative |
| POS | Search/categories, cart, customers, discounts/vouchers, cash/digital tender, receipts, history, refunds and shifts | Idempotent checkout and auditable financial corrections |
| Operations | Configurable stations, routing, preparation queues, prioritization, status, KDS and optional printing | Authorized devices synchronize without manual refresh; failed printing can be recovered |
| Stock | Recipes, deduction/returns, movements, transfers, suppliers, purchase orders, partial receiving, valuation and alerts | Quantities remain consistent under concurrent operations; history is preserved |
| Pricing | Deterministic schedules, recurrence, timezone, restrictions, caps, usage and stacking | Rp25,000 becomes Rp18,000 daily at midnight, restores at 06:00, including live featured placement |
| Campaigns | Vouchers, banners, announcements, images and scheduled/location visibility | Checkout revalidates eligibility; limits cannot be exceeded by concurrent redemption |
| Payments | Per-business Midtrans/BCA SNAP QRIS, signatures, replay protection, expiry, refunds and reconciliation | Only verified provider settlement marks QRIS paid; preparation honors release policy |
| SaaS | Plans, trials, entitlements, limits, upgrades/downgrades, periods, renewal, failures, grace, cancellation and history | Missing/expired access fails closed; billing never uses tenant merchant credentials |
| Customers/reports | Customer contact/history and actual sales, stock, employee/station, promotion and refund analytics | Filters and permissions apply; no fabricated metrics |
| Realtime | Orders, POS, stations, stock, availability, menu, pricing, payments, memberships, assignments, notifications, dashboards and printers | Tenant-aware Channels/Redis streams recover authoritative state after reconnect |
| Operations/security | Structured logs, correlation, readiness, failure visibility, alerting, secure uploads, backups and ASVS 5.0 L2 assessment | No secrets in logs; failure drills and requirement-level evidence precede acceptance |
| Interface | Editorial ivory/espresso identity, complete responsive flows, substantial landing GSAP/ScrollTrigger | Controls work; keyboard access, reduced motion and target viewports are verified |

## Core business rules

- PostgreSQL is authoritative. Redis carries events and shared production rate limits. Transactional outbox persists retryable operational events.
- All channels create orders through shared pricing/order services. Prices, names, discounts and customer details are historical snapshots.
- Signed table links are revocable. Guest tracking uses a separate private signed HttpOnly cookie per order.
- Recipe stock is deducted when preparation starts and returned on supported cancellation. Append-only movements explain corrections.
- Cash/card tender is cashier-recorded; QRIS settlement comes from verified callbacks. Card recording does not integrate a terminal.
- Existing tenants retain grandfathered subscription access; new businesses start a 14-day trial. Missing subscription records do not grant feature access.
- Verification links expire after 24 hours and are single-use through locked unverified account state. Reuse cannot reactivate a previously verified disabled account.
- Resend returns the same confirmation for known and unknown addresses. At most one eligible message per address per ten minutes and eight attempts per source IP are processed in that window.

## Non-functional acceptance

Preserve Django modular monolith, PostgreSQL, Redis, Channels, ASGI/Uvicorn and server-rendered templates. Maintain tenant isolation, transaction integrity, idempotency and explicit authorization. Realtime cannot be replaced by polling-only interfaces. Test on PostgreSQL/Redis and actual browsers, with external service tests clearly identified as mocked or live. Review performance, accessibility, backup recovery and production security before commercial acceptance.

## Dependencies and limitations

Provider merchant accounts, Google OAuth credentials, SMTP, printer hardware and production hosting/monitoring are external dependencies. Hosted Google consent and billing require external acceptance. Assisted factor recovery requires an implementation. [Current work and acceptance gates](FEATURE-STATUS.md) Current passing tests establish specific behavior, not overall product acceptance.

## Figma design and application acceptance

These requirements apply to the public website, authenticated application and guest storefront. Each workflow needs an editable design, a working Django implementation and evidence of the interactions exercised. Record progress and links to the relevant evidence in [FEATURE-STATUS.md](FEATURE-STATUS.md).

### Design system and navigation

- Keep the existing warm ivory, cream, espresso and charcoal palette. Define named color and spacing variables, text and effect styles, responsive grids, control heights, icon sizes and component states in Figma. Map them to shared CSS and reusable Django template components.
- Increase functional text sizes across navigation, forms, product descriptions, tables, prices and station tickets. Distinguish display, title, section, body, supporting text and status roles through weight, size, line height and contrast. Long names, amounts and error messages must remain legible when the viewport narrows or text scales.
- Place Settings near the top of authenticated business navigation. Group related destinations by the user's work. Expandable settings, advanced filters and contextual actions must expose their current state and work with a keyboard or touch. Keep checkout, payment status and principal actions immediately discoverable. Scoping and permissions apply independently of navigation visibility.
- Use native editable frames, Auto Layout, component instances and variants to represent controls, selects, multi-selects, date inputs, navigation groups, tables, status indicators, dialogs, notifications, tabs, accordions, product images, promotional banners, cart lines and receipts. Include meaningful focus, pressed, disabled, empty, loading, validation, success and failure states.
- Cover the landing page, authentication and account security, business/location management, catalog/categories, employees and permissions, workspaces, tables, order history, stations, inventory/purchasing, promotions/vouchers, billing and analytics. Group major workflows and their responsive variants on named Figma pages. Distinguish proposed workflows from working destinations in the application.
- Dashboard summaries, inventory alerts, recent activity, notifications and quick actions must use authorized business data. Location/workspace selection must narrow the view within existing grants. A new business sees a useful empty state and an eligible next action.

### Storefront, carts and campaigns

- A signed table link must open a branded Menu page for that business, location and table. An authorized location manager can enable and share a signed takeaway menu or disable the current link. A standalone takeaway checkout uses the signed location. At an enabled location, table guests can also choose takeaway in Your Order. Takeaway orders hold a location, fulfillment type and server-side tracking digest. Dine-in retains the table verified by the QR link. Show configured branding, product names, images, descriptions, availability, categories, featured products, applicable promotions and current prices. Product discovery and add-to-cart actions belong on the Menu.
- A visible **Your Order** action and quantity count open a separate page. Customers can return to the Menu, change quantities or selections, remove items and edit instructions. Show images, variants, modifiers, discounts, vouchers where supported, subtotal, final total, order type, table context and permitted tenders before checkout. An empty cart provides a route back to the Menu.
- Anonymous carts must survive navigation and refresh. Two guest sessions scanning the same QR must have independent carts. Checkout must validate the current signed identity, availability, variations, quantities, promotion/voucher eligibility and authoritative total. Retried submission must have a single financial effect. Private tracking access starts after successful checkout.
- Catalog managers can create, search, reorder, rename and hide business-scoped categories. Hidden categories stay on their assigned products for management and pricing, while guests can still order those products under All. Deletion is blocked while a product, promotion, voucher or campaign banner refers to the category. Product creation and editing support category assignment or an uncategorized product, guest descriptions, availability and image management.
- Business owners use Settings → Ordering page to set the public display name, optional introduction and general banner. The preview opens an active signed table or takeaway menu. The general banner and the campaign banners can coexist. Brand settings do not change the legal business name on historic orders or receipts.
- Authorized merchants can upload, preview, replace and remove product images. Product management, the storefront, featured products and POS should use the same merchant-managed image. Enforce accepted formats, content validation and upload-size limits. Scope writes and reads to the proper tenant and visibility. Crop and size responsively, provide alternatives and handle an absent image without breaking ordering.
- Merchants can create, preview, edit, activate and deactivate image banners with headline, description, accessible image alternative, CTA, display priority, location eligibility and start/end schedule. CTA targets must resolve to the configured product, category or promotion for that merchant. Staff must be able to distinguish draft, scheduled, active and expired campaigns.
- Use the existing pricing engine and business timezone to resolve campaign eligibility and prices. At the daily midnight/06:00 promotion boundary, an open menu must update price, featured placement, promotion indicator and matching banner through authorized realtime events. On reconnect fetch current server state. Reject stale or ineligible discounts during checkout.

### POS, preparation and receipts

- Prototype and implement fast product selection, payment selection, confirmation, receipt preview, printing and controlled reprinting. Make **Print Receipt** discoverable at eligible checkout completion, paid/completed order details, order management/history and transaction details. Each entry point must enforce tenant, location and employee permissions.
- Generate receipts from historical transaction records including receipt/order identifiers, time, merchant and location, configured logo/footer, cashier, dine-in/table or takeaway, quantities, selections, prices, discounts and applicable tax, service and voucher amounts. Show payment method/status, amount paid and cash change when relevant. Repricing a product must leave an earlier receipt unchanged.
- Preview and print at 58 mm and 80 mm with readable wrapped names, aligned amounts and clearly separated totals. A browser print fallback must work with no paired printer. Opening or cancelling the browser dialog supplies no evidence of physical output.
- Authorized staff can select/configure a location's printer, name, connection type, width, preferences and default receipt printer. Distinguish pending/queued, service acceptance, device acknowledgement, failure and cancellation. Track job history, controlled retry and intentional reprints. Retried requests must not duplicate jobs. Include actual printer hardware and observed output in any physical-print claim.
- Kitchen, bar, coffee, bakery and custom stations need readable, touch-friendly, scoped work queues and permission-appropriate transitions. Redis/Channels synchronization must handle duplicate events and reconnects. Scope status deliveries to the authorized station, business and location.

### Design validation and release evidence

1. Link and exercise Figma prototypes for Dashboard to Settings/catalog/promotions/POS, QR Menu to product/cart edit/checkout, POS to payment/receipt/print/reprint, and banner creation to scheduled storefront preview. Use desktop, tablet and phone frames to assess hierarchy and reflow before translating the approved designs.
2. Compare the actual server-rendered workflows with their design frames at matching viewports. Exercise keyboard navigation, focus, native disclosures, form validation, submissions, status changes and recovery. Verify long content, touch targets, image fallbacks and reduced motion. Preserve purposeful GSAP/ScrollTrigger landing interactions and efficient operational screens. WCAG 2.2 AA is an engineering target.
3. Verify cross-tenant denial for media, banners, receipts, prints and printer settings. Preserve MFA, CSRF, signed guest identity, auditability and authorization on HTTP and WebSocket paths. Test scheduled visibility, checkout idempotency and stock/payment integrity with PostgreSQL and Redis. Exercise real browser interactions for guests, cashiers and authorized managers.
4. Record implementation, automated tests, browser tests, responsive tests and production verification separately per workflow. Identify unavailable provider credentials, deployed services and physical hardware explicitly. Record material differences from Figma and keep incomplete workflows open.

### Implementation evidence — 2026-11-18

The [approved Figma workflows](https://www.figma.com/design/6tzk8TqeeQMTAkWFZ45JP3) informed the Django management shell, separate QR Menu and Your Order, banner management, POS and receipt preview. See [Feature Status](FEATURE-STATUS.md) for verification and remaining acceptance criteria.

### Storefront controls and pickup acceptance

- Menu categories are actual catalog filters. Selected categories use a pressed state and wrap at narrow widths. Quantity controls show bounded increment and decrement actions in the menu, cart and POS. The cart preserves entered checkout instructions while refreshing totals from the server.
- The authenticated business shell places brand, business identity, selected location where present and the account disclosure in the same navigation surface. The account disclosure includes business switching, account security and sign-out.
- Local checkout acceptance covers location pickup, QR dine-in or takeaway, order replay, authenticated POS, kitchen preparation and completion, receipt and recipe stock consumption. A verified QRIS connection is selectable, and the order can enter preparation after provider confirmation.

## Product options

Catalog managers can create and edit reusable groups with product assignments, optional location restrictions, ordering and availability. A minimum of zero makes a group optional, and a maximum of one makes it single-select. Options can add a unit surcharge. Scheduled promotions apply to the product base price. Option surcharges are added after promotion pricing.

Guests and cashiers can place several configurations of the same product in a single order. Guests can revise a configuration in Your Order. The checkout service validates available choices and prices for the selected location. Kitchen tickets and receipts use the option names and unit surcharges recorded when the order was created. The base product still supplies recipe ingredients and station routing.

## Design freeze

The approved Figma workspace and storefront remain the design reference. Category filters, quantity controls and authenticated navigation retain the established controls and visual tokens. Future changes should address a verified usability, accessibility, functional or visual issue, or extend the existing system for a required feature. Business identity, location and account actions remain part of the authenticated workspace.

## Voucher management, pricing and reporting

Merchants use Promotions → Vouchers to configure a name, tenant-unique code, discount, business-local validity window, optional minimum and cap, total uses and scope. Tenant-unique codes are trimmed and uppercased. Optional product and category scopes match either, while location and order type must also match when configured. Per-customer limits require a saved customer selected in POS. Guest checkout supports anonymous vouchers.

Scheduled promotions apply to the product base price first. A permitted voucher discounts eligible product totals after those promotions. Modifier surcharges remain payable in full. The guest cart and POS show the confirmed discount and total before submission. Entering or previewing a code leaves usage available until the order commits.

The initial committed order consumes one use even if payment fails or expires, or an order is later cancelled. Payment retries use the saved net total. Historical orders and reprints retain the original discount and code. Reports use durable redemptions for usage and discount totals, and paid non-cancelled orders for net sales and average paid order value. Receipt and payment-provider acceptance require the relevant external hardware and merchant credentials.

## Supplier purchasing

Inventory links to supplier accounts and purchase orders. Authorized staff can find a supplier, record contact details, deactivate it, and review purchases within their permitted destinations. Supplier deactivation keeps history available and prevents new orders with that supplier.

Each purchase belongs to one business, supplier and destination location. A draft can be changed, submitted as ordered, or cancelled. A submitted purchase freezes quantities, names, units, unit costs, and calculated totals. An ordered purchase can receive multiple deliveries until all ordered stock is accepted. A partially received purchase can be cancelled with its accepted stock and receipt history intact.

Receiving records accepted, rejected and damaged quantities separately. Only accepted quantities reduce what is outstanding and add stock. Unit costs come from the purchase order. The inventory ledger links receipts to movements and shows the last accepted purchase cost by item and location. Earlier or manually adjusted stock can have an unknown purchase cost. The last purchase cost is a reference price and does not perform weighted-average, FIFO, or accounting valuation.

Use inventory.view for purchasing read access and inventory.manage as well for changes. Business owners can limit collaborator access to selected locations under Team access. Existing unrestricted access remains business-wide. Purchase and stock updates use authorized Channels streams backed by the transactional outbox.

## Soft Premium Veyra

The approved visual identity and page structure still govern the app. The shared geometry scale uses 6 px for compact states, 10 px for controls, 16 px for content surfaces and 20 px for raised surfaces. Cream and ivory separate content, while espresso keeps the sidebar and primary actions recognizable. Purchase lines and nested forms use separators and spacing. Operational tables retain their rows, column alignment, and labeled scrolling regions.

The shared styles define a 48 px control height, a small spacing scale, stronger field borders, and color tokens for readable text and success and error states. Native single selects use one chevron treatment. Promotions and vouchers use the same searchable multi-select with names, selected counts, keyboard operation and native form fallback. Dates and destructive confirmation retain platform behavior.

Public account screens share a compact layout with labeled fields, password visibility and actionable recovery states. Bound errors keep their field associations and focus the summary. Slow form submissions show status with an accessible announcement. Operational tables keep row structure, labeled scrolling regions and aligned amounts.

Native disclosures preserve keyboard access. Hover, pressed and focus feedback respond without delaying the action. The account popover uses a brief 150 ms entrance when motion is permitted. GSAP is loaded on the landing page. Its motion follows the browser preference, and the product preview shows the actual POS with an example catalog.

## Platform subscription billing

Business owners manage billing under Settings. Plans, renewal choices, invoice history and recovery remain accessible when operational entitlements expire or platform staff restrict a subscription. Owners and permitted team members keep their existing historical order, receipt and purchasing access. The starter trial defaults to 14 days and the grandfathered access policy continues. Platform staff can configure the duration or disable eligibility for future businesses through the starter-trial plan. Each new trial saves its terms and deadline at creation. Existing deadlines remain intact.

Paid plans use monthly whole-IDR prices and can be published by authorized platform staff. An invoice snapshots its terms and the local calendar-month period. The period starts when the initial invoice is issued or on the preceding paid period's end for renewals. Calendar-month ends clamp to the last available day of the destination month. A renewal uses the plan terms published when the invoice is issued. Owners see that invoice before opening checkout.

Plan changes and cancellation take effect at period end. A change applies after payment of the next invoice. Owners can resume renewal before the paid period ends. Unpaid renewals have seven days of operational grace and may restore access when settled within the invoice period. Expansion limits count active locations, employees, operational stations and catalog items. Existing records are preserved even when usage exceeds a new limit.

Hosted checkout uses Veyra's own platform Midtrans credentials. Verified provider status posts the invoice and activates its saved terms. Runtime scheduling advances trials, renewals, grace periods, cancellation and reconciliation without page visits. The current scope is monthly, manual-payment renewal with no automatic debit, immediate cancellation, proration, tax accounting, or email reminders.

## Public landing and account experience

The landing page introduces Veyra with a browser capture of the working POS and follows restaurant operations from order intake to station handoff and settlement. Product, Operations and Product tour links lead to their corresponding sections. Registration and signed-in entry lead to existing account routes. Product captures use an isolated sample catalog. The desktop tour scrubs with GSAP and ScrollTrigger. Compact screens, reduced-motion preferences and Data Saver keep the content usable through static images and chapters.

Sign-in, registration, password recovery, verification, invitation and authenticator challenge use a shared account layout. Forms use a separate column on wide screens and stack after the explanation on phones. Status pages give the result and the next action. Local return destinations survive navigation between sign-in and registration. Account security separates enrollment, one-time recovery codes and account connections. The enrollment QR must render in the session that started setup and carry private, no-store headers.

## Shared form and data presentation

Management and operational screens use the existing Soft Premium tokens for native controls, focus, errors and data tables. Relationship choices show business names for records and submit the original scoped identifiers. Short selections and permissions use native inputs. Longer product, category and location selections may use the searchable picker, with native selects available without JavaScript. Permissions and capabilities use plain-language labels for both the editor and saved display.

Media fields share a native file action, preview and feedback while server-side normalization remains authoritative. Payment settings show the fields for the selected provider, mask secrets, and keep the existing encrypted save flow. Browser error pages show the HTTP result and a safe way back. Data tables keep semantic headings, numeric alignment and labeled keyboard-scrollable regions.

## Pre-deployment acceptance — payments, inventory and ordering media

- Owners can independently add, replace or remove the restaurant logo next to the ordering-page name. Guest menu and cart continue to use the signed storefront and receive changes through Channels.
- Permanent offers bypass calendar dates. Previously saved dates are retained for later editing. Weekday and recurring time controls continue to apply independently.
- A refund is a separate record tied to the original capture. Owners can record cash already returned or a card-terminal refund reference. Midtrans QRIS requests remain reserved until authoritative provider evidence confirms or denies the matching refund. Confirmed totals are visible on payment history, receipt controls and guest tracking. Manual card records document a completed terminal action. Refunds and operational cancellation preserve voucher redemption.
- Paid order cancellation changes fulfillment and inventory. It never initiates a refund. Pending Midtrans QRIS must have a verified terminal cancellation, denial or expiry before staff can cancel the order. Other pending QRIS attempts require provider confirmation. Whole-rupiah amounts are required for Midtrans QRIS.
- An inventory manager reconciles the quantity at an authorized location by recording the expected amount, physical amount and reason. A nonzero variance appends a movement. Supplier returns record hand-back after delivery at the original receiving location and original purchase cost. They do not reopen the purchase or issue a supplier credit.
- Current stock cost estimates equal current quantity multiplied by the last accepted unit cost. Historical receipt and supplier return costs retain their original amounts. Accounting valuation requires a separate costing and reconciliation policy.
