# Veyra feature status

This is the current implementation inventory, checked on 2026-11-25. [Product requirements](PRD.md) describe the target. [Dated release and browser evidence](RELEASE-HISTORY.md) describes prior checkpoints. [Testing](TESTING.md) records current verification and its limits.

[Accounts and Management](#account-and-management) · [Orders and stations](#orders-catalog-and-stations) · [Inventory and finance](#inventory-and-finance) · [Platform and delivery](#platform-quality-and-delivery)

## Status meanings

| Status | Meaning |
| --- | --- |
| **Implemented and locally verified** | A usable local workflow and specific verification evidence exist. |
| **Implemented, external verification pending** | The workflow and adapter exist. An external provider, hardware, or deployment must still be verified. |
| **Partially implemented** | Usable pieces exist, with missing behavior identified in the row. |
| **Planned** | A follow-up is described in the requirements or operations plan. |
| **Not implemented** | No supported workflow was identified for the named capability. |

## Account and Management

| Feature | Status | Code, evidence and limits |
| --- | --- | --- |
| Email registration, verification, resend and password recovery | **Implemented and locally verified** | [Account views](../core/views.py) and [recovery tests](../core/test_account_recovery.py). Registration persists pending verification and delivers through a supervised queue. Worker retry, terminal failure, registration-session verification and cross-browser password creation have local coverage. Hosted delivery is separate. |
| Google sign-in and explicit linking | **Implemented, external verification pending** | [Identity views](../core/identity_views.py) and [identity tests](../core/test_identity.py) use Authlib and signed simulated Google tokens. Needs an approved Google client and live consent. |
| TOTP and one-use recovery codes | **Implemented and locally verified** | [Identity](../core/identity.py) uses a dedicated key, reauthentication and replay-resistant counters. Local HTTP/ASGI and prior browser evidence cover setup and challenge. |
| Additional account controls | **Not implemented** | Passkeys, assisted recovery after total factor loss, session inventory and Google unlinking are absent. |
| Business setup, multiple ownership and memberships | **Implemented and locally verified** | [Management views](../core/views.py), [models](../core/models.py), and [permissions](permissions.md). Canonical owner, switcher, optional collaborator invitations, explicit grants, and tenant scoping. |
| Multiple locations and inventory-specific grants | **Implemented and locally verified** | Management locations and [purchasing](../core/purchasing_views.py) use business scope. Owners can restrict inventory and purchasing by location. Supplier contacts and most other Management domains remain business-scoped. |
| Department/workspace assignment and owner transfer | **Partially implemented** | Departments, workspaces and assignments remain in [models](../core/models.py). Assignment-driven workflow authorization and a supported ownership-transfer flow remain absent. |

## Orders, catalog and stations

| Feature | Status | Code, evidence and limits |
| --- | --- | --- |
| Products, categories, photos and modifiers | **Implemented and locally verified** | [Catalog](../core/catalog_views.py), [media](../core/media_views.py), [modifiers](../core/modifier_views.py). Category management, location-scoped reusable options, availability, and historical order snapshots. |
| Stocked variants and bulk catalog import/export | **Not implemented** | Modifiers are priced choices and do not create independent inventory variants. |
| Scheduled prices, campaign banners and vouchers | **Implemented and locally verified** | [Pricing services](../core/services.py), [banners](../core/media_views.py), and [vouchers](../core/voucher_views.py). Business-local schedules and eligibility affect POS and guest menu. Vouchers have transactional limits and report totals. Fuller campaign analytics and announcements remain separate. |
| Signed table and location ordering | **Implemented and locally verified** | [Guest views](../core/customer_views.py) and the shared order service. Dine-in and takeaway, independent carts, options, vouchers, counter checkout and private tracking work through browser workflows. Live QRIS acceptance is separate. |
| Ordering Page Builder | **Implemented and locally verified** | [Builder audit and control matrix](ORDERING-PAGE-BUILDER.md#functional-audit-and-repair-2026-11-25) and [builder tests](../core/test_ordering_builder.py). Private preview, rendered four-color themes, responsive layout and catalog controls, draft revisions, owner-only publication and restoration, and live updates to existing QR and takeaway links. Hosted media archival and screen-reader acceptance remain open. |
| Browser POS, order history and receipt views | **Implemented and locally verified** | [POS](../core/views.py) and [receipt views](../core/receipt_views.py). Cash and terminal-card recording, payment-gated completion, eligible historical tender recovery, and 58/80 mm print layouts. Browser POS has no direct QRIS checkout. |
| Shifts, held orders, split tender, tax and service charges | **Not implemented** | No supported POS flow for these features. |
| Kitchen, bar, station pairing and device API | **Implemented and locally verified** | [Devices](../core/device_views.py) and [station reference](STATIONS-AND-DEVICE-API.md). Scoped API, queues, product routing, capability checks and browser KDS. Paired-device APIs use HTTP. |
| Physical printing | **Partially implemented** | The queue, ticket layouts, terminal preparation acknowledgements and explicit retry/reprint paths exist. Physical output and recovery require a compatible station agent and printer. Browser receipt output has been checked locally. |
| Customer records | **Partially implemented** | [Management](../core/views.py) and [models](../core/models.py) include customer records and order snapshots. Customer accounts, CRM history and segmentation, consent tools and guest messaging remain absent. |

## Inventory and finance

| Feature | Status | Code, evidence and limits |
| --- | --- | --- |
| Recipes, balances and stock movements | **Implemented and locally verified** | [Inventory and order services](../core/services.py). Location balances, opening/adjustment movements, and recipe deductions and supported cancellation returns. |
| Physical counts, purchasing and supplier returns | **Implemented and locally verified** | [Purchasing](../core/purchasing_views.py) and [reconciliation](../core/stock_reconciliation.py). Scoped suppliers, draft and submitted purchases, partial/full receiving, expected/count totals and append-only movements. Return quantity is bounded. |
| Inventory alerts and accounting | **Partially implemented** | On-hand quantities and last accepted cost estimates exist. General inter-location transfers, low-stock alerts, valuation costing and accounting integration remain open. |
| Tenant Midtrans and BCA SNAP QRIS | **Implemented, external verification pending** | [Tenant adapters](../core/payments/) and [payment guide](PAYMENTS.md). Midtrans uses a dedicated signing key and requires read-only QRIS evidence plus operator approval for checkout eligibility. Both adapters retain simulated callback and attempt coverage. BCA stays disabled for new QRIS pending an approved contract. Unresolved BCA attempts require terminal confirmation. Requires approved merchants and hosted callback/settlement verification. |
| Cash and terminal-card refunds | **Implemented and locally verified** | [Refunds](../core/refunds.py) preserve the original capture. Cash was tested end to end locally. Staff record the reference after an external terminal reversal for card. |
| Midtrans QRIS refunds | **Implemented, external verification pending** | Amount reservations, stable refund references, provider confirmation and the reconciliation worker are tested with simulated transport. Live bank confirmation and refund acceptance remain open. |
| BCA refunds, multi-capture refund and settlement accounting | **Not implemented** | These provider/payout workflows are outside the current refund and reporting services. |
| Subscription lifecycle and platform admin | **Implemented and locally verified** | [Billing](../core/billing.py), [billing views](../core/billing_views.py) and [tests](../core/test_billing.py). Plans, trial and quotas, invoices, renewal, grace, restrictions and cancellation. No automatic debit, tax invoices or support impersonation. |
| Platform Midtrans checkout | **Implemented, external verification pending** | [Billing gateway](../core/billing_gateway.py). Separate merchant, simulated checkout/status/callback tests and a reconciliation worker. Live checkout, notification delivery and settlement remain open. |
| Sales and voucher reporting | **Partially implemented** | [Reports](../core/views.py) use real sales/payment data, filters, product/category, location, fulfillment and voucher totals. Business-timezone filters and daily groups use the same date boundaries. Historical category identity and display name come from the sale snapshot, with a separate unknown bucket for earlier records. Confirmed refunds and retained payments use the order-created cohort. Export, tax, employee/station, scheduled and richer campaign reports remain open. |

## Platform, quality and delivery

| Feature | Status | Code, evidence and limits |
| --- | --- | --- |
| Real-time order, menu, stock, purchasing and billing | **Implemented and locally verified** | [Outbox and Channels](REALTIME.md), local PostgreSQL/Redis/ASGI and recorded browser workflows. Includes scoped updates, reconnect behavior and authoritative guest snapshots after delayed events. |
| Organization-wide synchronization and notifications | **Partially implemented** | Account mail and audit events exist. Many staff, workspace and management views lack live synchronization. General notification delivery, preferences, and an in-product inbox are absent. |
| Upload processing and local media persistence | **Implemented and locally verified** | [Forms](../core/media_forms.py), [scoped image views](../core/media_views.py) and PostgreSQL normalized bytes. Validation, fallback and private serving are tested. |
| Private Supabase archive | **Implemented, external verification pending** | [Storage integration](../core/media_storage.py), outbox and requeue command. Transport tests are simulated. Hosted bucket permissions, delivery and recovery remain open. |
| SMTP and Resend delivery | **Implemented, external verification pending** | [Mail settings](../config/settings.py) and [Resend backend](../core/email_backend.py). Local console and mocked HTTP evidence. Hosted sender, actual delivery and durable retry remain open. |
| Logs, readiness and outbox retries | **Implemented and locally verified** | [Observability](../core/observability.py) and [operations](OPERATIONS.md). Dependency health and sanitized request logs exist. |
| Monitoring and hosted delivery | **Partially implemented** | Private staging Compose supervises ASGI and four workers. Heartbeat and queue checks passed locally, including the stopped outbox drill. Hosted TLS/WSS, alert delivery and recovery objectives remain external. [Staging and recovery](DEPLOYMENT.md) |
| Security assurance and performance | **Partially implemented** | [Security](SECURITY.md) and [ASVS register](ASVS-VERIFICATION.md) describe controls. Independent assessment, production TLS/proxy review and load/capacity evidence remain open. |
| Landing, shared UI and accessibility | **Partially implemented** | [UI handoff](UI-DESIGN-HANDOFF.md) records design and browser evidence, keyboard and contrast sampling, and reduced motion. Full screen-reader and physical-device acceptance remain open. |

## Current verification boundary

The High-priority audit remediation reran the 231-test suite on local PostgreSQL and Redis, including 16 new regressions. [Remediation results](AUDIT-REMEDIATION.md) The prior 38-route browser polish matrix and workflow runs remain dated evidence. Provider mocks, a local container build, and prepared staging instructions establish specific local behavior. Live production acceptance remains open. [Verification details](TESTING.md) · [Deployment gates](DEPLOYMENT.md)
