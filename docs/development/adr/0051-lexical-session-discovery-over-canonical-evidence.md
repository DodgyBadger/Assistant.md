# 0051 - Discover Sessions Through Lexical Canonical Evidence

## Status

Accepted. Supersedes [ADR 0006](0006-session-summaries-derived-memory-indexes.md).

## Context

Cross-session discovery should work without embedding credentials or a scheduled summarization workflow. Canonical transcripts already cover short sessions and recent turns, while compaction maps provide concise, source-linked orientation and preserve earlier topics across checkpoint revisions. Maintaining a separate authored summary creates another inference, storage, indexing, and lifecycle path without being necessary for lexical discovery.

## Decision

Use a memory-layer discovery service to compose lexical hits from canonical session titles/workspaces, every session-map checkpoint, and canonical transcripts. Keep FTS projections beside their owning chat records, backfill without inference, and maintain them atomically with canonical changes. Index map narrative and entry text rather than serialized schema or author prompts.

Filter sessions by execution authority and optional workspace before exposing candidate evidence. Admit at most one hit per source per session, combine source ranks, and return bounded session identities and evidence. Identify historical map hits explicitly. Read maps through an authorized checkpoint operation and verify exact details through canonical transcript anchors.

Compaction remains independent of discovery. Search does not author maps, change pinned strategies, advance eviction boundaries, or synthesize checkpoints for short sessions. Sessions without maps and messages after the latest checkpoint remain discoverable through canonical text.

Retire summary generation, summary-selection helpers, summary API/UI surfaces, nightly summary seeding, and unused vector integration. Archive only recognizable packaged workflow copies; preserve authored customizations with a review warning and reject retired dependencies explicitly. A mandatory integrity-checked migration backup precedes scoped removal of known legacy summary tables. Preserve unknown tables, migration bookkeeping, canonical history, maps, and configured provider/model/secret records. Do not create the retired database on fresh installations.

Embedding model aliases and vector dimensions have no supported consumer and are excluded from model configuration. The packaged settings contain no embedding alias; existing embedding-capable aliases are omitted from active configuration and removed by backed-up settings repair. Shared providers, secrets, and supported chat/decision mappings remain intact.

Custom foreign-key dependencies on retired tables block retirement before any drop and emit an actionable diagnostic; dependent rows and migration bookkeeping remain unchanged. Unreadable or unrecognizable authored workflow files are preserved with review diagnostics without preventing unrelated template seeding.

## Consequences

- Core session discovery requires neither a model call nor an embedding API key.
- Lexical search does not promise semantic similarity, synonyms, or paraphrase recall.
- Maps supply candidate orientation, not canonical truth or exhaustive evidence; historical hits may describe superseded work.
- Checkpoint count does not contribute extra source votes. Fork-family deduplication remains a separate policy.
- Search indexes are rebuildable derived state, while chat records and checkpoint provenance remain authoritative.
- No nightly workflow is required to make short or newly active sessions discoverable.
- Future semantic retrieval, vault/session links, and background discovery snapshots can extend the focused discovery boundary without changing compaction.

## Evidence

- `core/chat/schema.py` and `core/chat/chat_store.py`
- `core/chat/transcript_retrieval.py`
- `core/memory/session_discovery.py`
- `core/memory/retirement.py`
- `core/tools/session_ops.py`
- `validation/scenarios/integration/core/session_discovery.py`
- `validation/scenarios/integration/core/session_discovery_cutover.py`
- `validation/scenarios/integration/core/session_summary_data_retirement.py`
