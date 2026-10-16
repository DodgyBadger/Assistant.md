# 0051 - Replace Session Summaries with Canonical and Map-Based Discovery

## Status

Accepted. Supersedes [ADR 0006](0006-session-summaries-derived-memory-indexes.md).

## Context

Session summaries were a means to cross-session discovery, not an end in themselves. Their nightly workflow and embedding setup added operational dependencies and a separate representation of conversations to maintain. Canonical transcripts and compaction-map checkpoints already provide searchable evidence, so discovery need not depend on another generated summary.

## Decision

Use lexical search over session metadata, canonical transcripts and map checkpoints for cross-session discovery. Retire the separate session-summary subsystem, nightly summary generation and unused embedding machinery.

Keep discovery independent of compaction: sessions without maps and messages after the latest checkpoint remain searchable. Maps provide concise orientation, including earlier topics through historical revisions; canonical messages remain the source for verification.

Retirement must protect canonical history, maps and user customizations, with backed-up removal of retired data rather than silent destructive cleanup.

## Alternatives

- Retain summaries alongside maps: preserves the existing semantic-search path but keeps duplicate memory representations and their maintenance burden without a demonstrated need.

- Generate maps nightly for discovery: reuses the schema but still makes discoverability depend on scheduled inference. Transcript search already covers sessions that have not needed compaction.

- Retain embeddings as a required discovery dependency: offers semantic recall, but the observed need does not justify the additional setup and lifecycle complexity.

## Consequences

- Core session discovery requires no model call, embedding API key or nightly workflow.
- Lexical search does not promise semantic similarity, synonyms, or paraphrase recall.
- Maps supply candidate orientation, not canonical truth or exhaustive evidence; historical hits may describe superseded work.
- Search indexes are rebuildable derived state, while chat records and checkpoint provenance remain authoritative.
- This trades semantic recall for simpler setup and fewer failure paths; it does not rule out optional semantic retrieval or broader vault/session memory later.

## Evidence

- `core/chat/schema.py` and `core/chat/chat_store.py`
- `core/chat/transcript_retrieval.py`
- `core/memory/session_discovery.py`
- `core/memory/retirement.py`
- `core/tools/session_ops.py`
- `validation/scenarios/integration/core/session_discovery.py`
- `validation/scenarios/integration/core/session_discovery_cutover.py`
- `validation/scenarios/integration/core/session_summary_data_retirement.py`
