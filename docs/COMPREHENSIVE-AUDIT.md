# Veyra comprehensive audit

Audit date 2026-11-25. Checkout ``update`` at ``698ea190a7c799821d5c46bd0502893d5dd2194d`` plus the existing working-tree changes. The commit alone does not identify all code inspected. This report records the investigation. Subsequent High-priority remediation and its verification limits are tracked separately. [Remediation roadmap](AUDIT-REMEDIATION.md) · [Product inventory](FEATURE-STATUS.md)

## Executive assessment

Veyra's core local workflows are usable. The full existing suite and responsive browser matrix passed on fresh fixture databases. Separate audit checks found defects in counter-order closure, reporting, provider timestamps, registration failure handling, print acknowledgements, and real-time recovery. Source review found additional defects at tenant payment and station boundaries. These affect the reliability of a merchant pilot even though the current local suite is green.

No Critical issue was confirmed within the assessment. The highest-priority work is to enforce a single eligible collection path, preserve provider transaction identity through retries, enforce station location scope, and reconcile the completion and tender states. The rebuilt Linux image also contains dependencies flagged by public advisories. Live money movement and public launch acceptance remain open.

[Verification](#verification) · [Feature coverage](#feature-coverage) · [High findings](#high-findings) · [Medium findings](#medium-findings) · [Limits](#external-acceptance-and-unassessed-scope)

| Severity | Confirmed | Potential | Total |
| --- | ---: | ---: | ---: |
| Critical | 0 | 0 | 0 |
| High | 6 | 1 | 7 |
| Medium | 8 | 0 | 8 |
| Low | 0 | 0 | 0 |
| Informational | 1 | 0 | 1 |
| **Total** | **15** | **1** | **16** |

Fourteen confirmed findings are defects or observed configuration weaknesses. One confirmed item describes the remaining operational readiness gap. Confirmed source findings identify a code or configuration mismatch. They do not imply that hosted failure, data disclosure or money movement was observed. Severity for High findings is explained separately.

## Verification

Tests used disposable PostgreSQL schemas, loopback ASGI, synthetic users and records, and Redis on the existing local service. External provider tests used controlled transport. Existing checkout data, application code and migrations were outside the change scope. The current architecture is the server-rendered Django monolith and authorized Channels consumers described in [architecture](ARCHITECTURE.md).

| Verification | Result and scope |
| --- | --- |
| Baseline | **215 discovered, executed and passed**, with 0 failed, skipped or blocked within the suite. The run took 335.519 s on Python 3.14.7, Django 6.1.3, PostgreSQL 17.12 and Redis 7.4.12. |
| Declared dependency rerun | **215 passed again**, 0 failed or skipped, in 285.528 s. The clean Windows environment resolved cryptography `46.0.7`, passed `pip check`, and imported application source from the audited checkout. The repeated run verifies compatibility without doubling the unique test count. |
| Additional correctness assertions | **7 distinct audit assertions** reached their intended condition and failed. Five failures were observed in the initial run and two in the focused follow-up. An invalid synthetic print-job ID caused a harness error on its first execution, corrected before recording that result. These checks are separate from the baseline and do not increase its test count. |
| Schema and runtime | Django system checks, migration drift and applied migration checks against the disposable database, Compose parsing, and Channels group delivery passed. The production-mode Django check reported HSTS preload advisory ``security.W021``. |
| Responsive matrix | **912 checks** across 38 routes and Chrome 157, Playwright Firefox 159 and Playwright WebKit 27.2. Each engine checked 320, 375, 430, 768, 834, 1024, 1200 and 1440 CSS pixels. Every checked route returned 200 after settling. No page-level horizontal overflow or unexpected client errors were recorded. Route coverage establishes rendering and layout, with interaction coverage below. |
| Browser interactions | All three engines exercised keyboard navigation, private builder previews, table and takeaway entry, POS filter/selection state, reports, billing and count keyboard access. Chrome exercised live owner publication, signed guest table checkout, cash, receipt access, live partial refund and a kitchen queue change reaching guest tracking. Its 11 sampled text pairs exceeded 4.5:1, sampled ordering controls met 44 CSS pixels in height, and the focus outline measured 3 px. The reduced-motion case and active GSAP film were checked. A second partial delivery updated a separate inventory tab from 15 to 17 without reload. Chromium generated receipt output at 163.92 and 227.04 points, corresponding to the 58 and 80 mm layouts. |
| Local deployment and recovery | Rebuilt the Python 3.12.16 release image with manifest digest `sha256:6c738eb90d9ed771d7a5271d348833076773f9e5de0845b081ff89b934c510b6`. A PostgreSQL 17 custom-format dump streamed from the disposable browser database to a separate network-isolated restore container. Both reported 55 migrations, 3 businesses, 12 products, 3 purchase receipts and 45.000 total stock units. The non-debug release container returned 200 for readiness, liveness and the landing page using a local forwarded HTTPS header, emitted HSTS and served manifest CSS. No hosted service was deployed. |
| Dependency advisory check | OSV checked 78 installed Windows dependency or frontend package versions and returned zero matches. The Linux release query covered 38 versions and flagged cryptography `46.0.7` and pip `25.0.1`. The original Windows environment used cryptography `50.0.2` outside the declared range, explaining why its scan is not representative of the release. See finding 014. This is an exact-version snapshot. The OS, browser binaries, images and future dependency resolutions have separate risk. [OSV batch API](https://google.github.io/osv.dev/post-v1-querybatch/) · [Django security archive](https://docs.djangoproject.com/en/6.1/releases/security/) |

Representative existing coverage included scoped HTTP and ASGI access, failed/replayed identity and payment evidence, file validation, database constraints, and concurrent checkout, voucher, purchase, count, refund and builder actions. The audit used those existing defensive assertions and source review for authorization and identity findings. Security-sensitive findings give the control to repair and the required negative regression, without executable bypass procedures.

## Feature coverage

| Result across the 16 brief areas | Count |
| --- | ---: |
| Locally verified within the exercises below | 7 |
| Partially verified or confirmed defect affects the area | 6 |
| External requirement blocks end-to-end acceptance | 3 |

| Brief area | Audit result and limits |
| --- | --- |
| A. Authentication | Partial. Existing tests exercised registration and resend, verified-email state, password reset, local redirects, TOTP enrollment/use/recovery and simulated OIDC. Browser checked sign-in and password visibility. Live Google/SMTP/Resend and complete session/factor lifecycle acceptance remain open. See findings 010 and 015. |
| B. Multi-tenancy | Locally verified for tested scopes. Business switching and owner/membership separation are present. Existing suites assert business-scoped object access on catalog, purchase, guest media, team, orders and financial paths. No export or supported general business deletion workflow was identified. |
| C. Roles and workspaces | Partial. Owners and explicit collaborator permissions are implemented. Department and WorkspaceAssignment provide no authorization, and fixed Admin/Manager/Employee hierarchies are absent. Inventory/purchasing location scope has defensive coverage. Station payment location scope has a gap. |
| D. Catalog | Locally verified for tested flows. Tenant reference resolution, category edits/reassignment, normalized media, required/optional options, locations, server-priced surcharges and order modifier snapshots are covered by HTTP tests. Product availability uses active products and scoped options/pricing. A separate per-location product-availability system and bulk import are absent. |
| E. Promotions and vouchers | Locally verified. Window, timezone, targeting and authoritative pricing assertions passed. The existing concurrent capped-redemption test passed. Current policy retains redemptions on cancellation or failed payment. Verify that policy commercially before a campaign, especially capacity limits for unpaid orders. |
| F. Guest ordering | Locally verified for counter flows. Signed table and pickup entry, private carts/tracking, checkout, option and voucher pricing, and revocation behavior have test coverage. Live status has a replay-ordering defect under retry, tracked separately. Provider QRIS and full guest device coverage remain open. |
| G. POS and order history | Partial. Normal cash, receipt, refund and history work in a local flow. Existing service assertions cover replay and authorized tender. Unpaid completion can strand collection. Held orders, shift close, split tender, taxes and POS provider checkout remain absent. |
| H. Kitchen and stations | Partial. Browser KDS and station pairing/capabilities, product routing and status behavior are implemented. Item-level progression and a paired-device WebSocket stream are absent. Inspect findings 004, 008 and 009 before device/print acceptance. |
| I. Inventory | Locally verified in the exercised cases. Opening, adjustment, recipe commit/return, count rollback and concurrent variance assertions passed. Last accepted unit cost is an operational estimate. Transfer, alerts and accounting valuation remain open. |
| J. Suppliers and purchasing | Locally verified in the exercised cases. Supplier scope, purchase revisions, partial/full receiving, damaged/rejected quantities, exact replay, concurrent over-receipt prevention, rollback and supplier returns passed existing checks. Fresh synthetic fixture used the real receiving view. |
| K. Ordering Page Builder | Locally verified in the exercised cases. Private preview, draft separation, published config, reference and upload validation, owner-only publication/restore, stale revision and existing-link coverage. Hosted storage and assistive-technology acceptance are still open. |
| L. Merchant payments and refunds | External acceptance blocked. Local mocked adapter, callback, cancellation, capture and refund assertions passed. Time, collection, correlation and readiness defects are listed below. No real charge or refund was made. BCA and multiple-capture refund operations are absent. |
| M. SaaS billing | External acceptance blocked. Owner restriction, invoices, grandfathering/trials, entitlements, existing mock checkout and reuse after timeout, late/failed/duplicate confirmations, and renewal/grace/cancellation were exercised by the baseline. Separate platform merchant and actual callback delivery remain open. No automatic debit. |
| N. Reports | Partial. Paid gross, pending, category, product, location, voucher and confirmed refund totals are implemented. A time boundary and live catalog reclassification change the result. The reconciliation panel explicitly follows the original order date. Exports and accounting settlement statements are absent. |
| O. Real-time delivery | Partial. Real Redis fan-out and local WebSockets cover business orders, guest menu/tracking, inventory, purchasing and billing. Retries can deliver stale snapshots. Reconnect behavior is scoped, and general notifications, delivery acknowledgements and worker health are incomplete. |
| P. Printing | External acceptance blocked. Receipt and preparation output paths, 58/80 mm layouts, jobs, acknowledgements and manual reprints have local coverage. Physical output, printer-driver behavior and hardware fault recovery were outside this run. Preparation acknowledgement can regress terminal state. |

## High findings

### VEYRA-AUDIT-001 — QRIS provider timestamps are seven hours wrong

**High · confirmed · high confidence · simulated provider request.** Affects tenant Midtrans and BCA SNAP QRIS. Both adapters format UTC wall time with a Jakarta offset without converting the datetime first. With a frozen UTC clock and stubbed transport, decoding both outbound time values gave a difference of **−25,200 seconds** from the intended instant. The expected offset-aware instants match the application's clock and configured expiry. Provider rejection or early expiry would block QRIS checkout. Actual provider handling remains unverified.

**Remediation and regression.** Construct offset-aware timestamps once using the provider's documented timezone/format. Freeze the clock at midnight and the expiry boundary in both adapter contracts, then compare request and response lifetimes with the approved sandboxes. Requires provider acceptance after the local correction. [Midtrans adapter](../core/payments/midtrans.py#L87) · [BCA adapter](../core/payments/bca_snap.py#L53) · [Midtrans expiry contract](https://docs.midtrans.com/reference/custom-expiry-object)

### VEYRA-AUDIT-002 — Counter tender and an active tenant QR can coexist

**High · confirmed by source · high confidence.** The tenant initiation and counter tender services do not enforce a shared collection invariant for non-guest orders. A pending provider attempt is persisted without reserving the order's payment method, and the cashier service gates chiefly on the order method/state. Both payment records can therefore remain eligible. The expected behavior is one active collection channel unless split tender is explicitly implemented. Consequence is potential duplicate collection or a reconciliation discrepancy. The audit did not attempt simultaneous collection or any real charge.

**Remediation and regression.** Under the order lock, model and enforce active collection ownership separately from final tender. Require verified release of an active provider attempt before accepting counter tender. Add negative transactional assertions spanning the station, POS and callback boundaries, plus race cases for each processing order. Coordinate cancellation and late-capture policy with findings 003 and 004. [Initiation service](../core/services.py#L1568) · [Tender service](../core/services.py#L1177)

### VEYRA-AUDIT-003 — Tenant QRIS loses correlation on an ambiguous provider response

**High · confirmed by source · high confidence in local behavior.** The tenant service stores a payment attempt only after the outbound provider call succeeds. Adapter references are generated inside initiation. There is no durable pending intent or reference available to the local callback matcher if the response is lost after provider acceptance. A subsequent attempt can use a different provider reference. Expected behavior is to retain one reference across an ambiguous retry and reconcile before creating a replacement. Unmatched provider attempts and late settlement can require manual financial recovery. The provider's actual behavior under this fault remains unverified.

**Remediation and regression.** Persist the tenant intent and stable merchant reference before requesting a QR. Move network calls outside long-held order locks. Reconcile uncertain attempts against supported provider status and keep late/additional captures visible to finance. Simulate acceptance followed by a transport timeout, concurrent retries and later reconciliation using one synthetic attempt identity. Depends on provider idempotency and status capabilities. [Initiation and callback match](../core/services.py#L1568) · [Midtrans reference](../core/payments/midtrans.py#L87) · [Separate platform retry reference test](../core/test_billing.py#L197)

### VEYRA-AUDIT-004 — Station payment lookup omits station location scope

**High · confirmed by source · high confidence.** The station payment branch verifies an active device with order read capability and business membership. The subsequent order lookup omits that device's location predicate, which other station order paths enforce. A location-bound device should only act on eligible orders at that location. The defect expands financial initiation and its associated visibility beyond the intended branch. Business scope remains in the query. Cross-tenant exposure and unauthorized settlement were not observed.

**Remediation and regression.** Reuse the station order scope in the payment service and decide whether initiating payment needs a distinct capability. Add existing-policy tests that ensure foreign locations are rejected before a provider operation or record creation and verify location-free register behavior separately. Coordinate with the collection guard. [Station payment handler](../core/device_views.py#L353) · [Payment service scope](../core/services.py#L1568) · [Status service scope](../core/services.py#L1113)

### VEYRA-AUDIT-005 — Completing an unpaid counter order strands tender

**High · confirmed · high confidence · PostgreSQL workflow.** In the local authorized owner workflow, a counter order moved through preparation, ready and completed while unpaid. Its POS card no longer offered tender. The server also rejects normal tender for completed orders. Expected behavior is either to require collection before completion or retain an explicit, audited post-completion tender path. The current result strands the receivable and removes the normal paid receipt workflow. This can occur during ordinary food preparation and handoff.

**Remediation and regression.** Choose one domain policy and apply it in the shared state service and station/POS controls. Add HTTP coverage for unpaid counter/KDS completion, paid completion, correction of affected existing orders and replay safety. Keep void and refund policy separate. [State transitions](../core/services.py#L1113) · [Tender](../core/services.py#L1177) · [POS controls](../templates/core/pos.html#L50)

### VEYRA-AUDIT-006 — Sales totals and daily grouping use different day boundaries

**High · confirmed · high confidence · PostgreSQL report responses.** A valid non-Jakarta business timezone was tested with one settled Rp25,000 order. The order belonged to November 24 in the business timezone. The November 24 report returned zero and November 25 returned Rp25,000 grouped under November 24. Expected behavior is that report membership and daily bucket use the same business day. This misstates daily financial totals for businesses outside the runtime timezone.

**Remediation and regression.** Derive default dates, range bounds and grouping from one business timezone. Test adjacent days, DST boundaries, and location and refund filters on known transactions. Keep the stated original-order cohort rule separate from settlement-by-day reporting. [Report date bounds and grouping](../core/views.py#L426)

### VEYRA-AUDIT-015 — Signup credential ownership needs additional assurance

**High · potential · medium confidence · source review only.** Signup persists a password on an inactive record and the later verification view activates that record without an independent credential enrollment step. The design does not show a binding between mailbox proof and the initial credential choice. The potential impact is loss of control of a newly verified identity if the initial enrollment and the mailbox proof are performed by different parties. Account takeover was not demonstrated.

**Remediation and regression.** Review an email-first flow or a verified final credential-enrollment step. Require a clear proof-of-ownership invariant for email, OAuth and invitations. Cover pending account completion, invitation continuity and safe migration of inactive registrations. [Registration and verification](../core/views.py#L245)

## Medium findings

### VEYRA-AUDIT-007 — Catalog reclassification rewrites historical category totals

**Medium · confirmed · high confidence · PostgreSQL report responses.** A paid product sale was moved to a different category in the catalog. The report assigned the old sale to the new category. Expected historical reporting keeps the sale's original category or states explicitly that it follows current assignments. OrderLine stores the product name and price snapshots, while reports read the current product category. Original monetary totals are unchanged, but category history and category-filter membership drift.

**Remediation and regression.** Choose a documented historical policy. Capture category identity and label on new order lines, with an explicit fallback for legacy rows. Assert report stability after category reassignment, rename, archive and recategorization. Depends on an additive schema decision. [OrderLine](../core/models.py#L1313) · [Report category query](../core/views.py#L508)

### VEYRA-AUDIT-008 — Late preparation acknowledgement regresses a printed job

**Medium · confirmed · high confidence · PostgreSQL print state.** A prep ticket accepted a successful acknowledgement. A later in-progress acknowledgement then changed it from Printed to Printing. A terminal acknowledgement should be stable, with explicit reprint jobs creating new history. Receipt jobs already have a terminal-state guard, and prep jobs lack the equivalent. Operators can see false status or attempt unnecessary reprints. Physical duplicate output was not tested.

**Remediation and regression.** Enforce valid transitions and idempotency across ticket types, with a deliberate retry claim and separate reprint record. Test duplicate and out-of-order acknowledgements, both receipt widths and actual device failure recovery. [Acknowledgement service](../core/services.py#L1512) · [Device API contract](STATIONS-AND-DEVICE-API.md)

### VEYRA-AUDIT-009 — Outbox backoff can reverse the visible guest state

**Medium · confirmed · high confidence · simulated delayed delivery and client review.** The dispatcher emitted Ready and later emitted the older New snapshot after its retry became due, while PostgreSQL remained Ready. The guest script deduplicates only identical event IDs and accepts distinct snapshots in arrival order. Expected state must not move backward after a later authoritative revision. Guests can see a false readiness, payment or refund display until another correct event or refresh arrives.

**Remediation and regression.** Attach an authoritative revision, ignore stale revisions, or fetch the current snapshot from a permission-checked path after event hints. Test failed then recovered publication, delayed receipts, duplicate delivery and reconnect under real Redis with a browser asserting the resulting state. Preserve the transactional event creation. [Outbox and payload](../core/services.py#L61) · [Guest consumer](../core/consumers.py#L65) · [Guest client](../core/static/core/site.js#L1)

### VEYRA-AUDIT-010 — Email failure leaves an inactive account behind a generic 500

**Medium · confirmed · high confidence · simulated mail fault through the registration view.** With a controlled provider failure, registration returned 500 and left an inactive account persisted. The repeat-registration path returns generic confirmation for an existing email, so retry does not reliably send fresh mail. Expected behavior is durable, idempotent mail delivery or actionable recovery without exposing whether an address has an account. The root cause is a synchronous send after account save without a durable delivery record.

**Remediation and regression.** Separate durable account creation and delivery state. Retry an eligible delivery with deduplication and generic public feedback. Cover registration/resend outages, recovery after the provider returns, and no duplicate delivery on retry. Hosted mail acceptance is separate. [Registration and resend](../core/views.py#L220)

### VEYRA-AUDIT-011 — Local credential-format check enables guest QRIS before merchant acceptance

**Medium · confirmed by source · high confidence.** The UI accurately labels its check as configuration validation. The same timestamp is used by checkout as the provider eligibility condition. The expected readiness contract distinguishes stored configuration from an externally approved payment method. A valid-looking bundle can therefore make a failing option appear at checkout. Hosted merchant outcomes were not tested.

**Remediation and regression.** Add explicit readiness or approval separate from format validation. Keep counter payment available during onboarding/failed configuration. Cover mixed verified/unverified provider selections and regression on disabling an approved method. Needs agreed provider acceptance process. [Configuration check](../core/services.py#L1547) · [Guest provider selection](../core/views.py#L819)

### VEYRA-AUDIT-012 — Local dependency ports publish on all host interfaces

**Medium · confirmed · high confidence · Compose and local Docker observation.** The default PostgreSQL and Redis mappings published on all host interfaces rather than loopback. Compose is documented as a development profile. Off-host reachability was not tested and depends on the host network and firewall. The risk is accidental exposure of local development infrastructure, especially on a shared network.

**Remediation and regression.** Bind published development ports to loopback, or omit publishing when consumers use a private network. Check the rendered Compose port bindings and keep local host development and container networking working. Use authenticated private services in hosted infrastructure. [Compose defaults](../compose.yaml#L1)

### VEYRA-AUDIT-013 — An existing guest socket does not recheck tracking expiry

**Medium · confirmed by source · high confidence.** HTTP and socket connection setup enforce a 24-hour tracking-cookie age. Subsequent guest update delivery checks order identity but does not revalidate the cookie age. The expected behavior is that the same access lifetime applies to an already connected viewer. Scope is a previously authorized channel and the affected order, with no observed cross-order or cross-tenant exposure.

**Remediation and regression.** Apply the same expiry policy at delivery or close the session at its authorized deadline. Test advancing a controlled clock with an already open guest stream, normal reconnect before expiry, and later authorized media/status requests. [Tracking validation](../core/permissions.py#L9) · [Tracking consumer](../core/consumers.py#L65)

### VEYRA-AUDIT-014 — Release resolves advisory-flagged packages and differs from the development environment

**Medium · confirmed · high confidence · exact release package inventory and OSV.** The rebuilt Python 3.12.16 Linux image contains cryptography `46.0.7` with bundled OpenSSL `3.5.6` and pip `25.0.1`. Both package versions matched advisories. The developer environment contains cryptography `50.0.2` and fails `pip check` against the project’s `<47` limit. At least one vendor correction for the bundled wheel is already outside that limit. The expected release is built and tested with aligned, reviewed dependencies. Version matches are confirmed. App-specific reachability, host OS vulnerabilities and exploitation were not assessed.

**Remediation and regression.** Resolve current fixed dependencies under explicit compatibility constraints, including build tooling, and capture a locked production manifest and approved digest. Validate Authlib, MFA and encryption compatibility against existing ciphertext. Rebuild from clean inputs, run the actual resolved dependency checks and scan the resulting artifact before release. [Python dependency range](../pyproject.toml#L10) · [Release build](../Dockerfile) · [Cryptography maintainer advisory](https://github.com/pyca/cryptography/security/advisories/GHSA-537c-gmf6-5ccf)

## Informational findings

### VEYRA-AUDIT-016 — Dependency readiness has a narrower contract than operational readiness

**Informational · confirmed · high confidence.** The health endpoint and PostgreSQL/Redis readiness checks behaved as documented. They do not prove continued outbox, billing or refund progress. No repository deployment manifest configures a hosted proxy, worker supervision, external alerts or recovery objectives. Expected pilot acceptance includes worker progress, queue age and alerts observed against actual infrastructure. Implement operational probes and a staged incident drill before external launch. [Health and logs](../core/observability.py) · [Operations](OPERATIONS.md)

## Security and reliability controls observed

- **Identity and sessions.** The deployment config requires a strong secret outside debug, sets secure session/CSRF cookies, enforces forwarded HTTPS and HSTS, and uses owner/admin MFA policy. Existing tests exercised return URL validation, TOTP replay and one-use recovery. The request log uses route patterns and allowlisted metadata. Proxy configuration and hosted log retention remain separate.
- **Tenant authorization.** Management reads and writes generally resolve the tenant and permission, with scoped form choices and object lookups. The tested permission matrix includes location-limited inventory and private editor/media. Owner actions and paired devices use different guards. The station counterexample in this report should be fixed before merchant acceptance.
- **Browser and media.** CSRF controls protect normal browser writes. Other auth boundaries use device credentials or provider verification. Django rendering, restrictive headers, preview sandbox/CSP treatment and bounded decoded JPEG/PNG/WebP input were inspected. This is a scoped defensive review, not a penetration assessment.
- **Transactions.** The baseline includes PostgreSQL concurrency for voucher capacity, counts, receiving, refunds, invoices and builder changes and tests transaction rollback for stock. Order, tender and refund records separate operational state from collection. Payment failures above identify interoperability gaps that row locks alone do not cover.
- **Delivery.** Local socket tests covered authorization, revocation assertions, and representative order, catalog, inventory and billing paths. A channel send proves neither browser receipt nor physical printing. The retry finding and worker/recovery gates are distinct.

## External acceptance and unassessed scope

- **Payment and billing** need approved tenant Midtrans and BCA merchants, a separate platform merchant, reachable HTTPS callbacks and authorized sandbox settlement/refund/reconciliation. No actual transaction or provider refund was initiated.
- **Identity, media and messaging** need a registered Google callback, approved sender and recipients, and an authorized private Supabase project. A local adapter response or private preview does not establish provider configuration or delivery.
- **Infrastructure** needs a hosted TLS/WSS proxy, authenticated Redis/Valkey, durable database backups, configured worker processes, supervised recovery and controlled production secrets. The restore rehearsal covered local schema/record integrity, with no hosted recovery-time or failover result.
- **Hardware and accessibility** need paired printers and approved clients, physical phone/tablet/Safari coverage and screen-reader acceptance. Current browser sampling covers layout and selected keyboard/contrast behavior. No performance, capacity, destructive or independent security assessment was undertaken.
- Unsupported capabilities such as passkeys, formal workspace permissions, general customer notifications, advanced inventory valuation, exports, BCA refunds, automated recurring debit and POS shifts are product scope items. Their absence is tracked in [feature status](FEATURE-STATUS.md).

[Remediation in recommended order](AUDIT-REMEDIATION.md)
