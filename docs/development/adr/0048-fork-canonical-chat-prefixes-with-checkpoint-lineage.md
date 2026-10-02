# 0048 - Fork Canonical Chat Prefixes With Checkpoint Lineage

## Status

Accepted.

## Context

A chat fork must reproduce the source conversation at a selected assistant message while remaining independently mutable afterward. Effective history can contain generated recovery-card or session-map context plus retained raw messages, so copying that projection would discard canonical evidence and turn generated context into an ordinary message. A checkpoint authored after the selected branch point can also contain knowledge from the future of that branch.

## Decision

Store each fork as a physical copy of the source session's canonical raw-message prefix through the selected assistant message, preserving canonical sequence indexes and applicable structured tool events. Copy a checkpoint only when its complete author observation boundary is at or before the fork point. Assign copied checkpoints child-owned IDs and record their immediate source checkpoint in metadata.

Checkpoint replacement history records aligned nullable canonical origins. Generated context messages have no origin; retained canonical messages record their source sequence index. This lets browser-visible retained assistant messages identify an unambiguous canonical fork point. Legacy checkpoints resolve origins by exact ordered provider-message equality and expose no fork point when the mapping is ambiguous.

Record logical lineage in session metadata with the immediate parent, canonical branch point, original lineage root, child-owned history boundary, and inherited checkpoint count. Parent and child remain physically isolated; lineage does not define cross-session retrieval ranking or deduplication.

## Consequences

- Fork creation copies raw messages, structured tool events, eligible checkpoints, and lineage in one SQLite transaction.
- Recovery-card forks remain pinned to recovery-card compaction and session-map forks retain inspectable map revisions.
- Checkpoints whose authors observed later messages cannot leak into an earlier branch.
- Fork creation requires no model inference.
- Existing forks that were previously stored as flattened effective history are not rewritten automatically.

## Evidence

- `validation/scenarios/integration/core/chat_session_fork_lineage.py`
- `validation/scenarios/integration/core/chat_history_compaction.py`
- `SESSION_FORK_LINEAGE_IMPLEMENTATION_PLAN.md`
