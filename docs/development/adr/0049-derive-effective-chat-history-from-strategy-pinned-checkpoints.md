# 0049 - Derive Effective Chat History From Strategy-Pinned Checkpoints

## Status

Accepted.

## Context

Long-running chat sessions eventually exceed a model's useful context budget. AssistantMD must reduce the history sent to the model without deleting canonical messages, breaking provider tool protocols, committing an author result against changed source history, or silently changing an established session's interpretation when installation settings change.

Recovery cards and session maps are different lossy authoring strategies, but they require the same durable reduction boundary. Generated continuity context must remain distinguishable from canonical conversation, and failures must leave the prior effective history usable.

## Decision

Keep canonical messages intact. Derive effective history from an append-only checkpoint replacement plus the remaining canonical suffix, keeping generated continuity text distinguishable from original conversation.

Record separate consumed and observed boundaries: what the checkpoint replaces and what its author could see. This distinction prevents retained-tail knowledge from leaking into an earlier fork.

Reduce only complete, protocol-safe conversational groups. Use a high watermark to trigger reduction; session maps target a low token watermark without a retained-turn floor, while recovery cards retain a configured number of newest turns. Preserve an incomplete active turn.

Author through the governed task executor and commit validated replacements atomically against an unchanged source-history revision. Failure preserves the prior effective history rather than silently switching strategies.

Default to `recovery_card`, with `session_map` optional. The first successful checkpoint pins the session's strategy; installation-setting changes do not reinterpret existing sessions.

Allow explicit per-session upgrades to a current map reconstructed from canonical history. Do not fabricate historical maps that never informed later turns or automatically convert existing sessions.

Keep full canonical history inspectable and retrievable independently of effective model context. Page the ordinary chat timeline rather than loading an unbounded transcript or duplicating it in the map inspector.

## Rationale

Separating immutable canonical history from a replaceable effective projection makes compaction reversible at the evidence layer even though every compact representation is lossy. One generic checkpoint contract lets both strategies share concurrency, failure-safety, forking, inspection, and retrieval invariants without pretending that their authored representations are equivalent.

Pinning prevents an installation-wide settings change from silently changing the semantics of an existing conversation. Distinct consumed and observed boundaries preserve both accurate context projection and safe lineage when an author reads retained messages beyond the evicted prefix.

## Consequences

- Canonical history remains the durable source of truth and can be inspected, searched, forked, or reprojected after compaction.
- Session maps can produce map-only context; preserving an incomplete active turn may prevent meeting the token target.
- Sessions remain stable across settings changes, but conversion requires an explicit inference-backed upgrade.
- Recovery cards and session maps remain lossy, so agents may need canonical transcript retrieval for source verification.
- Cross-session discovery follows the separate lexical-evidence contract in [ADR 0051](0051-lexical-session-discovery-over-canonical-evidence.md); fork-family ranking and vault linkage remain separate memory-system decisions.

## Evidence

- `core/chat/compaction.py`
- `core/chat/context_strategy_upgrade.py`
- `core/chat/chat_store.py`
- `core/tools/session_ops.py`
- `validation/scenarios/integration/core/chat_history_compaction.py`
- `validation/scenarios/integration/core/session_context_strategy_upgrade.py`
- `validation/scenarios/integration/core/stepped_eviction_planner.py`

## Related Decisions

- ADR 0003, Chat Sessions Use Canonical SQLite Storage
- ADR 0012, Chat History Broker
- ADR 0017, Conservative Tool Result Shaping For Compaction
- ADR 0019, Runtime Execution Task Runner
- ADR 0048, Fork Canonical Chat Prefixes With Checkpoint Lineage
- ADR 0050, Source-Grounded Whole-Replacement Session Maps
