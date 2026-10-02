# 0049 - Derive Effective Chat History From Strategy-Pinned Checkpoints

## Status

Accepted.

## Context

Long-running chat sessions eventually exceed a model's useful context budget. AssistantMD must reduce the history sent to the model without deleting canonical messages, breaking provider tool protocols, committing an author result against changed source history, or silently changing an established session's interpretation when installation settings change.

Recovery cards and session maps are different lossy authoring strategies, but they require the same durable reduction boundary. Generated continuity context must remain distinguishable from canonical conversation, and failures must leave the prior effective history usable.

## Decision

Keep canonical chat messages intact and derive effective history from the latest applicable checkpoint replacement followed by the verbatim canonical suffix after that checkpoint's consumed boundary. A checkpoint supersedes a canonical source prefix with a derived replacement. Generated replacement entries have no canonical origin, while a replacement may also carry explicitly origin-linked canonical messages needed for protocol or continuity.

Treat checkpoints as append-only revisions. Each checkpoint records the canonical prefix governed by its replacement, its strategy kind, replacement history, author observation boundary, source-history revision, and strategy-specific metadata. The consumed boundary determines the governed canonical prefix and where the raw suffix resumes; origin-linked messages carried inside the replacement retain their canonical identity. The observation boundary records the newest canonical message available to the author and may extend into the retained suffix; the distinction prevents later-message knowledge from crossing an earlier fork.

Plan reduction over complete protocol-safe conversational groups. Trigger automatic reduction at a configurable high token watermark and preserve at least the configured number of recent conversational turns. Session-map reduction advances its consumed boundary toward a configurable low token watermark; recovery-card reduction summarizes every complete group older than the retained-turn floor. Protocol integrity and the retained-turn floor take precedence over the session-map low-watermark target when a recent group is unusually large.

Resolve a reduction plan against canonical storage and fence authoring and commit with the source-history revision. Authoring runs through the governed execution-task path. Persist the replacement and checkpoint metadata atomically only after the author result and its evidence contract validate. Authoring, validation, cancellation, staleness, or commit failure preserves the prior effective history and does not fall back to another strategy. A strategy change detected before authoring begins may be re-resolved once inside the same governed task.

Use `recovery_card` as the default strategy and make `session_map` optional. Installation configuration selects the strategy only while a session has no checkpoint. The first successful checkpoint pins the session to that strategy; later configuration changes do not reinterpret existing history.

Use one optional compaction-author model setting for both strategies, falling back to the default chat model when it is unset. Allow an explicit per-session upgrade from a recovery-card checkpoint to a session-map checkpoint. The upgrade reconstructs one honest current map from canonical history and does not fabricate historical maps that never informed later turns. Do not automatically downgrade or bulk-convert sessions.

Keep canonical transcript search and bounded window retrieval available through `session_ops`. Recovery guidance may cue retrieval when exact wording, provenance, or omitted detail matters.

## Rationale

Separating immutable canonical history from a replaceable effective projection makes compaction reversible at the evidence layer even though every compact representation is lossy. One generic checkpoint contract lets both strategies share concurrency, failure-safety, forking, inspection, and retrieval invariants without pretending that their authored representations are equivalent.

Pinning prevents an installation-wide settings change from silently changing the semantics of an existing conversation. Distinct consumed and observed boundaries preserve both accurate context projection and safe lineage when an author reads retained messages beyond the evicted prefix.

## Consequences

- Canonical history remains the durable source of truth and can be inspected, searched, forked, or reprojected after compaction.
- Effective context can remain substantially smaller than the canonical transcript without presenting generated continuity text as canonical conversation.
- A session's context semantics remain stable across installation-setting changes.
- Sessions below the high watermark may have no checkpoint or session map.
- Very large recent turns may keep session-map effective history above the low watermark to preserve conversational and provider-protocol continuity.
- Recovery cards and session maps remain lossy, so agents may need canonical transcript retrieval for source verification.
- The explicit recovery-card-to-session-map upgrade spends inference only for sessions selected by the user and preserves the existing recovery checkpoint if authoring or commit fails.
- Cross-session discovery, fork-family ranking, vault linkage, and replacement of nightly session summaries remain separate memory-system decisions.

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
