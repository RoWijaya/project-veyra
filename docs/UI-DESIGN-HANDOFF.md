# Veyra UI design handoff

## Design direction

The public page uses an editorial headline beside a captured POS view. The first viewport introduces the product, names its audience, shows the interface, and gives account and product entry points. The service overview links that view to ordering, preparation, and stock. The tour keeps its chapters in document flow and uses the desktop film for context.

Instrument Serif is reserved for large public and account headings. Management controls and data use the existing sans-serif stack. Ivory, espresso, and the existing semantic feedback colors carry across public pages and the application. Buttons, text fields, menus, and panels share the existing control radius. Surface corners increase only where the grouping needs it. [Instrument Serif source and license](https://github.com/Instrument/instrument-serif)

Account routes use an aligned two-column layout on desktop and one column on small screens. Related action and history panels have visual separation. Metrics share one summary surface. Table scroll belongs inside named regions that keyboard users can focus.

## Figma

Work is in the existing Veyra design file. The prior design pages and approved frames were retained. The updated designs are editable Auto Layout frames. Restaurant artwork and POS captures are image fills.

- [Public landing desktop](https://www.figma.com/design/6tzk8TqeeQMTAkWFZ45JP3?node-id=9-13) and [mobile](https://www.figma.com/design/6tzk8TqeeQMTAkWFZ45JP3?node-id=9-52)
- [Sign in](https://www.figma.com/design/6tzk8TqeeQMTAkWFZ45JP3?node-id=9-85) and [registration](https://www.figma.com/design/6tzk8TqeeQMTAkWFZ45JP3?node-id=9-105), with phone frames on the same page
- [Overview](https://www.figma.com/design/6tzk8TqeeQMTAkWFZ45JP3?node-id=13-133), [catalog](https://www.figma.com/design/6tzk8TqeeQMTAkWFZ45JP3?node-id=13-197), [POS](https://www.figma.com/design/6tzk8TqeeQMTAkWFZ45JP3?node-id=13-292), [guest menu](https://www.figma.com/design/6tzk8TqeeQMTAkWFZ45JP3?node-id=13-569), [inventory](https://www.figma.com/design/6tzk8TqeeQMTAkWFZ45JP3?node-id=13-24), and [promotions](https://www.figma.com/design/6tzk8TqeeQMTAkWFZ45JP3?node-id=13-404), each with a compact version
- [Component library](https://www.figma.com/design/6tzk8TqeeQMTAkWFZ45JP3?node-id=0-1) adds Select, Searchable picker, Empty state, Data row, Status feedback, Confirmation dialog, and default/focus/error field states. Existing colors and core control sizing map to CSS. Surface and elevated radii, display styles, preview shadow, and responsive landing grids are defined in Figma.

The native frames describe composition and reusable control states. Browser output has dynamic copy, real permissions, and actual data. The Figma set does not cover every runtime error, permission, or long-content combination.

## Django implementation

- Public composition and motion changed in `home.html`, `landing.css`, `site.css`, and `site.js`. Display fonts ship locally with their license.
- `account.css` applies the shared account form and status layout to sign-in, registration, verification, reset, invitations, and authenticator screens.
- `business_navigation.html`, `dashboard.html`, and `app-ui.css` put business switching near workspace identity and turn dashboard metrics into one clearly divided surface.
- `pos.html` links directly to the current order and recent orders. Order history appears with the permission required to open it. `customer-ui.css` tightens the guest menu, filters, image and checkout proportions. Server-rendered prices retain their existing format.
- `promotions.html` groups amount and discount type, puts campaign links together, and announces weekday changes. Inventory and purchasing share mobile-friendly surface widths. `purchasing.js` discards fetch results once the user leaves a view.
- Named routes, backend forms, pricing, payments, tenant checks, Google sign-in, and WebSocket routing continue to use the existing application contracts.

## Review boundaries

The Standards review led to fixes for the dashboard cascade, old tour styling, and inconsistent guest price formatting introduced in the draft. The Spec review led to a scoped POS order-history link, visible workspace switching, and promotion spacing and feedback fixes. Legacy exact promotion timestamps remain a behavior question for the separate feature audit. A full screen-reader session and complete state-by-state Figma parity remain to be verified.

## Verification on 2026-11-24

| Check | Evidence |
|---|---|
| Baseline and final regression suite | 206 tests passed before the redesign and again after it. Both runs used PostgreSQL 17 and Redis 7. Django system checks, migration drift checks, JavaScript syntax and Git whitespace checks passed. |
| Before and after visual audit | The baseline Chrome matrix covered 30 routes at nine widths. Before and after images were captured at 390 and 1440 pixels. The final contact sheet includes the landing, sign-in, overview, POS, stock count and guest menu at all nine widths. The final landing and overview were compared with their native Figma frames. |
| Responsive rendering | Chrome, Firefox and Playwright WebKit each passed 288 checks across 32 routes at 320, 375, 390, 768, 834, 1024, 1200, 1440 and 1600 CSS pixels. The final checks detected no page-level horizontal overflow, uncaught exceptions, console errors, unexpected failed requests or local HTTP errors. Navigation cancellations were excluded. |
| Account and navigation | Chrome exercised mobile keyboard focus, Enter and Escape, section anchors, password visibility, registration through the verification prompt, authenticated navigation and switching back into the synthetic business. |
| Catalog, offers and POS | Chrome created and edited a category and product, repriced an open guest menu on a promotion edit, previewed and redeemed a voucher, created a POS order, recorded payment and verified that the paid receipt action appeared. Keyboard toggles and weekday selection were verified. |
| Guest orders and inventory | Chrome, Firefox and WebKit exercised ordering-logo changes on an already-open QR menu, guest checkout, cash tender and live partial refunds, physical stock counts, live cost estimates and supplier returns. Chrome also received a delivery to completion. |
| Landing motion | Chrome verified GSAP and ScrollTrigger, video seeking during normal scroll, absence of pinned sections, no video source on reduced motion and a measured layout-shift score below 0.1 in the tested desktop session. |

The route matrix covered the landing, sign-in, registration, verification resend, password reset request, guest menu and order, workspace overview, catalog, categories, ordering-page settings, promotions, vouchers, inventory, suppliers, purchase list, new purchase, purchase details, order history, stations and devices, sales reports, billing, settings, team, locations, tables, POS, account security, business onboarding, stock count, receipt and refunds. Local browser verification used synthetic tenants. The before visual matrix ran against a synthetic SQLite fixture while the PostgreSQL/Redis baseline and final regression tests used the production database engine. After verification used the local ASGI app with PostgreSQL, Redis and a running outbox worker.

Pending visual acceptance includes screen-reader testing, physical Safari and mobile-device checks, and long-content and permission-specific states beyond the sampled routes. Live Google consent, email delivery, merchant settlement and physical printing retain their existing deployment verification requirements.

## Product polish on 2026-11-25

| Area | Changes |
| --- | --- |
| Public site and account | The hero uses a new capture from the real POS. Product, preparation, and inventory shortcuts point into the tour. Related account pages share the public display type. The film stays in normal document flow. |
| Management and catalog | Shared gutters, breadcrumbs, section headings, controls, and panels make catalog, offers, team, locations, and settings easier to scan. The phone overview pairs metric labels with right-aligned totals in one compact panel. The workspace header puts business identity and navigation first. |
| POS and guest ordering | The POS keeps order type, location, and catalog search near the top. The table field appears for dine-in. Active filters can be reset without clearing selected items. Guest categories share a horizontal row at phone widths and follow keyboard focus. |
| Ordering Page Builder | The live preview stays in view when the desktop viewport has room and remains in document flow on shorter viewports. The editor and preview have separate jump links on phones. Draft saving and the owner confirmation dialog retain their existing behavior. |
| Stock and finance | The count shows expected stock above the entry field. Report filters sit above the summaries, with payments retained and confirmed refunds shown separately. Billing places plan and usage together on wider screens. |

The existing Figma file has refinements in ten desktop and phone frames covering the landing, management overview, POS, guest menu, and builder. The updates use native Auto Layout and shared components for primary and secondary actions, fields, status, data rows, and confirmations. The new section-heading style and [workspace surface reference](https://www.figma.com/design/6tzk8TqeeQMTAkWFZ45JP3?node-id=35-89) carry those patterns into reports and inventory. The POS capture is an image fill. The native references were compared with the browser at phone and desktop widths for layout order, type hierarchy, and control grouping. The app includes role, data, and error states beyond the representative frames.

Priority Figma frames include [landing](https://www.figma.com/design/6tzk8TqeeQMTAkWFZ45JP3?node-id=9-13), [phone overview](https://www.figma.com/design/6tzk8TqeeQMTAkWFZ45JP3?node-id=13-165), [phone POS](https://www.figma.com/design/6tzk8TqeeQMTAkWFZ45JP3?node-id=13-320), [phone guest menu](https://www.figma.com/design/6tzk8TqeeQMTAkWFZ45JP3?node-id=13-569), and [phone builder](https://www.figma.com/design/6tzk8TqeeQMTAkWFZ45JP3?node-id=30-678).

### Verification

- The baseline and final suites each passed 215 tests against PostgreSQL 17 and Redis 7. The landing navigation assertion now covers all three new tour shortcuts. Django system checks, migration drift, JavaScript syntax, and Git whitespace checks passed. The polish pass required no migrations.
- Chrome, Firefox, and Playwright WebKit each checked 38 routes at 320, 375, 430, 768, 834, 1024, 1200, and 1440 CSS pixels. All 912 settled route and width combinations returned successfully without page-level horizontal overflow or console errors. Live pages finished joining their stream before the harness left the route. Before-and-after Chrome captures cover 36 shared routes at 430 and 1440 pixels.
- All three engines exercised keyboard navigation, password visibility, POS order-type changes, filter reset and selected quantities, phone category scrolling, private builder previews at all three device sizes, report filters, live billing, and the scroll region for stock-count history.
- Chrome confirmed that invalid sign-in exposes a visible alert and that the password-reset link is keyboard accessible. It also verified GSAP and ScrollTrigger film scrubbing, reduced-motion fallback, draft saving, Escape and focus restoration in the publish dialog, and a published change arriving on an already-open guest menu. Independent checks verified signed QR checkout, cash tender, receipt availability, a live partial refund, and a kitchen ticket advancing to Ready from the keyboard and updating the guest’s status. Workflow writes used a local synthetic tenant.
- In Chrome, 11 sampled foreground and background pairs exceeded 4.5:1. The sampled POS and guest category controls were at least 44 CSS pixels tall. The keyboard focus sample had a visible 3 px outline. These are targeted browser checks. Assistive technology and physical-device testing, including Safari on Apple hardware, remain open. Hosted providers and physical printers need their existing production acceptance.

### Source files

- Shared and workflow styling lives in `core/static/core/site.css`, `app-ui.css`, `account.css`, `landing.css`, `customer-ui.css`, and `ordering-builder.css`. POS filter behavior lives in `core/static/core/ordering-ui.js`.
- The templates are `templates/core/home.html`, `business_navigation.html`, `dashboard.html`, `pos.html`, `reports.html`, `billing.html`, and `stock_count.html`. The captured hero image is `core/static/core/landing/veyra-pos-example.png`. The landing regression assertions are in `core/test_public_experience.py`.
