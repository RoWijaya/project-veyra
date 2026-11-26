# Stage 1 implementation and release gates

The implementation is on branch update from 5dec2ca5f2c656c8270a06a5a7f5c356b2eada05. The initial tree and index were clean. Application and deployment changes are uncommitted for review. Test databases, Redis namespaces, browser accounts and staging resources were isolated from development data.

**READY FOR HOSTED STAGING ACCEPTANCE**

The final application suite passed 271 tests with zero failures and zero skips. Disposable non-debug staging and the three browser engines passed the applicable workflows. Independent hosted, provider and owner gates remain open. [Evidence](TESTING.md#stage-1-release-candidate)

## Implementation and evidence by task

| Task | Prior behavior and delivered change | Evidence and scope |
| --- | --- | --- |
| A Authorization | Location policy already gated HTTP and WebSocket data. A revoked connection could remain subscribed until the transport disconnected. It now leaves immediately when the server detects revocation. Inventory broadcasts load current authorized stock so a stale or replayed event cannot regress a quantity. | PostgreSQL and Redis tests cover inventory and purchasing, independent owners and members, sibling grants, permission changes, rejected reconnects, stale subscriptions and current quantities. Existing order, purchase, receipt and guest scopes remain under regression. [Consumers](../core/consumers.py) · [Location tests](../core/test_priority_location_access.py) |
| B Signing and rotation | Separate API and signing keys existed. The fallback was implicit and a tenant credential replacement overwrote callback authentication for historical attempts. Legacy tenant signing now requires the exact connection allowlist entry and platform legacy mode requires its own separate flag. Rotation links older tenant attempts to encrypted signing material with a 30-day upper bound and a revocation path. Current API credentials continue trusted read-back within the same immutable merchant/environment. Platform supports a separate time-limited previous signing key for attempts created before cutover. Midtrans status must explicitly identify the expected merchant before an unbound tenant attempt can settle. | Tested wrong, absent, replayed and split signatures, separate credentials, old attempt reconciliation with the new API key, and rejection after early retirement. Provider rotation policy remains external. [Signature tests](../core/test_priority_payment_signatures.py) · [Rotation runbook](PAYMENTS.md#staged-credential-rotation) |
| C Payment readiness | The merchant owner already submitted authenticated read-only Midtrans evidence plus operator approval. New QRIS now requires verified state, a verification timestamp and approval reference. Platform checkout independently requires its API and signing keys, HTTPS public origin and dated approval reference. Outstanding read-back remains available even when new checkout approval is withheld. | HTTP tests cover a missing platform approval, tenant merchant identity and QRIS proof, and BCA remaining gated. The application records operator evidence. Provider Write and callback evidence still need an approved test environment. [Tests](../core/test_priority_merchant_readiness.py) · [Platform tests](../core/test_billing.py) |
| D Network and proxy | Compose already kept PostgreSQL and Redis private. Django previously read the raw forwarded scheme header. Uvicorn now accepts forwarding only from configured direct peers. The application validates Host and production WebSocket scheme and origin. Wildcard host and all-address proxy trust settings are rejected outside development. | HTTP and ASGI tests cover trusted and untrusted forwarding, hostile Host, foreign or insecure WebSocket origins and CSRF. The checked-in image and Compose topology are covered in isolated staging. Real hosted ingress, certificate renewal and public WSS remain open. [Proxy tests](../core/test_stage1_operations.py) · [Acceptance commands](DEPLOYMENT.md#hosted-stage-1-acceptance) |
| E Workers and outbox | Existing workers already retried and recorded progress. Shared worker-type keys concealed a stalled replica and outbox retry had no terminal state. Worker keys now identify the replica and process generation. Outbox dispatch has a bounded attempt budget, fences stale claims and retains exhausted rows for scoped, audited redrive. | Real Redis tests exercise independent progress keys, and expiry/retry tests exercise exhaustion and recovery. The isolated staging drill exercises process crash, stalled progress, healthy peer and dependency interruption. Actual hosted paging and acknowledgement remain open. [Worker tests](../core/test_stage1_operations.py) · [Operations](OPERATIONS.md) |
| F Backup and recovery | The restore probe previously checked payment and MFA ciphertext only. It now checks current and retained payment signing data, active account-mail ciphertext and an optional marker signed with the period-appropriate Django key. The runbook specifies encryption, key custody and measured recovery. | The synthetic fixture preserves tenant boundaries, the order total, private media and encrypted operational material. Hosted key retrieval, backup scheduling, retention and approved RPO/RTO remain open. [Fixture](../tests/recovery/staging_fixture.py) · [Runbook](DEPLOYMENT.md#backup-and-restore) |
| G Account recovery | Signup already used a durable leased queue. Verification tokens now bind to the delivery generation, so a resend supersedes the preceding link. Migration 0043 preserves links issued to existing pending accounts until resend or expiry. Password reset accepts known and unknown requests through the same rate-limited queue. Recipient data is encrypted and cleared after terminal processing. | Tests exercise retry, deadline, neutral responses, deduplication, replacement and single-use activation. Browser checks exercise the public signup, resend and reset screens. Hosted sender and inbox delivery remain open. [Mail tests](../core/test_priority_account_mail.py) |
| H Historical reporting | Migration 0040 already captured category and SKU at sale time. Added a workflow regression for signed guest ordering and POS, category changes, cash and card, unpaid and cancelled orders, and separate refunds. | The existing tests preserve deleted categories and unknown legacy attribution. Category sales remain gross. Refunds are shown separately and affect retained sales. [Reporting tests](../core/test_priority_category_history.py) |
| I Integration | Checks run against real PostgreSQL and Redis, disposable non-debug staging, and Playwright browser fixtures. No merchant charge, callback from a provider or real email is part of local evidence. | [Verification record](TESTING.md#stage-1-release-candidate) |
| J Documentation | Updated the audit, roadmap, environment, payments, testing and operator runbooks. | This document records the checklist and evidence boundaries. |

## Migration and rollback

- **0041** adds a retained signing record and an optional relationship from a payment attempt. Existing attempts remain untouched until the next same-merchant rotation. No past key is inferred. Preserve retained rows and ciphertext when rolling back a release that has already rotated credentials. Disable new payment initiation and coordinate pending work before returning to code that ignores signing versions.
- **0042** adds the nullable outbox failure timestamp. It preserves event IDs and backlog. Redrive one scoped terminal event after reviewing the incident. Reverting to a worker that ignores the terminal marker may replay exhausted events. Keep workers paused until a rollback can classify them.
- **0043** adds password recovery deliveries and marks existing verification rows to honor unexpired issued links. Signup and recovery data are kept. Roll back with account-mail workers stopped and a reviewed disposition for pending deliveries. Reissue links if an older app cannot read a newer verification claim.

## Completion checklist

- [x] Extend local stock and purchasing authorization regression coverage.
- [x] Gate legacy signing and retain bounded verification for existing attempts after rotation.
- [x] Separate tenant and platform readiness and keep unverified channels gated.
- [x] Constrain trusted proxy identities and verify the local HTTP/ASGI boundary.
- [x] Add replica heartbeat, bounded outbox retry and operator redrive.
- [x] Extend recovery validation to rotated credentials and Django-signed artifacts.
- [x] Verify superseded signup links and queued password recovery.
- [x] Verify historical attribution through guest and POS workflows.
- [ ] Accept the exact image, proxy, private dependencies and monitor in a hosted staging environment.
- [ ] Accept separate merchant and platform providers for each enabled payment channel.
- [ ] Approve and witness hosted recovery and email delivery.

## External release gates

| Gate | Risk and blocker | Next action and closing evidence |
| --- | --- | --- |
| Hosting ownership and topology | No authorized hosted staging target or proxy access was available. Public exposure, proxy trust and certificate behavior depend on the environment. | Use the deployment checklist on the approved target. Record the exact image digest, allowed proxy peers, private dependency tests, valid TLS/WSS and failed Host, Origin and direct untrusted requests. |
| Worker and queue alarms | The repo defines probes and handoff conditions. No hosted monitor destination or operator acknowledgement was available. | Configure a read-only monitor with separate dependency, per-replica and queue alarms. Induce a controlled failure for each class in staging. Record operator receipt, acknowledgement and recovery timestamps. |
| Tenant Midtrans and platform billing | No sanctioned merchant accounts, hosted callbacks or provider-confirmed rotation window were available. Both payment modes have separately gated application paths. | Use approved sandbox merchants and independently record status access, Write, signed callback, replay, rotation and reconciliation for each enabled mode. Confirm merchant-side settlement only in a separately approved rollout. |
| BCA | The application cannot establish approved SNAP authorization, payment status and recovery capability with the available contract. | Leave BCA gated. Obtain the exact approved BCA merchant and product contract before deciding what additional adapter work is required. |
| Recovery policy | Synthetic recovery does not establish hosted durability, key access or achievable objectives. | Owner and operator approve retention and RPO/RTO. Configure storage, separate secret custody and backup jobs. Record start time, clean recovery time, tested data cutoff, key and media hashes, and post-restore validation. |
| Email | No approved sender, test inbox or real hosted delivery access was available. | Verify queued signup, resend and reset through the approved host and recipient. Record provider acknowledgement and inbox receipt, then exercise a safe failure and successful recovery. |

No pending hosted gate has been accepted on the strength of local tests.

## Acceptance classification

- **Verified locally.** Application authorization, payment and account gates, history and browser workflows passed on disposable PostgreSQL and Redis. [Testing evidence](TESTING.md#stage-1-release-candidate)
- **Verified in disposable staging.** The final non-debug image passed scoped proxy forwarding, Host and WebSocket checks, replica crash and stall behavior, Redis interruption, encrypted recovery and missing-key checks. [Staging evidence](TESTING.md#stage-1-release-candidate)
- **Actual hosted staging.** No authorized hosted target was available in this session.
- **Requires provider cooperation.** Tenant and platform Midtrans credentials, Write, callback and cutover acceptance, and the BCA authorization and recovery contract.
- **Requires owner approval.** Hosted target and operator access, recovery objectives and retention, the email test recipient and permitted merchant sandbox activity.
- **Still unverified externally.** Public TLS and WSS, alert delivery and acknowledgement, inbox delivery, hosted backup custody and performance, and the provider cooperation checks above.

## Working tree inventory

The uncommitted change contains 51 files. The branch and index were preserved.

### Application and runtime

[.env.example](../.env.example) · [Dockerfile](../Dockerfile) · [config/asgi.py](../config/asgi.py) · [config/settings.py](../config/settings.py) · [core/account_mail.py](../core/account_mail.py) · [core/billing_gateway.py](../core/billing_gateway.py) · [core/consumers.py](../core/consumers.py) · [core/customer_views.py](../core/customer_views.py) · [core/management/commands/check_operational_queues.py](../core/management/commands/check_operational_queues.py) · [core/management/commands/check_recovery_keys.py](../core/management/commands/check_recovery_keys.py) · [core/management/commands/check_worker.py](../core/management/commands/check_worker.py) · [core/management/commands/process_account_mail.py](../core/management/commands/process_account_mail.py) · [core/management/commands/process_billing.py](../core/management/commands/process_billing.py) · [core/management/commands/process_outbox.py](../core/management/commands/process_outbox.py) · [core/management/commands/redrive_outbox.py](../core/management/commands/redrive_outbox.py) · [core/management/commands/retire_payment_signatures.py](../core/management/commands/retire_payment_signatures.py) · [core/middleware.py](../core/middleware.py) · [core/models.py](../core/models.py) · [core/password_recovery.py](../core/password_recovery.py) · [core/payments/midtrans.py](../core/payments/midtrans.py) · [core/permissions.py](../core/permissions.py) · [core/services.py](../core/services.py) · [core/urls.py](../core/urls.py) · [core/views.py](../core/views.py) · [core/workers.py](../core/workers.py)

### Migrations

[core/migrations/0041_retained_payment_signing_versions.py](../core/migrations/0041_retained_payment_signing_versions.py) · [core/migrations/0042_outbox_terminal_failures.py](../core/migrations/0042_outbox_terminal_failures.py) · [core/migrations/0043_durable_password_recovery.py](../core/migrations/0043_durable_password_recovery.py)

### Verification

[core/test_audit_remediation.py](../core/test_audit_remediation.py) · [core/test_billing.py](../core/test_billing.py) · [core/test_customer_cart.py](../core/test_customer_cart.py) · [core/test_priority_account_mail.py](../core/test_priority_account_mail.py) · [core/test_priority_category_history.py](../core/test_priority_category_history.py) · [core/test_priority_location_access.py](../core/test_priority_location_access.py) · [core/test_priority_payment_signatures.py](../core/test_priority_payment_signatures.py) · [core/test_refunds.py](../core/test_refunds.py) · [core/test_stage1_operations.py](../core/test_stage1_operations.py) · [core/test_vouchers.py](../core/test_vouchers.py) · [core/tests.py](../core/tests.py) · [tests/browser/priority-workflows.cjs](../tests/browser/priority-workflows.cjs) · [tests/browser/serve_ordering_builder.py](../tests/browser/serve_ordering_builder.py) · [tests/recovery/staging_fixture.py](../tests/recovery/staging_fixture.py)

### Documentation

[docs/DEPLOYMENT.md](DEPLOYMENT.md) · [docs/ENVIRONMENT.md](ENVIRONMENT.md) · [docs/OPERATIONS.md](OPERATIONS.md) · [docs/PAYMENTS.md](PAYMENTS.md) · [docs/PRODUCT-FEATURE-AUDIT.md](PRODUCT-FEATURE-AUDIT.md) · [docs/PRODUCT-ROADMAP.md](PRODUCT-ROADMAP.md) · [docs/STAGE-1-RELEASE-READINESS.md](STAGE-1-RELEASE-READINESS.md) · [docs/STAGE-1-VERIFICATION.md](STAGE-1-VERIFICATION.md) · [docs/TESTING.md](TESTING.md)
