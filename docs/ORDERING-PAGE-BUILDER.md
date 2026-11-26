# Ordering Page Builder

Business owners open **Settings → Ordering page** to customize the menu used by existing table QR and takeaway links. Branding, cover positioning and captions, color presets, layout, product and category presentation, and public restaurant information share one editor. Classic uses a side-by-side menu item with an optional thumbnail. Editorial emphasizes product photos, while Compact uses a single, denser list.

1. Choose a preview location and switch between desktop, tablet, and mobile. The private preview reflects the current fields and selected uploads.
2. Save the draft. Guests continue to see the current published design.
3. Review the saved draft, then choose **Publish** and confirm. The existing guest menu stream updates open menus after the transaction commits.
4. Restore a version from **Publication history** to create an editable draft. Publish again to make it live. **Unpublish design** returns guests to the standard menu. Table and takeaway links keep working.

The color controls validate a minimum 4.5:1 contrast for text, secondary text, and accents on the selected background. The accent text color is derived from the chosen accent. A cover can use a solid caption or a dark overlay. The normalized banner keeps its full image so the focal position can change without a replacement. Restaurant details appear only when public display is enabled. Opening information is a free-text note.

## Django integration

- Migration `0037_ordering_page_builder` adds publication metadata to `OrderingPage` and creates `OrderingPageSnapshot`. The existing branding fields and media stay available on the published record. The migration defaults existing ordering pages to published. It adds schema without changing their existing values.
- A snapshot has no number while it is the draft. A database constraint allows one draft per page. Publication promotes that draft and advances the active publication number in the same transaction. Public menus, carts, logos and banners read the tenant's active numbered snapshot. The publish transaction maintains the older fields for the archive worker. A per-business lock and submitted revision protect simultaneous saves and publication. An unmatched revision returns HTTP 409. An original design from before the migration remains readable until it is first superseded or unpublished.
- `ordering_forms.py` validates presentation values and resolves reference IDs inside the current business. The editor uses actual catalog names and stores referenced IDs and presentation preferences. The guest renderer still prices products and resolves modifiers through the existing services. Hidden products and categories are removed from menu browsing and new guest additions, while an already-open cart retains its prior items subject to the normal availability and price checks.
- `ordering_views.py` uses the existing editor URL for viewing and saving drafts and for verified POST actions. The new `preview/` route renders the same guest template with private, ephemeral data. The new `preview/products/<uuid>/` route serves authorized product photos in the preview. The existing editor logo and banner routes serve the current draft. Public signed menu, cart, image, and order-status URLs are retained.
- The canonical owner can publish, unpublish, and restore. An active member with `ordering_page.view` can read the builder and its saved preview. An active member with `ordering_page.edit` can also save drafts and preview unsaved changes. The editor checks the existing QR ordering entitlement on writes. Workspace selection and catalog permissions grant no builder access.
- Existing Pillow validation decodes JPEG, PNG, and WebP, rejects animated and oversized images, and normalizes uploads to WebP. PostgreSQL stores the normalized bytes for drafts, live serving, and history. The existing outbox archives published images to the private tenant-prefixed Supabase bucket when configured. The service-role key remains server-side.
- The preview is authenticated and same-origin. Its response policy sandboxes it and blocks checkout submissions. A per-response nonce authorizes the private preview stylesheet. Public menus and carts load the same declarations through a versioned same-origin stylesheet. Guest updates use the PostgreSQL outbox and Redis Channels stream to refresh the content and stylesheet link.

## Design and implementation files

