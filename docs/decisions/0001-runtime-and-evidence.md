# 0001: Preserve runtime and evidence boundaries

Status: accepted; documents existing architecture. Date: 2026-11-17.

## Context

Veyra already stores tenant data and uses Django templates, PostgreSQL, Redis, Channels, ASGI, pricing/order services and transactional outbox delivery. Product completion must preserve these decisions and existing data.

## Decision

Continue the modular monolith and shared authoritative services. Additive migrations preserve existing tenants. HTTP workflows are the agreed regression seam; PostgreSQL and Redis tests establish database/channel behavior. Browser verification and external-provider acceptance are distinct evidence. A missing subscription fails closed; existing tenants were explicitly grandfathered by migration 0018.

## Alternatives and consequences

A frontend rewrite or database reset would risk working behavior and violate scope. SQLite-only tests cannot establish PostgreSQL locks or Redis delivery. Mocked provider tests are reproducible but do not verify production settlement. Keep missing billing, security, authorization and operational workflows visible in FEATURE-STATUS rather than declaring the product complete.
