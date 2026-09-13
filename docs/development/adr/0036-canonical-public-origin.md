# 0036 - Use A Canonical Public Origin For External Application URLs

## Status

Accepted, backfilled.

## Context

AssistantMD may run behind a reverse proxy whose browser-visible origin differs
from the address observed by FastAPI. OAuth callbacks and future externally
shared application URLs must not depend on untrusted request hosts, forwarded
headers, or duplicated frontend URL construction.

## Decision

Use optional installation setting `ASSISTANTMD_PUBLIC_URL` as the canonical
externally reachable application origin. Treat it as typed infrastructure
configuration loaded once into the runtime, not as user settings, principal
state, or secret material.

Accept HTTPS origins and loopback HTTP development origins. Reject credentials,
queries, fragments, non-root paths, missing hosts, and unsupported schemes.
Centralize normalization, same-origin comparison, and safe construction from
application-relative paths.

When configured, the canonical origin is authoritative. Request `Host`,
`Forwarded`, `X-Forwarded-*`, and browser-supplied origins cannot override it.
When it is absent, an interactive feature may use an explicitly validated
browser-origin fallback and must report that source. Do not persist the
fallback as installation configuration.

Consumers opt into canonical external URL construction. Do not globally alter
FastAPI request URL behavior or enable permissive proxy-header trust.

## Consequences

- Reverse-proxy deployments must configure their externally visible origin and
  restart the application after changing it.
- Missing configuration remains compatible with local interactive use but is
  visible in sanitized system diagnostics.
- Invalid configured values fail during startup rather than producing an
  incorrect security-sensitive URL later.
- TLS termination, authentication, DNS, certificates, and proxy routing remain
  deployment responsibilities.
- Provider-defined loopback and device-code callbacks remain separate policies.

## Evidence

- Implementation plan: `PUBLIC_URL_IMPLEMENTATION_PLAN.md`
- Current runtime contract: `core/runtime/public_url.py`
- Current system map: `docs/development/architecture.md`
