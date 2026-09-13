# 0039 - Enforce MCP Network Policy At Socket Connection Time

## Status

Accepted.

## Context

Validating an MCP URL or an HTTP request before a transport opens its socket
leaves a DNS rebinding interval: the hostname can resolve differently during
the actual connection. Rewriting URLs to IP literals would avoid a second DNS
lookup but would break HTTP origin, connection pooling, TLS SNI, and certificate
verification semantics.

## Decision

Enforce MCP and MCP OAuth address policy inside a dedicated HTTPCore async
network backend at `connect_tcp` time. Resolve the original hostname once for
that socket attempt, classify the complete result, reject prohibited or mixed
resolution sets, and pass only approved numeric addresses to the delegated
network backend.

Keep the original hostname above the network backend so it remains
authoritative for HTTP `Host`, connection-pool origin, TLS SNI, and certificate
verification. If multiple approved addresses are supported, attempt only the
captured set in deterministic order within the original timeout budget.

Reject Unix sockets, redirects, credential-bearing URLs, ambient proxies, and
proxy-environment inheritance. Preserve cancellation and normal timeout/error
classes. Retain request hooks as defense in depth for URL scheme, credentials,
and OAuth-discovered endpoint checks, not as the authoritative DNS control.

Declare a bounded direct HTTPCore dependency because the socket boundary is an
intentional integration rather than an incidental transitive dependency.

## Consequences

- MCP transport coverage must verify numeric connect targets and original-host
  TLS behavior against the supported HTTPCore version.
- A newly prohibited DNS result blocks the connection even when an earlier
  request-level validation succeeded.
- Local and private HTTPS remain supported. Local/private HTTP requires an
  explicit per-connection acknowledgement; public HTTP remains prohibited.
- Updating the lower-level transport dependency requires focused compatibility
  review.

## Evidence

- Implementation plan: `BRANCH_HARDENING_IMPLEMENTATION_PLAN.md`, Stage 1
- Planned implementation boundary: `core/mcp/network.py`
- Current system map: `docs/development/architecture.md`