The existing Figma file contains the desktop and mobile editors, section states, preview widths, publishing confirmation, empty and error states, and layout specimens. [Builder overview](https://www.figma.com/design/6tzk8TqeeQMTAkWFZ45JP3?node-id=30-13) · [Mobile editor](https://www.figma.com/design/6tzk8TqeeQMTAkWFZ45JP3?node-id=30-678) · [Editorial](https://www.figma.com/design/6tzk8TqeeQMTAkWFZ45JP3?node-id=31-138) · [Compact](https://www.figma.com/design/6tzk8TqeeQMTAkWFZ45JP3?node-id=31-163)

The main implementation is in `core/ordering_page.py`, `core/ordering_forms.py`, `core/ordering_views.py`, `templates/core/ordering_page_settings.html`, the two ordering builder template includes, and the `ordering-builder.css`, `ordering-builder.js`, and `ordering-storefront.css` assets. The shared guest templates and client apply the published design and update an already-open menu.


## Functional audit and repair, 2026-11-25

Chrome reproduced the color defect in the private iframe. A selected Secondary text value of #405770 saved correctly, but category labels still rendered as #625A51. The shared field-hint rule specified a literal color, bypassing the storefront's muted token. Accent, Background, and Main text reached their primary surfaces. The storefront stylesheet now applies Secondary text to category and form hints and uses the selected foreground, background, and border colors for checkout and quantity controls. The repaired category label renders as rgb(64, 87, 112).

The editor shows each selected hex value, displays the first preview validation issue beside the iframe, and warns before leaving with unsaved edits. After an invalid save, text and other form values remain visible, and a valid selected image is explicitly marked for reselection when the browser clears the file input. Until validation succeeds, the private preview displays the saved design. Preview readiness follows the latest requested render, including after rapid edits and after a mobile save. [HTTP regressions](../core/test_ordering_builder.py) · [Rendered browser regression](../tests/browser/ordering-builder.cjs)

### Control inventory

Saved means the value survives Save draft and reopening the builder. Live means the appearance after an explicit Publish. A restored version starts as a draft.

| Controls | Expected and observed behavior | Private preview | Saved | Live | Mobile and regression |
| --- | --- | --- | --- | --- | --- |
| Display name, Tagline, Description | **Working.** Set the brand and menu introduction. Blank name uses the business name. | Yes | Yes | Both links | Browser and HTTP |
| Restaurant logo, Remove current logo | **Working.** Upload, replace and remove. A published design without a logo uses a monogram. | Yes | WebP bytes | Both links | Browser and HTTP |
| Banner image, Remove current banner | **Working.** Upload, replace and remove independently of the logo. A caption can remain without the image. | Yes | WebP bytes | Both links | Browser and HTTP |
| Banner image description | **Working.** Sets alt text. A blank value marks a decorative image. | Yes | Yes | Yes | Browser and HTTP |
| Horizontal and Vertical focal point | **Working.** Control the visible crop from 0 to 100 percent per axis. | Computed crop | Yes | Yes | Browser and HTTP |
| Banner title, Banner description, Caption style | **Working.** Display text below the image or as a dark overlay. | Yes | Yes | Yes | Browser and HTTP |
| Accent color | **Working.** Themes the header, primary buttons, selected categories and links. Contrast determines accent foreground. | Computed color | Yes | Both links | Browser and HTTP |
| Page background | **Working.** Themes the page and neutral controls. | Computed color | Yes | Both links | Browser and HTTP |
| Main text | **Working.** Themes titles, product copy and themed form fields. | Computed color | Yes | Both links | Browser and HTTP |
| Secondary text | **Working after repair.** Themes category labels, hints and supporting copy. | Computed color | Yes | Both links | Browser and HTTP |
| Color preset and hex readouts | **Working.** Warm ivory, Olive and cream, and Deep plum set the four pickers. Custom edits update the readouts. | Yes | Stores the palette | Applies the palette | Browser |
| Rounded and Pill buttons | **Working.** Set the visible button radius. | Computed CSS | Yes | Yes | Browser |
| Divided, Outlined and Soft shadow cards | **Working.** Set border or elevation treatment. | Computed CSS | Yes | Yes | Browser |
| Classic, Editorial and Compact layouts | **Working.** Switch card structure, photo presentation and catalog density. | Shared CSS | Yes | Yes | Browser and HTTP |
| Category Move up/down and Hide | **Working.** Reorders navigation. Hidden categories remove products from browsing and new guest additions. | Yes | Yes | Yes | Browser and HTTP |
| Product Move up/down, Hide and Feature | **Working.** Reorders the featured and regular groups, hides products from browsing, and highlights featured products. | Yes | Yes | Yes | Browser and HTTP |
| Find categories and Find products | **Working.** Filters editor rows and their move controls. | Editor only | Session only | Editor only | Browser |
| Show product images and Show product descriptions | **Working.** Each toggle controls its respective catalog content. | Yes | Yes | Yes | Browser and HTTP |
| Show this information on the public menu | **Working.** Controls visibility of the contact panel. | Yes | Yes | Yes | Browser and HTTP |
| Public address, Phone number and Opening information | **Working.** Display public details when enabled. Phone uses a telephone link. | Yes | Yes | Yes | Browser and HTTP |
| Website, Instagram and Facebook | **Working.** Render validated public web links when enabled. | Yes | Yes | Yes | Browser and HTTP |
| Preview guest menu, Preview location, Desktop, Tablet and Mobile | **Working.** Render the shared customer page. Device widths are 1120, 768 and 390 CSS pixels. The chosen location scopes the preview catalog. | Yes | Preview-only controls | Current design | Browser and HTTP |
| Save draft, Publish, Unpublish design, Restore as draft | **Working.** Drafts stay private. Published changes reach both existing signed links. Owner and revision checks apply. | Yes | Yes | Explicit publication | Browser and HTTP |

Product prices, stock, operational availability, descriptions and modifier choices continue to come from the authoritative catalog. The builder has no separate Primary color, typography picker or independent density control. Layout controls density. Campaign banners remain managed in the campaign editor. Hosted Supabase archival and delivery require external verification.

### Repairs and regression coverage

| Repair | Implementation | Regression coverage |
| --- | --- | --- |
| Selected colors on rendered labels and controls | Storefront palette rules in ordering-storefront.css and picker values in ordering_views.py and the shared field template | Browser computed colors for all four inputs and the presets. HTTP verifies equivalent theme declarations in preview, live QR and takeaway, the live refresh response, and restored history. |
| Preview validation and mobile readiness | ordering-builder.js and the builder and customer menu templates report validation beside the frame. The saved preview starts loading after navigation, ignores status from older preview responses, and gives the location picker an explicit label. | Real Chromium, Firefox and WebKit form interactions. Builder HTTP tests verify preview isolation, location-scoped modifiers, validation, and role checks. |
| Unsaved edits and upload recovery | The editor marks invalid submitted values as unsaved, warns before navigation, hides the publish confirmation until the draft is saved, and tells users when a valid selected file needs to be chosen again. | Browser navigation and validation checks. Builder HTTP tests verify that a rejected save preserves entered values and the saved/public versions. |
| Media, catalog and publication safeguards | Existing Django snapshot, pricing and permission services remain the authority. No schema or data migration was required. | Both media files pass upload, replacement, removal and restoration. Two independent businesses retain separate published palettes. Tests reject unauthorized preview and restoration, preserve authoritative checkout, and enforce draft revisions. |

The repair changes the builder view, the shared customer and editor templates, and the scoped builder and storefront assets. Regression files are core/test_ordering_builder.py, tests/browser/ordering-builder.cjs, and tests/browser/serve_ordering_builder.py. The feature inventory and general testing notes link back to this audit.

### Reproduce the browser regression

Use a disposable PostgreSQL name ending in _browser and a local Redis connection. The fixture runner applies migrations to Django's separate test database, seeds two businesses per engine, starts the local ASGI service and outbox worker, and disables hosted media archival. Its fixture JSON contains generated local login details. At shutdown the runner records only public snapshots and media checksums in publications.json, then removes the login fixture and disposable database.

```powershell
$env:PYTHONPATH = (Get-Location).Path
$env:DATABASE_URL = 'postgresql://veyra:veyra@127.0.0.1:5432/veyra_builder_example_browser'
$env:REDIS_URL = 'redis://127.0.0.1:6379/12'
$env:DJANGO_DEBUG = 'true'
$env:DJANGO_SECRET_KEY = ([guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N'))
$env:DJANGO_ALLOWED_HOSTS = '127.0.0.1,localhost'
.\.venv\Scripts\python.exe tests/browser/serve_ordering_builder.py .scratch/builder-browser
```

From a second shell with Playwright Core and the browser engines available, run the checked-in browser regression once per fixture engine. Set VEYRA_PLAYWRIGHT_MODULE to the module path when using a standalone Playwright installation. Set VEYRA_CHROME_EXECUTABLE when testing installed Chrome. Create the audit-finish marker after the runs, or interrupt the fixture runner cleanly.

```powershell
node tests/browser/ordering-builder.cjs chrome .scratch/builder-browser/audit-browser-fixture.json
node tests/browser/ordering-builder.cjs firefox .scratch/builder-browser/audit-browser-fixture.json
node tests/browser/ordering-builder.cjs webkit .scratch/builder-browser/audit-browser-fixture.json
New-Item -ItemType File .scratch/builder-browser/audit-finish
```

## Public publication repair, 2026-11-26

### Reproduction

Chrome reproduced the reported preview-to-public discrepancy with a saved and published test design. The preview and a direct connection to local Django displayed all four custom colors. With an independent content security policy of style-src 'self' added only to the guest HTML response, the guest browser rejected the inline theme stylesheet on both the first anonymous visit and reload. All four custom CSS properties were empty even though the database contained the published values. The earlier browser regression passed against the application's own policy, which allowed the inline nonce. It also observed publication mainly through guest pages that were already open. The new regression covers a fresh context under both independently enforced policies. The policy added in the test models an intermediary. The hosted site's headers and cache were unavailable for inspection.

A separate regression confirmed that guest rendering and ordering media could use the duplicated fields on OrderingPage even when the active numbered snapshot had different values. That left the guest result dependent on the duplicate staying synchronized. The public request now selects the active numbered snapshot scoped to the business in the signed link. Older published pages without a version still use their existing fields. A draft, including a restored version, stays private until the owner publishes it. In the same locked transaction, the saved draft becomes a numbered snapshot, the active version advances, and a menu change is queued for open guests. Unpublish removes the custom design from public rendering. The existing legacy projection remains synchronized for archival.

### Rendering and cache behavior

The preview and guest page share palette declarations and the interpretation of the layout, branding, media, catalog selections and public information. The guest menu and cart link to a same-origin CSS resource resolved from the signed guest link. The URL contains a digest of the CSS declarations. A change to the palette or focal point gives the stylesheet a new URL. A matching URL is privately cacheable for five minutes. If publication changes between the HTML and CSS requests, an old URL returns the current stylesheet with private, no-store so the result cannot persist under the old key. Signed guest HTML remains private, no-store because it contains session-specific cart state. Logo and banner responses retain their private, no-store behavior and their URLs change with the published snapshot. An already-open menu receives the versioned stylesheet URL through the existing Redis-backed refresh flow.

The builder offers four colors. Accent colors the header and primary action. Page background, Main text and Secondary text color their respective surfaces. Typography comes from the shared storefront styles. Layout changes the density and presentation.

### Verified behavior

Chrome, Firefox and Playwright WebKit each saved and reopened the editor, verified the private preview, published from Management, and rendered the signed table and takeaway menus in a fresh anonymous context. First load and reload matched the stored publication under the additional stylesheet policy. The builder and guest menu fit at 320, 375, 768, 1024 and 1440 CSS pixels. The regression checked the saved layout, button shape, card treatment, banner crop, logo, cover, branding, featured and hidden items, category order, descriptions and public information. It also checked a previously opened guest session, replacement and removal of both media assets, unpublish, restore and counter checkout on both signed links.

The table lists properties measured on the guest page after publication and reload. The restore produced the same measurements.

| Builder control | Published value | Guest computed value |
| --- | --- | --- |
| Accent and primary action | #105140 | rgb(16, 81, 64) |
| Page background | #E5EFFA | rgb(229, 239, 250) |
| Main text | #133454 | rgb(19, 52, 84) |
| Secondary text | #405770 | rgb(64, 87, 112) |

The restored publication is version 4, with the same settings and media checksums as version 1. The other tenant stayed on its own version 1 with a burgundy accent and cream background through every publication, removal and restoration. The reproducible fixture writes the published settings and logo and cover digests to publications.json. The browser regression writes preview, public and restored screenshots and computed values for each engine.

The full PostgreSQL and Redis suite passed 253 tests, with 33 in the focused builder, cart and catalog run. One new HTTP test proves that the guest sees the active snapshot even when the duplicated fields differ, and that the new CSS version, stale URL handling and unpublish behave as expected. The expanded HTTP and browser checks cover the normal signed routes. Hosted proxy behavior, CDN behavior and hosted Supabase delivery remain unverified. [Testing evidence](TESTING.md#ordering-page-builder-publication-regression)

### Files changed

| Area | Files |
| --- | --- |
| Snapshot selection and publication | core/ordering_page.py, core/ordering_views.py |
| Guest and legacy routes, theme CSS delivery and public media | core/customer_views.py, core/views.py, core/urls.py, core/media_services.py, core/media_views.py |
| Shared rendering and live client | templates/core/customer_base.html, templates/core/customer_menu.html, templates/core/storefront_theme.css, core/static/core/customer-ui.js |
| Regression and reproducible browser evidence | core/test_ordering_builder.py, tests/browser/ordering-builder.cjs, tests/browser/serve_ordering_builder.py |
| Documentation | docs/ORDERING-PAGE-BUILDER.md, docs/TESTING.md |

No migration was required. The repair and PostgreSQL verification used separate disposable databases.

## Verification and limits

The functional audit confirmed 231 tests before the change and 234 passing after it on PostgreSQL and Redis. The 26-case builder, catalog, and storage integration run passed. Django checks and migration drift checks passed. Separate builder HTTP requests accepted JPEG, PNG and WebP, served normalized WebP, and rejected oversized and animated uploads. Chrome, Firefox, and Playwright WebKit each passed the rendered regression without uncaught page errors. It exercises computed colors in light and dark combinations, real media editing, all three preview sizes, browser widths from 320 to 1440 CSS pixels, publication to already-open QR and takeaway menus, restoration, and counter checkout through both original links. The historical implementation checks below were recorded on 2026-11-24. A later browser polish pass verified keyboard confirmation, preview widths and publishing to an already-open guest menu. [Current test evidence](TESTING.md) · [UI handoff](UI-DESIGN-HANDOFF.md)

- **Automated.** All 206 baseline tests passed on PostgreSQL 17 and Redis 7 before this change. All 215 passed after implementation. Nine new HTTP tests cover private and unsaved preview, tenant and role boundaries, contrast and references, media retention, publication history, visibility, dine-in ordering, location-scoped modifiers, public information, and simultaneous saves. Existing branding and storage tests now use the explicit save-and-publish flow. Django checks, migration drift, focused Ruff checks, JavaScript syntax, and Git whitespace checks passed.
- **Database.** Migration `0037` passed on the isolated QA database and was applied to the local development PostgreSQL database together with its pending schema dependencies. Existing business, product, order and storefront counts matched before and after, as did a checksum of the legacy storefront text and banner contents.
- **Browser.** Chrome, Firefox, and Playwright WebKit each exercised unsaved branding and media previews, keyboard dismissal of the confirmation, save, publish, live logo and theme updates on an already-open guest menu, dine-in and takeaway checkout, unpublish, and restoration. Each passed 35 responsive checks across 320, 375, 390, 768, 834, 1024, 1200, and 1440 CSS pixels and the three preview device settings. Chrome also verified mobile navigation back from the preview followed by a draft save and visible confirmation. Intentional navigation cancellations are excluded from browser errors.
- **Acceptance still open.** The browser runs used synthetic local tenants and counter payment. The Supabase transport test used controlled provider responses. Live merchant QRIS, hosted Supabase credentials and storage delivery, and screen-reader and physical-device acceptance remain open. Campaign banner preview remains in the separate campaign editor.
