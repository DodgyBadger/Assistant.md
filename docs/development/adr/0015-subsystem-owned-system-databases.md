# 0015 - Declare System Databases By Subsystem Ownership

## Status

Accepted, backfilled.

## Context

AssistantMD persists several kinds of runtime state: chat sessions, cache,
processed-file state, vault state, session summaries, goals, ingestion jobs, and
scheduler jobs. These stores have different owners, lifecycles, migration
concerns, and operational semantics. Earlier implicit database naming allowed
schema bleed between unrelated stores.

## Decision

Subsystem ownership is logical and does not require one physical file per owner.
State that must change atomically may share a SQLite file while domain modules
retain explicit table and policy ownership. ADR 0046 applies this rule to access
state.

Declare known system databases centrally in `core/database.py`, including name,
owner, and description. Keep subsystem data in separate SQLite files when the
lifecycle and ownership boundaries are distinct. Require table creation to be
explicit for the intended database instead of calling broad metadata creation
against arbitrary engines.

## Rationale

Separate database files keep subsystem ownership visible and reduce accidental
coupling. A central registry still gives contributors one place to discover
which system stores exist and how paths should be resolved. Explicit table
creation prevents unrelated SQLAlchemy models from being created in the wrong
database.

## Consequences

- New system databases should be registered with ownership metadata.
- Modules should resolve database paths through declared helpers.
- Shared SQLAlchemy metadata does not imply shared database files.
- Consolidation means clearer ownership and helpers, not merging all state into
  one monolithic database.
- Database migrations and cleanup remain subsystem-specific unless a shared
  migration need is explicitly introduced.

## Evidence

- Current contract: `core/database.py`,
  `docs/development/architecture.md`
- Recovered sources: PR #40 `database_layer_consolidation_plan.md`,
  `chat_session_sqlite_store_plan.md`, PR #42 `vault_state.md`
