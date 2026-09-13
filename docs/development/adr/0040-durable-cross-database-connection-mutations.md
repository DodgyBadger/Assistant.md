# 0040 - Coordinate Cross-Database Connection Mutations With A Durable Saga

## Status

Superseded by [ADR 0046](0046-share-atomic-access-state-transactions.md).

## Context

MCP connection metadata and encrypted credentials have distinct subsystem
owners and live in separate SQLite databases. Creating, reconfiguring, or
deleting a connection can therefore commit in one store and fail in the other.
Optimistic cleanup ordering cannot guarantee that partially credentialed state
stays unavailable after a crash.

## Decision

Keep `mcp.db` and `secrets.db` separate under ADR 0015. Do not use SQLite
`ATTACH`, merge the stores, or claim cross-database atomicity.

Coordinate multi-store connection changes with durable, sanitized mutation
records in `mcp.db`. Register a staging-cleanup record before writing new secret
values under operation-scoped encrypted identities, then commit the intended
non-secret metadata transition and lifecycle together with promotion to intent.
Apply one atomic secrets operation, finalize metadata and its configuration
version, restore the active lifecycle, remove the record, and invalidate retained
runtime state. Recovery of a staging-only record removes its encrypted namespace
and preserves the prior metadata state.

Only active connections without unresolved mutations are runtime-eligible.
Run bounded idempotent reconciliation after managed migrations and secrets
bootstrap but before the MCP manager starts. Mutation entry points reconcile
their target before accepting another change.

Mutation records contain operation and connection identity, mutation kind,
sanitized desired metadata or action, lifecycle state, attempt count,
timestamps, and sanitized error class. They never contain credentials, tokens,
OAuth state, authorization URLs, or raw secret-bearing payloads.

A successful mutation response means metadata, required secret effects,
configuration version, runtime invalidation, and terminal logging have all
settled. Recovery applies metadata/version transitions at most once and
preserves immutable IDs and slug reservations.

OAuth persistence uses a cross-database fence rather than a process-local lock.
The active MCP row and an encrypted marker share a random token. OAuth writes
compare that marker and persist their value in one secrets-store transaction.
OAuth-sensitive mutations make the MCP row non-active before atomically rotating
the marker and deleting OAuth state, so an adapter issued before the mutation
cannot recreate state after cleanup.

## Consequences

- Connection persistence gains explicit pending and deleting lifecycle states
  plus a mutation journal.
- Unresolved operations reduce availability but never expose partially applied
  configuration.
- Secret storage needs atomic batch delete, promotion, and authenticated
  relocation primitives.
- Every durable boundary requires failure-injection and restart-convergence
  coverage.
- Operational diagnostics can report retryable mutation state without exposing
  mutation payloads or secret material.

## Evidence

- Implementation plan: `BRANCH_HARDENING_IMPLEMENTATION_PLAN.md`, Stage 4
- Related decisions: ADR 0015, ADR 0028, ADR 0034, and ADR 0035
- Planned implementation boundaries: `core/mcp/service.py`,
  `core/secrets/service.py`, and `core/runtime/bootstrap.py`
