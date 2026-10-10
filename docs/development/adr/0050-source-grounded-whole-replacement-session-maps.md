# 0050 - Use Source-Grounded Whole-Replacement Session Maps

## Status

Accepted.

## Context

Repeated narrative compaction can preserve central facts while gradually changing emphasis, interpretation, and the causal story that connects decisions. A more structured representation can make current state, uncertainty, and supersession clearer, but a rigid ledger can also crystallize exploration into commitments or imply that its selected references exhaust the conversation.

Compaction V2 needs a representation that remains small enough to replace an old transcript prefix, carries enough narrative orientation for continued conversation, distinguishes user commitments from assistant proposals and tool observations, and preserves a trustworthy path back to canonical evidence. It must remain honest about being a lossy authored projection rather than a second source of truth.

## Decision

Represent Compaction V2 continuity as a bounded, sparse session map containing an optional set of typed current-state entries and, when entries exist, one concise session-level narrative trajectory. Preserve the durable throughline and major causal pivots needed to understand current work across whole-map replacements; update that longer arc rather than resetting it around each new evidence interval. Describe pivots, displaced alternatives, and changed interpretations only when canonical evidence establishes them rather than forcing every revision into a dramatic causal shape. Entries may describe orientation, goals, options, next actions, decisions, constraints, findings, open questions, and artifacts. Each entry records its lifecycle state and evidentiary basis so active, superseded, and closed state remains distinct and assistant proposals do not silently become user commitments.

Treat each authored revision as a complete replacement map rather than a patch log. Author from the previous map plus newly evicted canonical evidence, retained recent canonical evidence, and any bounded transcript evidence explicitly retrieved and mechanically verified for the active session. Stable semantic entry identifiers support reconciliation across revisions, but the author may revise, supersede, close, or omit entries as the conversation changes.

Require every trajectory and entry claim to cite one or more canonical message ranges available through the prior map or admitted authoring evidence. Validate the bounded schema, conservative entry admission, source availability, and provenance before persistence. A source range proves that evidence was available; it does not by itself prove semantic entailment, make the references exhaustive, or elevate the map above the canonical transcript.

Validate stored map payloads when reading checkpoints as well as when authoring them. Invalid metadata fails explicitly without changing canonical history or the pinned strategy. Corruption responses and operational diagnostics identify the session and checkpoint using controlled error text; do not serialize private map input through validation messages or chained exceptions.

Keep each evidence envelope's contiguous consumed interval distinct from its citable ranges. Remove `session_ops` retrieval returns from author-visible message projections while preserving eligible parts of mixed messages. Admit bounded original transcript fragments only after verifying their canonical identity and exact content. Record the verified evidence-admission version in checkpoint metadata so sanitized mixed-message citations remain durable. A prior-map citation from a checkpoint without verified admission requires fresh eligible evidence when its message contains a retrieval return; retrieval-only messages are never citable. Persistence independently enforces the same provenance boundary.

Keep the canonical eviction boundary separate from the author observation boundary. Retained messages may inform current-state correction and may be cited when they support a salient map claim, but they remain verbatim in effective history until a later reduction consumes them. Persist a validated map only through the atomic, history-revision-fenced checkpoint contract in ADR 0049.

Expose append-only map revisions, their boundaries, and their source references in a map-focused inspector. Keep complete canonical messages available through bounded reverse paging in the ordinary chat timeline, with the active map boundary shown at its canonical position. Keep canonical transcript search and bounded window retrieval available for exact wording, disputed provenance, omitted detail, and information outside the sparse map.

Do not use a classifier as a map-authoring gate. Author a new whole map whenever the configured reduction policy selects a new eviction boundary. Generic decision-model capability remains independent of session-map operation.

## Rationale

A small typed map makes current state and supersession easier to inspect than an unconstrained narrative summary, while the trajectory preserves the causal thread that a field-only schema tends to lose. Evidentiary basis and conservative admission directly address the observed risk that brainstorming, assistant suggestions, or tentative options become false decisions or goals.

Whole-map replacement avoids an ever-growing chain of active, superseded, and closed patch operations in model context. Canonical source ranges and transcript retrieval make the compact representation auditable without requiring it to carry every historical fact or quotation. Governed authoring and fail-closed validation keep malformed or stale maps from becoming effective context.

## Consequences

- Compaction V2 supplies compact, structured orientation with explicit options, open questions, supersession, causal trajectory, and inspectable provenance.
- The session map is derived and lossy. It is not canonical truth, a complete transcript summary, or an event ledger. Its text can supply candidate evidence for cross-session lexical discovery under [ADR 0051](0051-lexical-session-discovery-over-canonical-evidence.md).
- Map references are evidence routes rather than an exhaustive declaration of relevant history.
- Canonical messages remain available for retrieval, reauthoring, upgrades, inspection, and safe forks.
- The ordinary chat timeline remains the single visual home for canonical messages; the map modal does not duplicate transcript paging.
- Complete replacement bounds model-facing map growth, while append-only checkpoints preserve revision history outside the active context.
- Schema validation, provenance validation, author retries, checkpoint metadata, transcript inspection, and model inference add complexity and cost at reduction boundaries.
- A map may omit useful narrative texture or emerging ideas; the retained verbatim tail and canonical transcript retrieval remain part of the architecture rather than temporary compatibility mechanisms.
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
