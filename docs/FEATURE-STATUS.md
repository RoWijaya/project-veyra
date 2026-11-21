# Feature status and known gaps

This status is based on source files included in the supplied ZIP. It is not a claim that the application has passed a production readiness assessment.

## Present in the inspected code

| Area | Evidence / status |
|---|---|
| Custom email-based user model | Implemented in `core.models.User` |
| Email verification | Registration and verification routes/views present |
| Password reset | Django password-reset routes configured |
| Multiple businesses per user | Supported by ownership and membership relations |
| Canonical business owner | `Business.owner` field and owner checks present |
| Tenant-scoped catalog | Product/category models and catalog view |
| POS order creation | Server-priced orders with order-line snapshots |
| Dine-in and takeaway | Order type fields and POS/device request handling |
| Order status transitions | Service-level transition validation |
| Order idempotency | Idempotency key/digest on order creation |
| Customers | Tenant-scoped customer records and order snapshots |
| Locations | Business-scoped location management |
| Inventory | Items, location balances, append-only stock movements |
| Audit events | Append-only audit event model and service writes |
| Optional collaborators | Explicit permission strings and hashed expiring invitations |
| Paired stations | Owner-managed stations, capability checks, pairing and revocation |
| Payment credential storage | Per-business encrypted credential storage |
| Order outbox | Durable outbox model and worker command |
| Order WebSocket consumer | Authenticated business order stream |
| Cinematic landing film | Static video asset, poster and GSAP/ScrollTrigger implementation notes |

## Explicitly incomplete or not evidenced

| Area | Status |
|---|---|
| BCA SNAP QRIS MPM payment creation | Not implemented |
| Midtrans payment creation | Not implemented |
| Provider credential verification | Not implemented; `verified_at` remains unset |
| Payment webhooks/notifications | Not implemented |
| Webhook signature verification and replay protection | Not implemented |
| Payment reconciliation/refunds | Not implemented |
| QRIS expiry and payment-confirmed order release | Not implemented |
| MFA/passkeys | Not implemented |
| Workspace-based authorization | Not implemented; workspace records are not grants |
| Location-scoped collaborator permissions | Not implemented |
| Invitation revocation | Not implemented in the current permissions documentation |
| Ownership transfer | Not implemented |
| Station token rotation/renewal | Not implemented |
| KDS user interface | Not evidenced |
| Printer routing, print queue, retry and duplicate prevention | Not evidenced |
| Recipe-based automatic stock consumption | Not implemented |
| Suppliers and purchase orders | Not implemented |
| Analytics/reporting | Not implemented |
| SaaS billing/subscriptions | Not implemented |
| Production media storage workflow | Not implemented |
| Full realtime synchronization across all domains | Not implemented; current stream is order-oriented |
| Live Redis/WebSocket production verification | Not established by the source archive |
| Production MFA/security audit/penetration test | Not evidenced |

## Important distinction

A `PaymentConnection` record means encrypted provider credential material has been stored. It does **not** mean the provider has authenticated the merchant, a payment can be created, a QR code can be generated, or a webhook can be trusted.

A `RecipeLine` model means recipe-related schema exists. It does **not** mean stock is automatically deducted when an order is placed.

A `Workspace` and `WorkspaceAssignment` model means configuration data exists. It does **not** mean workspace assignments enforce authorization.

A Channels consumer and outbox worker mean an order-event delivery path exists in code. They do **not** prove every event reaches every live client reliably in production.

## Source archive limitations

The supplied archive contains the new project under `new-project/Veyra`, plus a separate older project folder at the archive root. This documentation intentionally describes only the new project. The archive did not include a Veyra PRD, so this package describes code and the existing repository notes rather than claiming full PRD traceability.

The archive also included generated staticfiles and virtual-environment content. Those generated/dependency files were not treated as application source for this documentation.
