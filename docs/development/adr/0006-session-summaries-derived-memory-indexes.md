# 0006 - Treat Session Summaries As Derived Memory Indexes

## Status

Accepted, backfilled.

## Context

AssistantMD needs a way to surface useful prior work without making hidden
memory authoritative or merging unrelated sessions into an opaque project
object. Chat sessions are already durable and vault-scoped, and users can reopen
the original session when they want the canonical record.

## Decision

Model current concrete memory as derived session summaries. Keep canonical chat
history in the chat subsystem. Store summary, intent, retrieval fields, source
provenance, and indexes in the session-summary subsystem. Expose lookup and
summarization through `session_ops` and authoring helpers rather than direct
chat-store access from context scripts.

## Rationale

Session-scoped summaries preserve provenance: a summary points back to a real
chat session and its source history. This avoids premature cross-session merge
logic while still enabling related-session retrieval. Treating generated memory
as candidate context rather than hidden authority keeps the user-facing record
auditable.

## Consequences

- Session summaries can be refreshed or reindexed without rewriting chat
  history.
- Retrieval policy can evolve behind `session_ops` and authoring helpers.
- Context scripts use the chat-history broker and safe history units when they
  need source conversation history.
- Other memory primitives can be added later as separate documented contracts.

## Evidence

- Current system map: `docs/development/architecture.md`,
  `docs/tools/session_ops.md`
- Recovered sources: PR #43 `memory-implementation-plan.md`,
  `memory-sketch.md`, `memory-prd.md`, `memory-experiment-lessons.md`
