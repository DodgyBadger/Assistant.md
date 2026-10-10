# 0050 - Use Source-Grounded Whole-Replacement Session Maps

## Status

Accepted.

## Context

Repeated narrative compaction can preserve central facts while gradually changing emphasis, interpretation, and the causal story that connects decisions. A more structured representation can make current state, uncertainty, and supersession clearer, but a rigid ledger can also crystallize exploration into commitments or imply that its selected references exhaust the conversation.

Compaction V2 needs a representation that remains small enough to replace an old transcript prefix, carries enough narrative orientation for continued conversation, distinguishes user commitments from assistant proposals and tool observations, and preserves a trustworthy path back to canonical evidence. It must remain honest about being a lossy authored projection rather than a second source of truth.

## Decision

Represent V2 continuity as a bounded, sparse map of typed current-state entries with a concise session-level trajectory when entries exist. Preserve the conversation's throughline across revisions, not just the newest interval. Distinguish exploration, proposals and commitments, and retain uncertainty rather than forcing every conversation into a project ledger.

Author each revision as a complete replacement from the previous map and eligible canonical evidence, rather than accumulating a patch log. Carry forward relevant state and provenance while allowing entries to change or disappear as the conversation develops.

Require canonical message references for trajectory and entry claims. References provide a route to evidence, not proof of semantic correctness or an exhaustive account of relevant history. Validate maps before persistence and on read; malformed maps must not replace usable context or expose private input through diagnostics.

Exclude retrieval-tool envelopes from source evidence so repeated search results cannot become self-reinforcing memory. Retrieved original transcript fragments are admissible only when verified against canonical messages.

Use the checkpoint and canonical-history contract in [ADR 0049](0049-derive-effective-chat-history-from-strategy-pinned-checkpoints.md). Keep map revisions and provenance inspectable, with transcript retrieval available for omitted detail or disputed claims.

Author when the reduction policy selects a new eviction boundary, without a classifier gate. Experiments did not establish enough benefit from that extra decision layer to justify its dependency and tuning complexity.

## Rationale

A small typed map makes current state and supersession easier to inspect than an unconstrained narrative summary, while the trajectory preserves the causal thread that a field-only schema tends to lose. Evidentiary basis and conservative admission directly address the observed risk that brainstorming, assistant suggestions, or tentative options become false decisions or goals.

Whole-map replacement avoids an ever-growing chain of active, superseded, and closed patch operations in model context. Canonical source ranges and transcript retrieval make the compact representation auditable without requiring it to carry every historical fact or quotation. Governed authoring and fail-closed validation keep malformed or stale maps from becoming effective context.

## Consequences

- The session map is derived and lossy. It is not canonical truth, a complete transcript summary, or an event ledger. Its text can supply candidate evidence for cross-session lexical discovery under [ADR 0051](0051-lexical-session-discovery-over-canonical-evidence.md).
- Map references are evidence routes rather than an exhaustive declaration of relevant history.
- Complete replacement bounds model-facing map growth, while append-only checkpoints preserve revision history outside the active context.
- Structured authoring, validation and model inference add complexity and cost at reduction boundaries.
- A map may omit narrative texture or emerging ideas, or misinterpret evidence; canonical retrieval remains necessary even when the map validates.
- Session maps have no runtime dependency on Jev or another classifier service.

## Evidence

- `core/memory/session_map/models.py`
- `core/memory/session_map/authoring.py`
- `core/memory/session_map/evidence.py`
- `core/memory/session_map/retained_evidence.py`
- `core/memory/session_map/service.py`
- `core/memory/session_map/checkpoints.py`
- `core/constants.py`
- `api/services/chat_sessions.py`
- `static/js/session-map.js`
- `validation/scenarios/integration/core/session_map_schema.py`
- `validation/scenarios/integration/core/session_map_checkpoint.py`
- `validation/scenarios/integration/core/session_map_retained_evidence.py`
- `validation/scenarios/integration/core/session_map_evidence_admission.py`
- `validation/scenarios/integration/core/session_map_authoring_task.py`

## Related Decisions

- ADR 0003, Chat Sessions Use Canonical SQLite Storage
- ADR 0012, Chat History Broker
- ADR 0019, Runtime Execution Task Runner
- ADR 0048, Fork Canonical Chat Prefixes With Checkpoint Lineage
- ADR 0049, Effective History From Strategy-Pinned Checkpoints
