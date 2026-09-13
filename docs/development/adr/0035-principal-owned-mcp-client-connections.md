# 0035 - Use Principal-Owned MCP Client Connections

## Status

Accepted, backfilled.

## Context

AssistantMD needs to call tools exposed by multiple Model Context Protocol
servers without allowing remote catalogs to displace built-in tools, leak
credentials across principals, or make model-facing tool identities unstable.
MCP servers may be unavailable, slow, or require OAuth, so reconnecting and
listing every server during each agent construction would also make chat
startup unreliable.

## Decision

AssistantMD acts as an MCP client. It does not expose an AssistantMD MCP server.

Store sanitized connection metadata and principal-owned encrypted credentials,
OAuth state, and tokens in separate domain tables within `access.db`.
All configuration, catalog, credential, and tool-call operations derive their
owner from `ExecutionAuthority`; normal APIs neither accept nor expose an owner
selector.

Give each connection an immutable, principal-unique slug. Prefix remote tool
names with that slug so model-facing names remain collision-safe and continue
to identify the same connection after display-name edits or application
restarts. Retired slugs are not reused.

Retain runtime clients by principal, connection identity, and configuration
version. Agent runs borrow settled, frozen catalog views so one unavailable
server does not prevent other capabilities from loading. Runtime shutdown and
configuration invalidation close the corresponding retained resources.

Keep built-in tools as first-class settings-backed capabilities. Filter and
prefix MCP definitions before deferring individual tools behind Pydantic AI
tool search. Once discovered, MCP calls use the same execution ownership, call
budgets, activity hooks, result shaping, and conservative recovery boundaries
as built-in tools.

## Consequences

- MCP support requires explicit client dependencies and runtime lifecycle
  ownership.
- A connection slug is a persistence identifier, not presentation text.
- Tool search limits initial prompt exposure but does not itself defer network
  initialization or catalog settlement.
- Secrets and remote tool metadata remain isolated by principal even while the
  product exposes only the `local-user` interactive principal.
- MCP tool failures remain model-visible without changing the availability or
  recovery contracts of built-in tools.

## Evidence

- Implementation plan: `MCP_SUPPORT_IMPLEMENTATION_PLAN.md`
- Current system map: `docs/development/architecture.md`
- Runtime implementation: `core/mcp/manager.py`, `core/mcp/service.py`, and
  `core/llm/capabilities/mcp_tools.py`
- Related decisions: ADR 0007, ADR 0008, ADR 0015, ADR 0028, and ADR 0034
