# 0003 - Store Chat Sessions Canonically In SQLite

## Status

Accepted, backfilled.

## Context

Chat sessions need durable replayable history for session resume, cancellation
safety, history retrieval, summaries, tool-event inspection, compaction, and
transcript export. Markdown transcripts are useful for users but are too lossy
to be the canonical runtime state.

## Decision

Store chat sessions and provider-native messages canonically in
`system/chat_sessions.db`. Treat markdown transcripts as optional exports
derived from the database. Keep structured tool-call and tool-result events in
the chat store so future readers can inspect tool activity without relying only
on flattened text.

## Rationale

Provider-native message JSON preserves replay fidelity and protocol details that
markdown cannot. A dedicated SQLite store keeps chat lifecycle concerns separate
from cache, scheduler, and vault-state databases. Derived projections such as
`content_text`, transcript markdown, and session summaries can be rebuilt or
refined without changing the canonical message record.

## Consequences

- `ChatStore` owns the durable session/message contract.
- Session IDs are permanently bound to a vault.
- Transcript export reads from the store instead of being the storage path.
- History compaction records replay checkpoints rather than deleting archival raw
  message rows from the canonical tables.

## Evidence

- Current system map: `docs/development/architecture.md`
- Recovered sources: PR #40 `chat_session_sqlite_store_plan.md`, PR #41
  `CHAT_HISTORY_COMPACTION_SPEC.md`, PR #43
  `session-compaction-checkpoint-implementation-plan.md`
