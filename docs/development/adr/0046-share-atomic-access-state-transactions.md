# 0046 - Share Atomic Access-State Transactions

## Status

Accepted.

Supersedes [ADR 0040](0040-durable-cross-database-connection-mutations.md).

## Context

MCP and native Google connection mutations frequently change sanitized metadata
and encrypted credentials as one logical operation. Separate SQLite files made a
process crash between commits observable and required mutation journals, staging
namespaces, and restart reconciliation.

## Decision

Store encrypted credentials, MCP connection metadata, and native connection
metadata in `system/access.db`. Domain modules continue to own their tables and
policy. `core.access_store` owns connection configuration, the ordered schema,
and short-lived `BEGIN IMMEDIATE` write transactions. Composed domain helpers use
the caller's connection and never commit or close it.

Keep encryption, principal ownership, immutable connection identities, OAuth
generation and credential guards, and post-commit MCP runtime invalidation. Never
hold a write transaction across a network request, subprocess, or asynchronous
wait.

MCP mutations acknowledge stale marking on the manager's owning event loop after
commit. Existing leases finish against their captured configuration; idle client
cleanup is asynchronous and its failures are observed. A failed invalidation
handoff reports that the durable change committed, without replaying the mutation.
Credential resolution checks the captured configuration version in the same read
snapshot as the credential, so an old endpoint cannot receive a newer secret.

OAuth storage checks the connection's current revision alongside each encrypted
read or write. Authorization attempts retain their captured revision across
external requests, and pending state is consumed conditionally once. Metadata
and credential transactions do not replace these external-request guards.

The supported released upgrade imports plaintext `system/secrets.yaml` directly
into `access.db`. Intermediate development databases are neither migrated nor
deleted.

When credentials are locked, runtime startup leaves the access database untouched
and disables connection API access. Migration diagnostics report access-state
inspection as unavailable, including when the file is malformed; other managed
databases remain independently inspectable and migratable.

## Consequences

- Metadata and encrypted state commit or roll back together.
- Connection mutation journals and deferred credential-purge ledgers are no
  longer part of the runtime contract.
- SQLite remains embedded and the file-first deployment model is unchanged.
- The installation key and `access.db` must be backed up together.
- External OAuth races still require guards because local transactions cannot
  span provider requests.
