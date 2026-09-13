# 0038 - Use Principal-Owned Multi-Connection Identity And Explicit Defaults

## Status

Accepted, backfilled.

## Context

A principal may authorize multiple accounts for the same provider. Mutable
display names cannot safely identify persisted credentials, OAuth attempts,
scheduled workflows, or model tool arguments. When callers omit an account,
selection must also be deterministic and must not silently combine data from
personal and work resources.

## Decision

Represent each configured connection with an immutable UUID, an immutable
principal-unique readable slug, and a mutable principal-unique display name.
Scope metadata, credentials, pending OAuth state, tokens, account identity, and
capability preferences by principal and connection ID. Normal APIs derive the
principal from execution authority and never accept owner IDs.

For providers whose tools support an omitted connection selector, maintain one
explicit default connection whenever the principal has any connections. The
first connection becomes the default. Changing the default is one metadata
transaction. Deleting a default while alternatives remain requires the caller
to choose its replacement.

The Gmail tool accepts a readable connection slug. Omitting it selects the
principal's default; supplying it selects that exact connection. Never search
or merge all accounts implicitly. Include bounded ready-connection context in
tool instructions so the model can select an account or ask the user when the
intended identity is unclear.

Use one stable installation callback path per OAuth provider. Cryptographic
state and connection-scoped pending records resolve callbacks to the initiating
connection, allowing concurrent authorization attempts without callback paths
becoming connection identities.

## Consequences

- Renaming a connection does not move credentials or invalidate persisted
  references.
- Slugs and IDs are durable contracts and retired slugs must not be reused.
- Scheduled and interactive behavior remains deterministic when more accounts
  are added.
- Default deletion requires an explicit product interaction instead of a
  silent behavior change.
- Existing singleton provider state requires a bounded ownership-preserving
  migration into the collection model.

## Evidence

- Implementation plan: `CONNECTIONS_UI_IMPLEMENTATION_PLAN.md`
- Current implementation: `core/connections/`,
  `core/integrations/google/connection.py`, and `core/tools/gmail.py`
- Current system map: `docs/development/architecture.md`
- Related decisions: ADR 0015, ADR 0028, ADR 0034, ADR 0035, and ADR 0037
