# Historical architecture reconciliation

This decision record describes the transition from the role-based pilot schema. Its payment rows describe what existed when that decision was made. Current provider and workflow status is in [architecture](ARCHITECTURE.md), [payments](PAYMENTS.md), and [feature status](FEATURE-STATUS.md).

| Area | Existing implementation | Decision |
| --- | --- | --- |
| Primary identity | One `User` may hold memberships in several businesses | Keep. Add `Business.owner` as canonical ownership; retain owner membership rows for compatibility with existing business-scoped services. |
| Business ownership | `Membership.role=OWNER` was the ownership source | Change. Backfill `Business.owner` from legacy Owner memberships, then remove role fields. Keep the new field nullable so legacy ownerless businesses migrate without losing data. |
| Team invitations | Required an account invitation with Admin/Manager/Employee role plus permissions | Keep optional invitations and hashed expiring tokens. Remove role hierarchy; only explicit permission strings grant collaborator access. No extra account is needed for an operational device. |
| Workspace models | Department, Workspace, and WorkspaceAssignment exist but have no application callers | Retain their records as configuration/history. Do not use assignments as access grants. Add separate Station, pairing-code, and paired-device models for device access. |
| Station security | No station or device-session model existed | Add stations with an explicit capability allowlist, short-lived single-use pairing codes, hashed persistent bearer sessions, per-request capability checks, and owner revocation. |
| Payment providers | No merchant connection or payment integration existed | Add per-business provider credentials, owner-only Management controls, Fernet encryption, and an unverified state. Keep payment processing and provider verification explicitly incomplete. |
| QRIS providers | No Midtrans or BCA API existed | Support separate Midtrans and BCA QRIS SNAP credential records. Provider API contracts, onboarding, callbacks, and sandbox/live behavior remain unimplemented until verified against provider infrastructure. |

The schema migration keeps collaborator permissions and invitation token digests. It drops role labels only after owner backfill. Existing WorkspaceAssignment rows are not deleted.
