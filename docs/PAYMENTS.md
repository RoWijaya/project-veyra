# Merchant payments and platform billing

Veyra separates what a guest pays a business from what a business pays for a Veyra subscription. They have different merchants, credentials, callbacks, records, and reconciliation.

[Business payments](#business-payments) · [Refunds](#refunds-and-reconciliation) · [Subscriptions](#platform-subscriptions) · [Evidence](#acceptance-levels)

## Business payments

The business owner configures a provider at **Payments**. A ``PaymentConnection`` belongs to one business and provider. Midtrans and BCA SNAP use different credential bundles and sandbox or production endpoints. The tenant bundle is encrypted with ``PAYMENT_CREDENTIALS_ENCRYPTION_KEY``. Management records configuration, provider evidence, approval, failed verification and disconnected states. Only an approved connection can expose a new QRIS attempt. Migration 0039 moves old format-check timestamps to the verification history and clears their eligibility.

| Method | How it is used | Evidence and limits |
| --- | --- | --- |
| Counter cash | Staff record tender against an unpaid counter order. The server validates amount received and calculates change. | Local HTTP and browser cash checkout, receipt, and refund. |
| Counter card | Staff record card acceptance after using the merchant's own terminal. | Locally exercised server recording. Card charging, reversal, and terminal connectivity are external. |
| Midtrans QRIS | An eligible guest checkout or station payment action creates a dynamic QR. Pending QR is scoped to the guest or authorized station. | Provider transport is simulated in local tests. Live onboarding, public callbacks, settlement and refunds need approved merchant access. |
| BCA SNAP QRIS MPM Dynamic | The adapter handles existing QRIS attempts and notifications. | New BCA connections remain disabled pending BCA approval and an accepted SNAP contract. Locally simulated transport and callbacks retain coverage. No BCA refund operation. |

### Verify or replace a merchant connection

1. Activate the applicable QRIS product and provision credentials in the provider portal. In Midtrans **Settings → Access Keys**, obtain the Server Key and Signature Secret Key for the same merchant and environment. Enter those values in Veyra Payments.
2. Verify using an existing Midtrans QRIS order or transaction ID. Veyra sends a read-only, authenticated Core API request to the configured provider environment. It checks the canonical reference, Merchant ID, IDR QRIS and response signature. Authentication, prior QRIS activity and notification signing material are distinct evidence. The read path cannot prove Core API Write, current charge capability, public delivery or bank settlement.
3. For controlled rollout, an operator verifies the scoped Server Key has Core API Write, reviews approved QRIS initiation and callback delivery for the current configuration, then runs `python manage.py approve_tenant_qris --business SLUG --environment sandbox --evidence APPROVAL_ID`. Use the corresponding environment after its independent acceptance. The command requires a successful evidence read within one day. Keep bank settlement evidence separate.
4. Replace credentials to rotate or reconfigure. A replacement clears approval and requires verification again. Disconnect stops new attempts. When there is history, encrypted credentials are retained for outstanding status checks, callbacks and refunds. The original merchant and environment are fixed once history exists. Coordinate changes to signing keys with the provider when notifications are outstanding.

BCA credential entry supports its provider-issued fields. The approved QRIS authorization, status and callback contract still needs provider confirmation. Veyra keeps new BCA connections gated. [Midtrans access keys](https://docs.midtrans.com/docs/access-keys) · [Midtrans status API](https://docs.midtrans.com/reference/get-transaction-status) · [BCA API facility terms](https://www.bca.co.id/-/media/Files/Bisnis/solusi/API/20250718-EBS-0098A-C-2025-ID)

The tenant callback enters ``POST /api/payments/webhooks/<str:provider>/``. The service validates provider evidence against the connection and transaction before advancing state. Midtrans verifies SHA-512 notification signatures using the saved Signature Secret Key and binds trusted status to amount and provider references. An explicitly allowlisted legacy connection without a signing field can verify notifications with its original Server Key during the approved drain. Separate keys are never interchangeable once a signing key is configured. BCA uses its adapter's signature and reference checks. Append-only webhook records, scoped provider references, and idempotent state transitions handle repeats. A QR image, browser return, or callback body alone does not prove funds were received. When a payment attempt has no provider transaction ID yet, authenticated status must identify the expected merchant before Veyra binds the attempt. Incomplete status keeps the order awaiting reconciliation.

Both providers serialize a timezone-aware Jakarta instant. The local expiry represents the same instant as the provider's requested ten-minute expiry. Passing that time hides the old QR, but a replacement depends on verified terminal no-charge evidence. [Midtrans expiry format](https://docs.midtrans.com/reference/custom-expiry-object) · [Midtrans transaction status](https://docs.midtrans.com/docs/get-status-api-requests)

Veyra reserves an internal attempt ID, a separate idempotency key, and a provider-facing merchant reference before the outbound request. The provider transaction ID is bound when returned or confirmed. The attempt and reference survive a timeout. Midtrans retries check the existing reference before a fresh attempt can receive a new reference. BCA recovery waits for a trusted terminal callback. An unconfirmed or contradictory capture blocks competing tender. An unexpected late capture is retained for reconciliation without counting it as a second sale or releasing preparation.

An order with a positive balance can advance through preparation to Ready while paying at the counter. Completing it requires recorded payment. A completed historical counter order that still owes payment can be settled from Order history. Guest QRIS preparation remains gated by confirmed payment. Pending Midtrans cancellation checks the provider before local closure. Refunds use the original settlement. Browser POS records cash and terminal-card tenders. Paired stations have a separate QRIS endpoint. [Routes](FEATURES-AND-ROUTES.md) · [Station payment API](STATIONS-AND-DEVICE-API.md)

## Refunds and reconciliation

Owners manage refunds from a paid order. Cash refunds record the returned amount. For card, staff complete the terminal reversal and record the reference. Midtrans QRIS partial or full refunds reserve money under a lock, include a stable provider refund key, and keep the original capture. Pending or uncertain results retain the reservation. A matching bank-confirmed result marks a refund successful. A bound provider denial can release the reservation.

The owner can check a pending refund or retry with the same key within the supported retry window. ``process_refunds`` checks due pending refunds against Midtrans status and never submits a new refund. Receipts, guest tracking, and reports distinguish confirmed refunds from pending ones. The paid total and refund ledger are separate. BCA refunds, multiple-capture and split-tender refunds, automated settlement-to-bank reconciliation, and chargeback handling remain outside current support. [Refund service](../core/refunds.py) · [Refund tests](../core/test_refunds.py) · [Incident workflow](OPERATIONS.md#investigate-an-unconfirmed-refund)

## Platform subscriptions

Veyra administrators publish monthly plans and can restrict or restore a subscription in Django admin. Existing businesses covered by the migration retain grandfathered access. Eligible new businesses start the configured starter trial. Current plan and quota checks gate operations and preserve owner access to billing recovery.

Owners can see usage, choose a plan, open an invoice, pay through Midtrans hosted checkout, see renewal and grace states, request a period-end cancellation, or resume it. Plan snapshots and invoice prices come from the server. Payment is customer-initiated each invoiced period. The system has no automatic debit, tax invoice system, or automatic subscription refunds.

The separate gateway uses ``VEYRA_BILLING_MIDTRANS_ENV``, ``VEYRA_BILLING_MIDTRANS_SERVER_KEY``, ``VEYRA_BILLING_MIDTRANS_SIGNATURE_SECRET_KEY`` and ``VEYRA_BILLING_PUBLIC_ORIGIN``. Set the platform signing secret independently. The Server Key remains the API credential, with any legacy notification fallback requiring an explicit platform-only drain flag. Configure the platform merchant notification at ``POST /api/billing/notifications/midtrans/``. The app validates signatures and uses provider status to verify the correct attempt and amount. Repeat notifications and lost checkout responses are handled with durable invoice/attempt references. The billing worker advances boundaries, issues due renewal work, and reconciles pending payment. The outbox sends owner-only updates. [Billing views](../core/billing_views.py) · [Platform gateway](../core/billing_gateway.py) · [Billing tests](../core/test_billing.py)

## Acceptance levels

| Level | Evidence |
| --- | --- |
| Simulated provider | Local tests substitute HTTP transport for payment providers and exercise validation, idempotency, and reconciliation through the application. |
| Local workflow | PostgreSQL, Redis, and ASGI were used for QR/order tracking and counter payments, refunds, and representative billing flows. A mocked provider checkout remains a simulated provider test even when the local app is real. |
| Live sandbox | Pending for tenant Midtrans, BCA SNAP, and platform Midtrans. Needs approved merchant credentials and a reachable HTTPS callback. |
| Production | Pending. Capture, amount, expiry, refund where supported, callback and operator reconciliation must be demonstrated with the approved merchant and hosting configuration. |

[Environment settings](ENVIRONMENT.md) · [Current feature status](FEATURE-STATUS.md)


## Staged credential rotation

1. Record the intended business or platform merchant, provider environment, exact credentials revision, operator and provider rotation constraints. Review pending charges, refunds and disputed attempts before changing anything. Never reuse tenant keys for platform billing.
2. Tenant QRIS new initiation is disabled during replacement until the owner supplies fresh status evidence and the operator reapplies approval. When the merchant and environment are unchanged, the new API Server Key handles trusted read-back. Existing attempts can authenticate notifications with either the current signing key for their merchant or the retained key bound to that attempt. Retained signing material has a 30-day upper bound and is encrypted with the existing payment encryption key family. The billing worker clears expired ciphertext.
3. For a previously accepted tenant legacy merchant, list the exact approved connection ID in MIDTRANS_LEGACY_SIGNING_CONNECTIONS solely while draining legacy callbacks. Explicitly remove the listed ID when the legacy mode is closed. An explicitly empty signing field fails closed.
4. For platform billing, install separate API and signature keys. Configure the HTTPS origin and platform approval reference and timestamp after reviewing the approved checkout and callback evidence. An incomplete approval disables new checkout while read-back can continue. A legacy platform deployment can explicitly enable VEYRA_BILLING_MIDTRANS_ALLOW_LEGACY_SIGNATURE for its drain only. It does not enable new checkout.
5. If the provider requires old platform signatures during cutover, set the previous signing secret, cutover time and expiration as one reviewed change. Only attempts created before cutover can use the previous key, and the window is capped at 30 days. Remove the previous secret and timestamps when the operator closes the window.
6. Verify trusted settlement, replay and refund handling with sanctioned isolated accounts before admitting payment traffic. Retire compromised or no-longer-needed historical tenant signatures early with python manage.py retire_payment_signatures --connection CONNECTION_ID after confirming the target and recording the operator's incident reference. The command erases retained signing ciphertext for that connection and preserves payment records. Confirm provider-side revocation separately.

## Provider acceptance

- [ ] Verify tenant status credentials for the exact business, merchant, environment, QRIS product, currency and signing secret. Retain the evidence reference and operator approval time.
- [ ] Independently verify platform merchant, checkout product, API credentials and callback secret. Record its own dated approval. Keep tenant and platform status, attempts, callbacks, refunds and payouts independent.
- [ ] For each enabled merchant prove sanctioned Write and status read-back, reachable TLS callback, matching reference and amount, replay, authoritative failure, expiry and credential rotation. Confirm the callback alone cannot release an unpaid order.
- [ ] BCA remains gated pending the provider-approved QRIS authentication, status, notification and recovery contract. Authorize contract testing before enabling any BCA initiation.
- [ ] Verify any bank settlement or refund movement through a separately approved rollout. No actual settlement was part of the local verification.
