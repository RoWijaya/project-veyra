# 0002: Allowlisted operational logs and dependency readiness

Status: accepted. Date: 2026-11-17.

## Context

Guest verification, invitation and table URLs contain bearer credentials. Generic request/exception logging can leak them. Liveness alone cannot show whether the app can use its authoritative database and Channels dependency.

## Decision

Emit allowlisted JSON fields, route patterns and exception class names; omit arbitrary messages, raw paths, bodies and exception text. Return validated/generated correlation IDs. Add separate dependency readiness that checks PostgreSQL and Redis and exposes only aggregate 200/503 status. Disable full-URL ASGI/proxy access logs. Log outbox delivery failures without copying exception text into durable state.

## Alternatives and consequences

Regex-based secret scrubbing can miss unknown token formats. Allowlisting is safer but removes diagnostic stack/message detail; protected error tracking and richer safe diagnostics remain work. Sentry integration would require an external destination and data review; it is not configured. Readiness checks verify connectivity, not worker health, migrations, provider availability or whole-product acceptance.
