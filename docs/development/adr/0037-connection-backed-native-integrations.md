# 0037 - Use Connection-Backed Native Integrations

## Status

Accepted, backfilled.

## Context

Some first-party capabilities require a user-authorized external service but do
not belong to model-provider configuration or the external MCP extension
boundary. Gmail is the first such capability. OAuth implementations also share
protocol mechanics while retaining provider-specific endpoints, scopes, and
product policy.

## Decision

Model first-party external services as connection-backed native integrations.
Keep provider domain code under `core/integrations/`, expose thin built-in tool
adapters under `core/tools/`, and route API and UI operations through the
existing System connection surface.

A connection-backed built-in tool is available only when it is globally
enabled and the current principal has a usable connection with the scopes the
capability requires. Apply this gate during shared capability resolution so
chat, delegates, code execution, and workflows receive the same effective
toolset. Do not attach an unavailable tool merely to return a configuration
error when called.

Treat a Google connection as a reusable provider relationship rather than a
Gmail-specific credential. Gmail is its first consumer; later Google
capabilities reuse the connection identity, OAuth grant, account identity,
refresh coordination, and sanitized readiness contract while enforcing their
own required scopes.

Share provider-neutral OAuth primitives for PKCE, state checking, pending-flow
storage, redirect parsing, and completion coordination. Keep provider
endpoints, payloads, grant rules, and deployment-specific flows in their owning
integration.

## Consequences

- Native integrations remain distinct from model providers and MCP servers.
- A configured provider connection does not automatically expose every
  provider-backed capability.
- OAuth protocol fixes can be shared without merging provider policy.
- Credentials, pending state, grants, and account identity remain encrypted
  and principal-owned.
- Gmail remains a first-class built-in tool when ready; large future native
  catalogs may independently choose deferred discovery.

## Evidence

- Implementation plan: `GMAIL_CONNECTOR_IMPLEMENTATION_PLAN.md`
- Current implementation: `core/oauth/`, `core/integrations/google/`, and
  `core/tools/gmail.py`
- Current system map: `docs/development/architecture.md`
- Related decisions: ADR 0007, ADR 0028, and ADR 0034
