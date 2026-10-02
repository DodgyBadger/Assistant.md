# 0049 - Pin Compaction Strategies To Session Checkpoints

## Status

Accepted.

## Context

Long-running chat sessions eventually exceed a model's useful context budget. AssistantMD must reduce the effective history without deleting canonical messages, silently changing an established session's interpretation when installation settings change, or treating generated continuity context as canonical evidence.

Recovery cards provide a compact narrative replacement for an older canonical prefix. Session maps provide a more structured replacement with inspectable revisions and canonical message references. Both are lossy authoring strategies, and neither can guarantee that every detail or source relationship remains in effective context. Canonical transcript retrieval therefore remains necessary regardless of the selected strategy.

## Decision

Keep canonical chat messages intact and model effective history as a derived checkpoint replacement plus a verbatim recent tail. A checkpoint records the canonical prefix it replaces, its author observation boundary, replacement history, strategy kind, and strategy-specific metadata.

Use `recovery_card` as the default strategy and make `session_map` optional. Installation configuration selects the strategy only while a session has no checkpoint. The first successful checkpoint pins the session to that strategy; later configuration changes do not reinterpret existing history.

Apply automatic reduction at a configurable high token watermark and evict toward a configurable low token watermark while preserving at least the configured number of recent conversational turns. A retained-turn floor takes precedence over reaching the low watermark when recent turns are unusually large.

Use one optional compaction-author model setting for both strategies, falling back to the default chat model when it is unset. Authoring runs through the governed execution-task path and checkpoint mutation occurs only after the author result and its evidence contract validate.

Allow an explicit per-session upgrade from a recovery-card checkpoint to a session-map checkpoint. The upgrade reconstructs one honest current map from canonical history and does not fabricate historical maps that never informed later turns. Do not automatically downgrade or bulk-convert sessions.

Keep canonical transcript search and bounded window retrieval available through `session_ops`. Recovery guidance may cue retrieval when exact wording, provenance, or omitted detail matters. Session-map references aid inspection and grounding but are not treated as an exhaustive evidence index.

## Consequences

- Canonical history remains the durable source of truth and can be inspected, searched, forked, or reprojected after compaction.
- Effective context can remain substantially smaller than the canonical transcript without presenting generated continuity text as canonical conversation.
- A session's context semantics remain stable across installation-setting changes.
- Sessions below the high watermark may have no checkpoint or session map.
- Very large recent turns may keep effective history above the low watermark to preserve conversational continuity.
- Recovery cards and session maps remain lossy, so agents may need canonical transcript retrieval for source verification.
- The explicit V1-to-V2 upgrade spends inference only for sessions selected by the user and preserves the existing recovery checkpoint if authoring or commit fails.
- Cross-session discovery, fork-family ranking, vault linkage, and replacement of nightly session summaries remain separate memory-system decisions.

## Evidence

- `core/chat/compaction.py`
- `core/chat/context_strategy_upgrade.py`
- `core/chat/chat_store.py`
- `core/memory/session_map/`
- `core/tools/session_ops.py`
- `validation/scenarios/integration/core/chat_history_compaction.py`
- `validation/scenarios/integration/core/session_map_checkpoint.py`
- `validation/scenarios/integration/core/session_context_strategy_upgrade.py`
- `validation/scenarios/integration/core/session_map_retained_evidence.py`

## Related Decisions

- ADR 0003, Chat Sessions Use Canonical SQLite Storage
- ADR 0012, Chat History Broker
- ADR 0017, Conservative Tool Result Shaping For Compaction
- ADR 0019, Runtime Execution Task Runner
- ADR 0048, Fork Canonical Chat Prefixes With Checkpoint Lineage
