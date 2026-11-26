# Integrations

[Environment reference](ENVIRONMENT.md) lists formats and which services need each configuration. [Deployment](DEPLOYMENT.md) describes how to accept a hosted environment.

| Integration | Application behavior | Acceptance boundary |
| --- | --- | --- |
| PostgreSQL and Redis | PostgreSQL is authoritative. Channels and shared non-debug cache use Redis. See [real-time flow](REALTIME.md). | PostgreSQL 17 and Redis 7 local verification. Hosted PostgreSQL TLS and compatible Valkey behavior need acceptance. |
| Account email | Console in debug without Resend, SMTP in non-debug without Resend, or the [Resend backend](../core/email_backend.py) when an API key is set. | Local console/test mail and mocked HTTPS tests. Sender authorization, real delivery, monitoring and durable retry remain open. |
| Google identity | [Identity views](../core/identity_views.py) use a configured web client and exact callback, including explicit linking through account security. | Authlib validates locally issued signed responses through simulated transport. Actual Google consent remains open. Requires authorized client and callback. |
| Authenticator MFA | [Identity](../core/identity.py) uses a dedicated key and local TOTP validation and recovery codes. | Local flow verified. Physical authenticator and full screen-reader acceptance remain open. This flow has no hosted identity provider dependency. |
| Merchant QRIS and platform billing | Tenant Midtrans and BCA SNAP adapters and the separate platform Midtrans gateway. | Local and simulated evidence only. See [payments](PAYMENTS.md) for merchant boundaries and unresolved operations. |
| Supabase Storage | Optional private content-addressed archive for normalized product, campaign, and ordering design uploads. The app serves authorized PostgreSQL bytes. | [Adapter tests](../core/test_staging_adapters.py) simulate responses. Hosted key, bucket permissions, delivery and recovery remain open. |
| Operational printing | The scoped station API exposes durable jobs, acknowledgement and controlled reprints. | [Device contract](STATIONS-AND-DEVICE-API.md) and browser receipt output verified locally. Actual print transfer and recovery need an approved client, device and printer. |

Provider reference links describe the external systems rather than evidence of a Veyra deployment. [Google OIDC](https://developers.google.com/identity/openid-connect/openid-connect) · [Supabase database connection](https://supabase.com/docs/guides/database/connecting-to-postgres) · [Private storage buckets](https://supabase.com/docs/guides/storage/security/access-control) · [Resend email API](https://resend.com/docs/api-reference/emails/send-email)
