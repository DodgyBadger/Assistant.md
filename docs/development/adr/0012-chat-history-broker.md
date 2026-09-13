# 0012 - Use Chat History Broker For Shared Conversation Access

## Status

Accepted, backfilled.

## Context

Session summaries, context templates, tools, compaction, and chat execution all
need access to conversation history. That history may come from canonical
SQLite storage or from an in-flight run. It also contains provider-native tool
call and tool return structures that must remain protocol-safe when reused.

## Decision

Use `ChatHistoryService` as the shared broker over conversation history. Expose
normalized safe units to tools and authoring helpers, including atomic tool
call/return exchanges. Keep direct chat-store access inside lower-level chat and
memory services rather than letting context scripts or general tools query chat
tables directly.

## Rationale

The broker keeps policy close to history consumption. It can choose persisted or
in-memory providers, return effective compacted history by default, and preserve
provider protocol requirements while still giving callers curated history units.
This avoids duplicating history-loading and tool-pair handling in every feature
that needs prior conversation context.

## Consequences

- Context scripts should use `retrieve_history(...)` and
  `assemble_context(...)`.
- Session summarization uses the broker when deriving summaries from source
  conversation history.
- Compacted sessions can expose effective replay history without every caller
  knowing checkpoint details.
- Lower-level raw access remains an internal service concern.

## Evidence

- Current system map: `docs/development/architecture.md`
- Recovered sources: PR #43
  `session-compaction-checkpoint-implementation-plan.md`,
  `memory-implementation-plan.md`, PR #41 `CHAT_HISTORY_COMPACTION_SPEC.md`
