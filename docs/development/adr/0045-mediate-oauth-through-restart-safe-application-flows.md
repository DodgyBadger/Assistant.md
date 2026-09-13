# 0045 - Mediate OAuth Through Restart-Safe Application Flows

## Status

Accepted.

## Context

AssistantMD supports external connections in self-hosted deployments that may
run headlessly, behind a reverse proxy, or at an origin different from the
address observed by FastAPI. An OAuth library flow that opens a browser or
starts a temporary loopback callback listener in the backend container does not
fit those deployments. Process-local pending state also loses an in-progress
authorization whenever AssistantMD restarts.

MCP servers and native providers such as Google both use OAuth authorization
code flows, but they do not share all provider policy. MCP may discover
authorization servers and dynamically register a client; Google uses fixed
endpoints, offline access, incremental consent, account identity, and its own
scope semantics. Duplicating state, PKCE, encrypted storage, callback, and
refresh mechanics would create security-sensitive drift, while forcing every
provider through one undifferentiated configuration model would erase necessary
differences.

## Decision

Mediate connection OAuth through AssistantMD APIs and the System connection UI.
Do not let an MCP or provider SDK open a browser, own a temporary callback
listener, or keep the only copy of authorization state in process memory.

Persist pending authorization attempts in the encrypted principal-owned secrets
store. Scope them to the connection and provider, bind them to cryptographic
state and PKCE, expire them promptly, consume them once, and retain enough
validated state to complete safely after an application restart.

Support two completion paths over the same pending-attempt contract:

- a normal browser callback at one stable provider-level route; and
- manual submission of the returned redirect URL for headless or unreachable
  callback deployments.

Use the canonical public origin from ADR 0036 when constructing an externally
reachable callback. Cryptographic state resolves the initiating principal and
connection; connection IDs are not embedded into callback-route identity and a
browser-supplied host cannot redefine the callback origin.

Share provider-neutral OAuth mechanics for state and PKCE, expiring pending
records, redirect validation, encrypted record storage, token exchange and
refresh, safe token merging, and sanitized errors. Keep discovery, client
registration, endpoints, authorization parameters, scopes, account checks,
resource indicators, and provider-specific token rules in explicit MCP or
native-provider adapters.

Store tokens, client registration, and pending state under principal- and
connection-scoped encrypted identities. OAuth secrets, codes, state, PKCE
verifiers, and tokens never enter model context, tool results, normal connection
metadata, URLs produced for logging, or unsanitized diagnostics.

## Consequences

- OAuth setup works for local, proxied, containerized, and headless
  installations without giving the backend control of a user's browser.
- An application restart does not necessarily invalidate a still-live
  authorization attempt.
- One stable callback route supports multiple concurrent connections because
  signed pending state identifies the initiator.
- MCP and native connections reuse reviewed protocol mechanics while retaining
  their distinct provider policies.
- Adding another OAuth-backed integration requires a provider adapter rather
  than another end-to-end authorization framework.
- Pending-flow migrations, token refresh, disconnect, credential replacement,
  and deletion require explicit concurrency and lifecycle fencing.
- Manual redirect completion increases deployment compatibility but must apply
  the same state, origin, expiry, ownership, and single-use validation as the
  browser callback.

## Evidence

- Shared OAuth foundation: `core/oauth/`
- MCP adapter and encrypted storage: `core/mcp/oauth.py` and
  `core/mcp/oauth_storage.py`
- Google adapter: `core/integrations/google/oauth.py`
- Canonical callback origin: ADR 0036
- Connection ownership and identity: ADR 0035, ADR 0037, and ADR 0038
- Google credential mutation fencing: ADR 0041
- Implementation plans: `MCP_SUPPORT_IMPLEMENTATION_PLAN.md` and
  `GMAIL_CONNECTOR_IMPLEMENTATION_PLAN.md`
