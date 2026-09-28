# Session Transcript Retrieval and Compaction v2 Plan

## Status

Planning complete. Slice 1 and all five of its checkpoints are implemented and validated. Slice 2 established live-path feasibility and is closed without a larger trigger benchmark. Slice 3 makes recovery cards the conditional routing surface for transcript search and is complete without generated source references. Slice 4 implements opt-in Compaction v2: provenance-aware structured compaction with stepped eviction behind the existing context-reduction boundary. Jev has been evaluated and abandoned for session memory on this branch. The completed foundation indexes canonical transcripts, exposes bounded source retrieval, reuses the same backend for broad deep-session discovery, and keeps generated retrieval envelopes from becoming their own evidence while permitting canonical messages explicitly recovered through transcript windows to support later map revisions.

## Decision Summary

AssistantMD's immediate long-session problem is not attention while relevant messages remain inside the model's effective context. It is the loss of detail, salience, and provenance after compaction replaces an older prefix with generated prose. Recovery cards remain useful for continuity, but repeated cards can reinterpret prior cards and cannot let the agent inspect the original evidence.

The first product slice therefore added sparse, bounded access to the canonical raw transcript through the existing `session_ops` tool. Lexical FTS is the initial ranking baseline, not a permanent restriction: the retrieval boundary allows a later hybrid lexical/semantic strategy to reuse AssistantMD's existing session-search vector machinery without changing the tool contract. Slice 1 did not add embeddings, an autonomous memory author, or a second context representation. Recovery-card compaction remained unchanged while retrieval reliability was measured.

The later experiment should be understood as Compaction v2 rather than as a distinct live-memory system. Like recovery-card compaction, it periodically replaces an older effective-context prefix with generated continuation state and retains a recent verbatim tail. Its improvements are a structured and source-linked compact artifact, explicit high and low watermarks, whole-group eviction, immutable checkpoints, canonical transcript preservation, and bounded retrieval. The internal artifact remains a `session_map`, but that name does not imply continuous updating or a separate authority layer.

Recovery-card compaction is Compaction v1 and remains the default and fallback while Compaction v2 is evaluated. Compaction v2 does not claim to eliminate summarization loss; it gives summarization a tighter schema and a route back to canonical evidence. Cross-session consolidation, vault memory, user-profile memory, and retrieval-cache admission remain separate memory-system concerns rather than extensions hidden inside the session map.

### Terminology and system boundary

| Term | Meaning |
| --- | --- |
| Compaction v1 | The existing recovery-card strategy: generated continuity prose plus a retained recent tail. |
| Compaction v2 | The opt-in structured-compaction strategy: a source-linked session map plus a retained recent tail, produced at configurable high/low-watermark boundaries. |
| Session map | The internal structured checkpoint artifact used by Compaction v2; not a continuously updated or independent memory service. |
| Canonical transcript | The authoritative raw session history retained outside effective model context and available through bounded search/window retrieval. |
| Broader memory architecture | Future cross-session, user, vault, and cached-retrieval systems that may consume session artifacts but remain separate from session compaction. |

Compaction v2 should earn promotion by outperforming Compaction v1 on continuity, current-state accuracy, resistance to salience drift over repeated reductions, provenance and recoverability, effective-context size, and total model work including retrieval. A smaller map alone is not success, and resemblance to a recovery card is not failure: the intended improvement is a cleaner and more inspectable compaction contract.

The earlier continuously maintained live-map implementation was removed rather than left dormant. Compaction v2 rebuilt a smaller provenance-aware map only at the context-reduction boundary. The generic TypeSafe/Jev provider, `decision` model capability, secret integration, and provider-neutral decision runtime remain reusable platform capabilities rather than Compaction v2 dependencies.

Jev is abandoned for session memory on this branch. The map-author gate could defer a rewrite but could not eliminate the eventual need to incorporate cumulative evidence, and the retrieval-reranking evaluation did not improve complete-answer coverage over a deterministic baseline. Stepped eviction authors unconditionally whenever its configurable high-watermark and low-watermark policy selects an outgoing prefix. Generic decision-model infrastructure remains for separately justified future features, but Compaction v2, transcript retrieval, and their validation have no Jev dependency. The completed reranking experiment is retained as a closed design record in [Retrieval Context Admission: A Role for Cheap Decision Models](RETRIEVAL_CONTEXT_ADMISSION_DESIGN.md).

## User Outcomes

- An agent can recover exact facts, decisions, wording, drafts, and surrounding context from messages that compaction has removed from effective history.
- Retrieval returns small, explicit, source-linked excerpts rather than injecting an entire transcript.
- Current-session lookup is direct; older-session lookup composes existing session discovery with the same transcript operations.
- Results never cross the active vault or existing session-ownership boundary.
- Canonical messages remain authoritative. Summaries, Compaction v1 recovery cards, search indexes, and Compaction v2 session maps are derived aids whose claims can be checked against source messages.
- Compaction v2 can reduce effective history to a configurable target while preserving whole provider-history groups, a concise source-linked continuation state, and bounded access to evicted evidence.
- Normal chat, compaction, and server startup do not depend on Jev or another optional service.

## Scope

### In scope now

- Maintain `search_transcript` and `get_transcript_window` as bounded access to canonical raw session evidence.
- Maintain the persistent lexical transcript index and the shared canonical backend for `search_sessions(mode="deep")`.
- Keep Compaction v1 recovery cards as the default and fallback strategy, including their transcript-search guidance.
- Evaluate opt-in Compaction v2 through the same post-turn compaction boundary using a structured session-map artifact, explicit high/low watermarks, whole-group eviction, provenance validation, and immutable checkpoint inspection.
- Compare Compaction v2 with Compaction v1 on continuity, current-state accuracy, repeated-reduction drift, provenance recovery, context size, latency, and total model work.
- Retain generic decision-model infrastructure without making Compaction v2 depend on Jev.

### Deferred

- Hybrid lexical/semantic transcript ranking, evaluated behind the same bounded retrieval interface using the existing session-search vector infrastructure where practical.
- Automatic retrieval on every turn.
- Generated source references inside recovery cards; transcript search is the recovery-card routing mechanism while source ranges are tested as mechanical map metadata.
- Promotion of Compaction v2 to the default strategy or removal of Compaction v1.
- Cross-session or vault memory consolidation.
- Replacing the nightly session-summary workflow or its UI.

### Non-goals

- Do not expose an operation that returns an entire transcript.
- Do not copy canonical chat into a second truth store.
- Do not treat retrieved text as trusted instructions.
- Do not allow cross-vault access, even when a caller knows a session ID.
- Do not treat the session-summary vector index or a future transcript vector index as canonical authority; semantic indexes remain rebuildable candidate generators whose hits resolve back to raw messages.
- Do not make retrieval depend on Jev, an embedding model, or any remote service.
- Do not change recovery-card compaction in Slice 1.

## Architectural Fit

The design follows the existing ownership boundaries recorded in ADRs 0003, 0006, and 0012.

| Concern | Owner | Decision |
| --- | --- | --- |
| Canonical transcript and lexical index | `core/chat` | Store the FTS index beside `chat_messages` and update it transactionally with canonical message writes and deletes. |
| Raw retrieval service | `core/chat` | Add a narrow repository/service surface for vault-scoped search and bounded sequence windows. Do not place SQL or authorization rules in the tool. |
| Agent-facing operations | `core/tools/session_ops.py` | Extend the existing session discovery and summary tool rather than create another chat-history tool. |
| Effective provider history | `ChatHistoryService` | Leave ordinary effective-history construction unchanged. Transcript retrieval deliberately reads canonical raw records through the lower-level chat boundary. |
| Broad historical discovery | Session summaries plus `search_sessions` | Keep summary search as the cheap first pass; make `deep` mode a bounded raw-message fallback returning anchors rather than transcript blocks. |
| Context reduction | `core/chat/compaction.py` with the bounded artifact under `core/memory/session_map` | Keep Compaction v1 and Compaction v2 behind the same post-turn compaction boundary; the map package owns only the structured artifact and authoring contract, not a parallel memory runtime. |

`ChatStore.get_stored_messages_range(...)`, introduced during the earlier map work, is a generally useful canonical range primitive shared by retrieval and Compaction v2 provenance resolution. The retired continuously maintained map policy must not return; Compaction v2 orchestration stays inside the existing compaction path.

The transcript-search service should expose a strategy-neutral result shape keyed by canonical session and sequence anchors. The lexical implementation is first, but ranking policy must remain behind the service boundary so a hybrid implementation can combine FTS candidates with the existing `VectorService` and session-search scoring patterns. Neither the tool nor transcript-window retrieval should know which candidate generator produced an anchor.

## Slice 1: Retrieval Foundation

| Checkpoint | Deliverable | Primary validation boundary |
| --- | --- | --- |
| Slice 1A | Retire the live session map while retaining generic decision-model infrastructure | Startup/settings migrations, existing chat execution, configuration, API, and UI smoke coverage |
| Slice 1B | Add the canonical transcript index and strategy-neutral retrieval service | Storage lifecycle, backfill, rebuild, authorization, purge, and bounded range tests |
| Slice 1C | Expose focused transcript search and window retrieval through `session_ops` | Real tool invocation, bounds, continuation, provenance, and hostile-content tests |
| Slice 1D | Move deep session search onto the shared canonical backend | Existing search compatibility, pre-compaction recall, ranking, and full core integration profile |
| Slice 1E | Harden source projection and provenance after live-path inspection | Structured tool evidence, retrieval-envelope exclusion, source metadata, migration backfill, and focused real-tool coverage |

Each checkpoint should be reviewable and testable on its own. Do not begin the next checkpoint while the current checkpoint has known failures in its declared validation boundary.

## Slice 1A: Retire the Live Session Map

This is required branch cleanup, not a soft deprecation. It lands first so new retrieval work is not built around abandoned runtime or persistence assumptions.

**Status:** Complete. The live-map runtime, task kind, settings, inspection API/UI, package, scenarios, fixtures, probes, and obsolete implementation plan are removed. Chat database migration 5 removes the three derived map tables from existing installations; settings repair removes retired keys, and the general-settings API no longer exposes those retired settings. Generic TypeSafe/Jev provider, secret, alias, decision capability, and decision-model validation remain. The canonical raw range-read primitive remains for Slice 1B. The full deterministic core profile passed all 115 scenarios after the removal.

### Remove product runtime

- Delete `core/memory/session_map/` and all map models, patch operations, authoring prompts, classifiers, stores, and orchestration services.
- Remove `SessionMapService` composition from runtime context and bootstrap, including interrupted-attempt reconciliation.
- Remove successful-turn accounting and background dispatch from `core/chat/task_execution.py`.
- Remove `ExecutionTaskKind.SESSION_MEMORY` and any session-map-specific execution policy or logging branch.
- Remove map API models, endpoints, service methods, session-list `has_session_map` fields, and serialization paths.
- Delete `static/js/session-map.js`; remove its imports, map icon/action, revision modal, map-specific CSS, and HTML hooks. Retain only genuinely generic modal primitives already used elsewhere.

### Remove configuration

- Remove every `live_session_memory_*` setting from `core/settings/settings.template.yaml`, accessors, validation, configuration editor metadata, and readiness payloads.
- Add a deterministic settings upgrade that removes retired keys from persisted settings while preserving unrelated custom configuration.
- Do not remove `jev`, the `decision` capability, the TypeSafe provider, `TYPESAFE_API_KEY`, generic decision adapters, or their provider/model readiness validation.

### Remove persistent derived state

- Add a forward chat-database migration that drops `chat_session_map_revisions`, `chat_session_map_maintenance`, `chat_session_map_attempts`, and their indexes.
- Remove unconditional map-table creation from `ensure_chat_sessions_schema`.
- Keep only the minimum immutable migration history needed to upgrade databases that may already have versions 3 and 4 recorded. Those historical records are compatibility machinery, not callable runtime behavior; the forward teardown migration must leave both upgraded and fresh databases with no map tables.
- Verify startup migration from a database that already contains map revisions and from a clean database. Removing the tables deletes derived experimental state only; canonical messages and compaction checkpoints remain untouched.

### Remove obsolete validation and documentation

- Delete live-map unit tests, integration scenarios, experimental probes, fixture loaders, labelled map corpora, replay helpers, and map-specific validation artifacts tracked by the repository.
- Remove map assertions from shared configuration, system-migration, startup, and chat-execution scenarios while retaining generic Jev/decision-capability coverage.
- Preserve any ignored production-derived transcript database only as private local input for the new retrieval evaluation; do not commit, rename into a tracked fixture, or delete user-provided data as part of automated cleanup.
- Delete `LIVE_SESSION_MEMORY_IMPLEMENTATION_PLAN.md` after any still-relevant findings have been carried into this plan. Update current architecture/tool documentation so no supported contract refers to live-map modes or map inspection.
- Run `rg` over source, static assets, validation, and docs as a cleanup invariant. Permitted residual mentions are limited to the forward teardown migration, immutable historical migration identifiers, explicit settings cleanup, retirement assertions, and this plan's record of the retirement decision.

### Slice 1A validation and exit gate

Run focused settings-upgrade, system-database migration, startup-migration, chat-execution, API session-list, and browser/static-module checks affected by removal. Verify both a clean system root and a copied database containing map tables. Slice 1A exits only when the application starts normally, canonical messages and checkpoints remain intact, retired settings and tables are removed, existing recovery-card behavior still passes, generic Jev configuration remains available, and the cleanup `rg` finds no unexplained live-map code paths.

## Slice 1B: Canonical Transcript Index and Retrieval Service

**Status:** Complete. Chat database migration 6 creates and backfills an external-content FTS5 index over canonical message text. Transactional insert, relevant-update, and delete triggers keep it synchronized, including foreign-key cascade deletion, and an explicit rebuild operation reconstructs it from `chat_messages`. `TranscriptRetrievalService` authorizes through the existing session-access boundary, returns bounded excerpts and strategy-neutral canonical anchors, and exposes exact raw-message interval reads. Shared FTS query normalization was factored out of session-summary code for reuse. The dedicated storage scenario and all 116 deterministic core scenarios pass.

### Storage and indexing

Add an FTS5 index over `chat_messages.content_text` in `chat_sessions.db`. The index is derived state; returned identity and provenance fields must come from a join back to canonical `chat_messages`, not from an independently trusted copy.

The schema change must provide all of the following:

- Backfill existing canonical messages when the migration runs.
- Keep the index current for inserts, relevant updates, and deletes through database triggers or an equivalently transactional mechanism.
- Remove index rows when a session is purged, including deletion caused by foreign-key cascade.
- Support an explicit rebuild path so index corruption or version changes do not require transcript loss.
- Apply `vault_name` and `session_id` scope inside SQL before limiting or returning hits.
- Preserve stable anchors using canonical `sequence_index`; internal row IDs are implementation details and must not become the public retrieval contract.

Prefer an external-content FTS table linked to `chat_messages` if focused migration tests confirm that trigger and cascade behavior are reliable. If SQLite limitations make that contract fragile, use a normal FTS table with explicit transactional triggers and a deterministic rebuild. Do not add another database.

### Slice 1B validation and exit gate

Add focused storage and service tests proving migration backfill, immediate indexing of new messages, relevant update behavior, cascade purge, deterministic rebuild, vault/session authorization, canonical ordering, exact range reads, bounded lexical excerpts, and safe handling of malformed FTS queries. Slice 1B exits only when these behaviors work without invoking a model or tool and the service returns strategy-neutral canonical anchors suitable for both lexical and later hybrid ranking.

## Slice 1C: Focused `session_ops` Retrieval

**Status:** Complete. The existing `session_ops` tool now exposes authorized `search_transcript` and `get_transcript_window` operations with current-session defaults, explicit same-vault lookup, stable canonical anchors, compaction-boundary metadata, chronological exact-source windows, conservative token budgets, and integrity-protected continuation cursors for oversized messages. Tool output and documentation mark recovered messages as untrusted historical evidence. Shared token estimation moved to `core/utils` so retrieval does not depend on the tool layer. The real-tool scenario covers pre-compaction recall, same-vault lookup, hostile historical content, lossless oversized-message continuation, and forged, mismatched, and stale cursor rejection. Ruff, Black, MyPy, diff checks, focused scenarios, and all 117 deterministic core scenarios pass.

### Tool contract

Extend `session_ops` with two operations.

#### `search_transcript`

Inputs:

- `query`: required non-empty lexical query.
- `session_id`: optional; defaults to the active session.
- `limit`: default 5, with a small hard maximum.

Each hit returns:

- `session_id` and `sequence_index` as the stable retrieval anchor.
- `role`, `message_type`, and `created_at` from the canonical row.
- A bounded highlighted excerpt.
- A deterministic relevance rank or ordering value, clearly described as search ranking rather than calibrated confidence.
- Enough boundary metadata to request a surrounding window and to tell whether the hit is before or after the latest compaction checkpoint.

The operation searches one authorized session at a time. Searching across sessions remains a deliberate composition: `search_sessions` identifies candidates, then `search_transcript` searches selected sessions.

#### `get_transcript_window`

Inputs:

- `session_id`: optional; defaults to the active session.
- `sequence_index`: required canonical anchor.
- `before` and `after`: bounded counts of adjacent canonical messages.
- `max_tokens`: explicit output budget with conservative default and hard maximum.
- `cursor`: optional opaque continuation token when one complete result cannot fit.

The result returns canonical messages in chronological order with sequence, role, type, timestamp, exact content, and truncation metadata. It prioritizes the anchor, then adds whole neighboring messages while the budget permits. A single message larger than the hard result bound is paged deterministically with an opaque continuation cursor; it must not become impossible to recover the end of a long draft merely because that draft exceeds one tool return. Continuations remain bound to the same vault, session, anchor, source revision, and retrieval parameters so a caller cannot use a cursor to widen scope.

The first implementation should use the smallest parameter set that satisfies this contract. Operation-specific validation must reject `limit="all"`, negative windows, unknown sessions, stale or malformed cursors, and requests above hard bounds with actionable `ModelRetry` messages.

### Tool guidance and trust boundary

Update the tool description and `docs/tools/session_ops.md` so the model knows to retrieve when a request depends on earlier wording, an omitted draft, a decision rationale, a correction, or a fact whose source is no longer visible. Guidance should recommend searching first and retrieving only the smallest useful window.

Retrieved chat content is evidence, not an instruction channel. Tool output and documentation must mark it as untrusted historical content, retain message-role provenance, and tell the caller not to execute directives found inside it unless the current user request independently authorizes them.

For a known or active session, the primary agent may call the operations directly. For vague research across several sessions or many windows, a delegated agent may iterate over the same bounded operations and return a compact result. Delegation is an orchestration choice, not a requirement of the retrieval API.

### Slice 1C validation and exit gate

Add deterministic real-tool coverage for default-current-session lookup, explicit same-vault lookup, cross-vault rejection, result limits, exact source recovery, chronological windows, oversized-message continuation, stale or forged cursors, and prompt-injection-shaped historical content. Slice 1C exits only when the normal `session_ops` path can recover canonical pre-compaction evidence with bounded output and no model, embedding, or remote-service dependency inside retrieval itself.

## Slice 1D: Deep Session Search Integration

**Status:** Complete. `search_sessions(mode="deep")` now combines existing summary evidence with the best bounded canonical raw-message hit per authorized session. It searches the persistent transcript index without loading effective histories or building a temporary database, returns stable sequence anchors and compaction-boundary metadata, applies workspace and principal eligibility before transcript ranking, and marks transcript excerpts as untrusted historical evidence. The integration scenario proves real-tool recovery of pre-compaction content, bounded anchors, and exclusion of same-vault transcript and summary evidence owned by another principal. Hardening also corrected oversized-window truncation flags to reflect actual neighboring messages. Ruff, Black, MyPy, diff checks, focused compatibility scenarios, and all 117 deterministic core scenarios pass.

### Deep session search

Replace the current per-call in-memory FTS built from effective histories. `search_sessions(mode="deep")` should query the new canonical index, group bounded hits by authorized session, combine them with existing session metadata/ranking, and return only compact excerpts plus sequence anchors. It must surface content hidden behind compaction without loading complete histories.

Ordinary summary-backed session search remains preferred because it is cheaper and semantically richer. Deep mode is the lexical fallback for details absent from session summaries.

### Slice 1D validation

Add a deterministic integration scenario, preferably `validation/scenarios/integration/core/session_ops_transcript_retrieval.py`, that exercises the real tool contract and database lifecycle. It must prove:

- A message before the latest compaction checkpoint is absent from effective history but discoverable through `search_transcript` and exactly recoverable through `get_transcript_window`.
- Default-current-session lookup and explicit same-vault lookup work.
- A session from another vault, an unknown session, and a forged continuation cursor reveal no content.
- New messages become searchable immediately and session purge removes every corresponding hit.
- Migration backfill makes pre-existing messages searchable, and index rebuild is deterministic.
- Excerpts, result counts, neighboring windows, total output, oversized single messages, truncation, and continuation stay within bounds.
- Window output preserves canonical ordering and exact source text, including user, assistant, tool-call, and tool-result records represented by the store.
- Search syntax errors and empty or pathological queries fail safely rather than exposing raw SQLite errors.
- `search_sessions(mode="deep")` finds pre-compaction raw content and returns useful retrieval anchors without returning a transcript.
- Existing `session_ops` summary/list/search behavior remains compatible.

The focused unit coverage for query normalization, cursor signing or integrity, token-budget packing, excerpt shaping, and authorization belongs in Slices 1B and 1C rather than being deferred until integration. During each checkpoint, run its tests and affected scenarios directly. Once Slice 1D is stable, run `python validation/run_validation.py run integration/core` as required by the repository validation cadence.

### Slice 1D and overall Slice 1 exit gate

Slice 1D exits when deep search uses the shared canonical backend, finds content hidden by compaction, returns only bounded anchors and excerpts, and preserves ordinary summary-backed search behavior. Overall Slice 1 is complete only when canonical pre-compaction evidence can be found and retrieved through the normal tool path with bounded output, vault isolation, purge correctness, all five checkpoint gates passing, the full deterministic core profile green, and no live session-map runtime remaining. The retrieval implementation must be entirely local and deterministic aside from the chat model choosing whether and how to call it.

## Slice 1E: Provenance Hardening

**Status:** Complete. Live-path inspection showed that the retrieval flow found the intended pre-compaction anchors, but also exposed two derived-index defects that would distort Slice 2 measurements: structured tool returns had empty searchable text, while prior `session_ops` retrieval results were indexed as ordinary user messages and could be returned as newer echoes of the evidence they quoted. The shared projection, migration backfill, provenance fields, and candidate exclusion are now implemented through the normal storage and `session_ops` paths.

Define one shared, deterministic message projection used by canonical writes, effective-history rendering, migration backfill, and retrieval metadata. Preserve provider-native `message_json` as canonical truth. Render mapping and sequence tool returns as readable JSON while omitting binary payload bytes from the derived text. Classify the projected source as user, assistant, system, tool result, retrieval result, tool call, or mixed, and include that provenance plus tool names in search hits and transcript windows.

Exclude `session_ops` tool-return envelopes from transcript candidate generation inside the authorized SQL query before ranking and limiting. This exclusion applies only to derived retrieval/search output, not to direct evidence returned by other tools. Direct structured tool output must remain searchable and recoverable at its stable canonical sequence anchor. A later hybrid candidate generator must preserve the same exclusion and result metadata.

Add chat database migration 7 to recompute derived `content_text` from every valid canonical `message_json` row and rebuild the FTS index. Malformed legacy rows retain their prior projection rather than blocking startup; normal deserialization diagnostics remain responsible for surfacing them. The migration does not rewrite canonical message JSON.

Extend the storage and real-tool scenarios before implementation. Prove that a structured tool error is searchable, a later `session_ops` payload quoting the same terms is not a candidate, results identify tool provenance, windows return readable structured content and provenance, migration backfills existing structured rows, and ordinary user/assistant retrieval remains compatible. Slice 1E exits when those focused scenarios, production quality gates, and the full deterministic core profile pass.

## Slice 2: Live-Path Feasibility

**Status:** Complete and intentionally closed. A small Terra probe established that canonical pre-compaction evidence can be recovered through the normal chat-task API, task executor, context manager, and `session_ops` path. With no dedicated retrieval-routing prompt, the model sometimes inferred the need to search and successfully reached the correct canonical evidence. The probe also confirmed that tool selection is the dominant tunable boundary, not a defect in transcript indexing or window retrieval.

Do not continue with a large trigger benchmark, promotion thresholds, prompt tournament, or repeated production replay. Tool selection is ordinary prompt-policy work, and the recovery card provides a narrower place for that policy than the global flight card. The disposable live probe is removed. Hybrid lexical/semantic transcript ranking remains deferred until a real lexical candidate-recall failure justifies testing it.

## Slice 3: Source-Linked Recovery Cards

Use the recovery card as a compact routing aid rather than a complete memory representation. Retrieval guidance belongs here because the card exists only after compaction; the global flight card should not carry transcript-recovery policy before any history has left active context.

### Slice 3A: Fixed recovery-card preamble

**Status:** Complete. Every recovery card now begins with a concise system-owned preamble that explains why the card exists, identifies the canonical transcript as the source of truth, states when the summary is sufficient, and queues `search_transcript` when canonical evidence is needed. The text lives in `core/constants.py` so prompt tuning has one obvious owner. It is composed outside generated summary prose so repeated compaction cannot omit, paraphrase, or amplify it, and the compaction author treats a prior preamble as operational guidance rather than summary material.

Validate deterministically that the preamble appears only in compacted effective history, precedes generated summary content, survives repeated compaction exactly once, and remains visible through session detail and forks. No live trigger benchmark is required; one ordinary chat smoke is sufficient after deterministic coverage passes.

### Slice 3B: Generated source references deferred

Do not add generated sequence references to recovery-card claims before the stepped-map experiment. Search already supplies a bounded path back to canonical evidence, while reference generation would add another probabilistic behavior to tune on a continuity representation that the experiment may replace.

Recovery cards remain the durable fallback when the map author is unavailable. Their fixed preamble queues transcript search without exposing lower-level window mechanics. If recovery cards remain the primary continuity representation after Slice 4, direct references can be reconsidered with an explicit anchor-accuracy evaluation.

**Decision:** Deferred. Source ranges belong first on deterministic eviction envelopes and derived map entries, where the ranges are mechanically known rather than generated by the recovery-card author.

## Slice 4: Compaction v2 — Stepped Structured Compaction

Compare Compaction v1 recovery cards with a structured high-watermark/low-watermark strategy through independently testable checkpoints. Compaction v2 remains opt-in until repeated live use demonstrates that it is at least as reliable and materially improves provenance, inspectability, or context efficiency; ordinary sessions continue to use Compaction v1.

Compaction v2 is stepped structured compaction, not naive FIFO truncation or a continuously maintained memory service:

1. Keep complete recent messages until a high token watermark is crossed.
2. Select a coherent oldest prefix of whole provider-history groups to remove until the low watermark is restored; an abandoned historical user group may leave context after a later group begins, while the newest group and malformed tool exchanges remain protected.
3. Before removal, project the outgoing groups into authoring evidence with mechanically derived source ranges.
4. Invoke a generative whole-map update for every eviction batch selected by the configurable watermarks.
5. Commit the derived update and eviction checkpoint atomically enough that no canonical message leaves effective context without either retained recent context or a durable pointer/state representation.
6. Keep every raw message in canonical SQLite and make it retrievable through Slice 1.

The state representation for this experiment must be designed from eviction evidence rather than copied from the retired live map. It should remain bounded, favor active state and provenance over narrative history, and compact or archive superseded entries deterministically. If authoring is unavailable, the system must fall back to existing recovery-card compaction without losing the chat turn.

### Slice 4A: Deterministic eviction planner

**Status:** Complete. A pure planning boundary accepts provider-native effective history plus high and low token watermarks and returns an oldest-prefix boundary with token and grouping audit metadata without mutating storage. It plans only after the high watermark is crossed, preserves chronological order, retains the newest group, and never splits a provider-history group or a valid tool-call/result exchange. A later user or system input makes an earlier abandoned user group historical and therefore evictable without deleting it from canonical storage. The planner reports stable no-op reasons for history below the high watermark, invalid tool history, or a history with no older group to evict. When the newest indivisible group alone exceeds the low watermark, it safely evicts older groups and reports that the target remains exceeded.

The deterministic scenario covers ordinary turns, multiple tool calls and returns in one exchange, oversized indivisible groups, an existing recovery-card message, abandoned historical input, an incomplete newest turn, malformed tool history, watermark validation, and stable token accounting. Slice 4A does not invoke Jev, author a map, change effective history, add settings, or persist state.

### Slice 4B: Provenance-bearing eviction envelopes

**Status:** Complete for uncompacted canonical sessions. Every planned outgoing group can be resolved to an immutable experiment envelope containing its canonical raw sequence range, shared stored-text projection, token estimate, source digest, and captured history revision. Deterministic envelope identity depends on session, range, and source content rather than the mutable revision, so replanning after later appends yields the same identity for the same evidence. Resolution rejects a stale revision, changed message snapshot, non-contiguous canonical range, or changed eviction boundary.

Neither Jev nor a generative model invents source identity. Sessions with an existing recovery-card checkpoint are explicitly ineligible because checkpoint replacement history contains a synthetic summary and currently exposes effective indexes that are not canonical raw identities. Those sessions remain on recovery-card compaction during the experiment; stepped eviction begins only while effective history is still canonical. Supporting an intentional recovery-card-to-map transition is deferred until evidence justifies designing a provenance-preserving migration.

### Slice 4C: Bounded eviction-derived map with unconditional authoring

**Status:** Complete. The bounded replacement-map schema covers orientation, goals, next actions, decisions, constraints, open questions, and artifacts. Every entry has a stable semantic ID, explicit evidence basis, lifecycle state, bounded text, and one or more canonical source ranges. Deterministic validation rejects unsupported provenance, duplicate or generic IDs, multiple orientations, and maps outside fixed entry and text budgets.

A six-batch Terra probe over the private 1065 redevelopment transcript kept every authored revision within the provenance contract, used stable subject-specific IDs, distinguished user-established, assistant-proposed, and tool-observed state, removed or revised stale entries, and pruned the map from eleven entries to ten when earlier communications ceased to matter. The probe exposed one remaining prompt ambiguity: records of sent communications could remain active. The contract now directs the author to close a retained completed communication and omit it once it no longer supports active work. This is evidence that the representation is viable, not a quality benchmark; the sample remains too small and domain-specific to establish general map fidelity.

A governed authoring service now invokes the structured generative model through a dedicated `session_map_authoring` execution task scoped to the chat session. It accepts the prior map plus ordered canonical evidence envelopes, rejects mismatched sessions, vaults, revisions, or ranges before execution, and validates every returned citation before completing the task. Start, completion, and failure events carry the task identity, model alias, prompt version, evidence range, and bounded counts. Deterministic validation proves both successful task completion and provenance rejection as a failed owning task. The service uses the internally streamed model path required by OpenAI OAuth and remains independent of persistence and runtime history replacement.

The first authoring experiments receive the current map plus the complete outgoing canonical envelopes. They do not call Jev. Once integrated with application execution, every map-author run must flow through the normal task executor and remain visible and governed like other model-backed work. An authoring failure leaves effective history untouched and routes the session to existing recovery-card compaction.

**Exit gate:** Advance only when unconditional authoring produces a bounded, source-linked map that preserves current state across repeated eviction batches. This slice establishes map quality independently of classifier quality or thresholds.

### Slice 4D: Opt-in stepped effective-history strategy

**Status:** Complete for the unconditional-author baseline. The strategy remains disabled by default and has deterministic coverage through the real post-turn chat path.

Compose the bounded map with the recent retained suffix and activate stepped eviction only under an explicit experimental setting with a ready generative author model. Commit the map update and eviction checkpoint so a source group cannot disappear from effective context before its durable envelope and state update exist. Preserve raw canonical SQLite messages and all Slice 1 retrieval operations. On any pre-commit failure, keep the old effective history and use recovery-card compaction.

The initial runtime strategy authors the map at every eviction boundary. This deliberately measures the value and cost of the map representation without Jev. Sessions already carrying a recovery-card checkpoint remain on recovery-card compaction during the experiment.

Implement this slice through three separately validated boundaries:

1. **4D1 — Durable map checkpoint and composition — Complete:** The existing append-only context-checkpoint ledger now has an explicit checkpoint kind rather than a competing effective-history store. A session-map checkpoint stores the typed map payload and a single rendered map context message, sets its consumed-through boundary to the last evicted canonical message, and lets effective-history assembly append canonical raw messages after that boundary. The write compares the expected history revision and atomically records the map payload, evidence metadata, replacement context, consumed boundary, and revision advance. Older checkpoints remain queryable for audit. Deterministic validation proves repeated composition, typed historical reload, complete raw-transcript preservation, and rejection of a stale revision without a partial checkpoint or effective-history change. No setting or post-turn hook exists in this sub-slice.
2. **4D2 — Explicit opt-in and readiness — Complete:** The experimental strategy defaults to `recovery_card`; stepped maps require `stepped_session_map`, automatic context reduction, an explicitly selected available text-capable author model, a valid configurable thinking policy, and a low watermark below the shared compaction threshold. Canonical sessions and sessions already carrying a valid map checkpoint are eligible. Missing or decision-only models, unavailable credentials, invalid settings, absent sessions, invalid map checkpoints, and any existing recovery-card checkpoint fail closed with stable reasons. The settings use the normal general-settings API and save without bespoke UI wiring.
3. **4D3 — Post-turn orchestration — Complete:** Under the existing per-session history lock, the post-turn hook plans eviction while preserving any pinned prior map, resolves only evicted raw messages to canonical envelopes, authors through the `session_map_authoring` task, rechecks the history revision, and atomically commits the checkpoint. Deterministic full-path validation proves successful map reduction and an injected author failure: the success case commits a source-linked map and reduces effective history, while the failure case records a failed author task and completes recovery-card compaction instead. Both cases preserve the full raw transcript and leave the completed chat turn successful.

The first live runtime baseline replayed the 38-message 1065 redevelopment transcript through two eviction boundaries with `gpt-mini` (`gpt-5.6-terra`) as the author, a 4,000-token high watermark, and a 1,500-token low watermark. The first checkpoint consumed canonical messages 0–17 into six entries. The second replaced that map after consuming through message 35, retained eight entries, preserved all 38 raw messages, and reduced 19,545 estimated effective-history tokens to 1,748 across the map plus the final raw user/assistant pair. Every citation in the latest map resolved to retained canonical evidence. The second map removed a completed initial-response artifact, added the pending legal-review action and courtyard-work dependency, preserved the unresolved land-authority question, and retained the tool-observed portfolio note. This is encouraging evidence for whole-map revision and lifecycle handling, but remains a single-domain, two-checkpoint sample rather than a comparative quality result.

The initial live attempt also exposed a transport bug before any checkpoint committed: the map author used `run_stream()`, which cannot exercise Pydantic AI's structured-output retry graph. The author now uses the existing OAuth-compatible `Agent.run(...)` path with an event-stream consumer, preserving streamed transport and validation retries. A focused deterministic regression forces one invalid structured result followed by a valid result and proves that the retry completes inside the map-author boundary. Recovery-card fallback also completed successfully during the failed live attempt.

A second live baseline used the materially different 54-message AI-consulting session, including collaborative positioning decisions, repeated durable file updates, and a large web-research tool exchange. The first checkpoint reduced 35,576 estimated tokens to 3,497 and the second reduced 60,714 to 5,488. Stable IDs and accepted decisions survived both replacements; the second map added the getting-started artifact and Vancouver market context while the competitive-research deliverable remained verbatim in the retained tail. A third natural boundary after a chat turn reduced 8,720 tokens to 4,403 and incorporated the competitive-research document. These observations remain qualitative, but the second domain did not expose a schema, provenance, or whole-map lifecycle defect.

The first exact-evidence chat attempt selected cross-session `search_sessions(mode="deep")` despite an explicit request for active-transcript search, then correctly refused to claim verification after the isolated fixture's embedding credential failed. The tool contract and map preamble now state the boundary directly: use `search_transcript` and `get_transcript_window` for evidence inside the active session, and use `search_sessions` only to find other sessions. On immediate live retry, Terra called both correct operations, retrieved compacted message 27, quoted the credibility sentence exactly, and completed the normal chat task. This confirms the end-to-end retrieval path with explicit prompting; it does not establish prompt-free retrieval recall.

### Slice 4E: Optional Jev movement gate

**Status:** Complete for the initial opt-in runtime and live Jev baseline. Threshold calibration remains part of Slice 4F rather than a blocker to the provider-neutral gate.

Only after unconditional stepped eviction works, route an optional movement decision through the normal task executor using the provider-neutral `decision` capability. Jev receives the bounded current map plus all cumulative unincorporated eviction envelopes and returns one `material_map_update_probability`. It does not classify dimensions or constrain the later author. A score below the threshold retains the envelopes in the pending buffer; a score at or above the threshold invokes the same whole-map author proven in Slice 4C. Provider failure, missing configuration, timeout, or an excessive pending-evidence buffer bypasses the optimization and forces authoring or recovery-card fallback rather than blocking context reduction.

Implement the gate through three separately validated boundaries:

1. **4E1 — Governed scalar decision contract — Complete:** One strict bounded output carries only `material_map_update_probability`. The prompt supplies the current map plus ordered cumulative evidence and distinguishes persistent state movement from repetition, transient analysis, routine tool chatter, and already-represented facts. The provider-neutral classifier runs only inside a dedicated `session_map_classification` task scoped to the chat session. Its result and telemetry record model/provider identity, prompt version, evidence range and count, pre-dispatch input estimate, latency, usage, scalar score, and task outcome without exposing Jev-specific response objects to the session-map module. Deterministic validation proves both a completed decision task and a provider failure retained as a failed owning task.
2. **4E2 — Durable cumulative pending evidence — Complete:** A deferred rewrite commits the unchanged map while advancing the effective-history boundary and stores only the pending canonical start/end range, SHA-256 source digest, and token estimate. A range resolver rehydrates the complete interval from immutable raw messages at the current history revision, rejects missing or non-contiguous messages and digest drift, and returns the same canonical envelope contract used by authoring and classification. The next authored checkpoint accepts the rehydrated pending interval plus contiguous new evidence and clears the pending marker. Checkpoint classification metadata records the scalar decision and action without transcript duplication. Deterministic validation proves the unchanged-map checkpoint, bounded metadata, later cumulative authoring, append-only audit history, full raw preservation, and fail-closed digest verification.
3. **4E3 — Optional post-turn orchestration — Complete:** Unconditional authoring remains the default when no gate model is selected, and the initial map is always authored. Later boundaries use the configured decision alias, scalar threshold, and maximum input-token budget. A low score commits the unchanged map with cumulative pending evidence; a high score authors from pending plus new evidence; an oversized request forces authoring without dispatching the classifier; and a missing or failed classifier records a failed/bypassed optimization before attempting normal authoring. Only authoring or commit failure reaches recovery-card fallback. Deterministic orchestration validation walks one session through initial authoring, deferred classification, cumulative threshold authoring, input-budget forcing, and classifier-failure bypass while proving task outcomes, append-only checkpoints, pending-state clearing, and full raw preservation.

An ephemeral Jev probe over six transitions from the private 1065 redevelopment transcript supports using the classifier as a conservative authoring gate, but not as an authority that discards evidence. Bundled six-field decisions were fast and repeatable: calls completed in roughly 0.25–0.43 seconds, and identical reruns generally moved scores by only a few hundredths. Clear artifact creation and major legal-state changes separated from an already-represented negative control, while subtler active-draft and narrative changes clustered in the ambiguous middle. Cumulative evidence correctly kept major courtyard and compensation changes elevated, but scores were not monotonic as more assistant analysis arrived. Independent field calls strengthened artifact detection but also produced a likely false decision update, showing that removing neighboring map context can trade one kind of interference for another. These results justify testing a movement gate; they do not establish a benefit from retaining dimensional classification.

When Slice 4E begins, use one scalar movement decision over the current map plus cumulative unincorporated evidence. Use one tunable threshold and keep pending evidence until a successful whole-map update incorporates its canonical source ranges. Give the classifier input an explicit hard budget derived from the selected decision model; if cumulative evidence reaches that budget, force authoring rather than exceeding it. Before runtime activation, build a larger labelled eviction corpus that distinguishes persistent state changes from transient completed actions, assistant proposals, and ordinary draft refinement. Reconsider dimensional classification only if the scalar gate exhibits a repeatable class-specific miss that decomposition could plausibly correct.

A follow-up scalar probe over the same 1065 transitions produced repeatable separation without dimensional outputs. Major branch changes scored approximately 0.67–0.80, already-represented evidence scored 0.38–0.39, and cumulative email refinement plus the later City-coordination branch scored 0.81–0.82. The initial scalar prompt underweighted a newly created project folder and memo at 0.32–0.37; explicitly defining creation, material revision, rename, or relocation of a durable artifact as a material map change raised that case to 0.79–0.80 while the negative control remained 0.41–0.43. An unaccepted lawyer-email revision remained low at 0.13–0.15, which is acceptable while it remains in the recent tail and has not become durable state. This supports the scalar contract and an initial threshold region near 0.5 for further evaluation, but the sample is too small to adopt a production default.

The first live integrated gate replay used the 1065 transcript with the threshold fixed at 0.5. After the initial Terra-authored map, Jev scored the next real legal-status evidence at 0.44 and deferred authoring while persisting canonical messages 20–21 as pending. A later explicit no-change pair expanded the cumulative range through message 23 and scored 0.18, again without a Terra call. Adding the real courtyard/compensation branch produced a cumulative score of 0.80, invoked one whole-map author over all three envelopes, added the courtyard entries, and cleared pending state. The three Jev calls completed in 0.30–0.38 seconds with 1,496–2,630 provider-reported input tokens and 23 output tokens each. Both Terra tasks and all classifier tasks completed, all 30 raw messages remained, and effective history ended as the map plus one raw pair. The 0.44 legal-status result is a useful near-threshold calibration case, not enough evidence by itself to lower the threshold; cumulative retention prevented information loss and allowed the later branch to trigger correctly.

### Slice 4F: Comparative live validation

Compare three conditions over long, multi-compaction conversations:

- Current recovery-card compaction plus bounded transcript retrieval.
- Stepped eviction with unconditional map authoring plus bounded transcript retrieval.
- Stepped eviction with the optional Jev movement gate plus bounded transcript retrieval.

Keep recovery-card reference generation outside the comparison unless the first two conditions expose a specific evidence-recovery failure that search cannot address. Score current-task continuity, exact-evidence recovery, semantic and salience drift, unsupported claims, effective-context size, model calls, latency, cost, and prompt-cache behavior. Include the retained recent tail as a separately measured contributor because earlier testing showed that it can mask weaknesses in the compact representation.

**Exit gate:** Stepped eviction advances only if it meets or exceeds recovery-card compaction on continuity and evidence fidelity, maintains strict context bounds, and has a simpler or clearly more valuable operating profile. Otherwise retain checkpoint compaction and retrieval and remove experimental runtime code.

#### Slice 4F controlled comparison protocol

Freeze the comparison protocol before generating outputs. Use the private `auth-proxy for Discourse` transcript as the primary held-out case because its 72 messages contain repeated hypothesis changes, durable configuration decisions, tool-derived evidence, and a late root-cause correction. Replay five cumulative batches ending at canonical sequence indexes 15, 31, 47, 63, and 71. Run the same batches through three fresh sessions: recovery cards, unconditional stepped maps, and stepped maps gated by Jev at the provisional 0.5 threshold. Use `gpt-mini` (`gpt-5.6-terra`) with low thinking for both generative conditions, preserve the same newest complete conversational group after every reduction, and run all author, compaction, and classification calls through their normal execution-task paths.

Record checkpoint-by-checkpoint effective message and token counts, compact-artifact size, consumed canonical boundary, generative call count, classifier call count and score, latency, raw-message preservation, and map-reference resolution. Treat the retained raw tail as its own measured input: grade each compact artifact alone and then grade the artifact plus retained tail so an apparently good result cannot hide dependence on recent verbatim messages.

Use this frozen semantic checklist for the held-out transcript: the app required server-side file access; obscurity alone was rejected because strangers could alter financial data; Discourse remained the identity provider; the selected chain was Caddy to `discourse-auth-proxy` to Marimo; one lightweight auth-proxy instance was used per protected service; Caddy and the auth proxy shared `caddy_default` while Marimo did not need that network; the dedicated public host was `finance.phhc.ca`; early Caddy, DNS, redirect, browser-state, and proxy theories were superseded as root causes; the actual failure was a Polars ARM incompatibility; changing the dependency to `polars[rtcompat]` fixed the app; and the auth proxy was reinstated successfully. For each checkpoint, score only checklist items established by that boundary and distinguish omission, accurate current state, stale-but-labelled history, stale state presented as current, and unsupported promotion of an assistant suggestion to a user decision.

Assess exact-evidence recovery separately from compact-artifact resemblance. After the final checkpoint, pose the same source-sensitive questions to each condition with only its effective context and the normal transcript tools available: what ultimately fixed the application, what final request path was in use, and which earlier diagnosis the later evidence displaced. Record whether the agent searches, whether the retrieved window contains the canonical evidence, and whether the answer remains grounded. This retrieval probe is diagnostic rather than a tool-selection benchmark; explicit recovery-card or map instructions may cue retrieval consistently across conditions.

Use the already-studied 1065 redevelopment transcript only as a calibration follow-up if the held-out run exposes an ambiguous scoring rule or a domain-specific failure. Do not tune prompts or the Jev threshold between conditions. Keep private transcript text and raw generated artifacts in ignored validation output or a temporary runtime; commit only aggregate measurements and conclusions.

#### Slice 4F first controlled result

The frozen held-out replay completed over all 72 messages and five reduction boundaries. Every condition preserved all canonical raw messages and the intended two-message recent tail. Recovery cards ran five governed compaction tasks. Unconditional maps ran five governed authoring tasks. Gated maps ran five governed authoring tasks plus four governed Jev classification tasks, so the gate saved no generative call on this deliberately change-dense transcript. The four post-initial scores were 0.90, 0.76, 0.75, and 0.89 at the fixed 0.5 threshold. Those decisions are defensible given that each 16-message batch contained a material architecture, deployment, diagnostic, or resolution change; this result does not measure steady-state small-slice deferral behavior.

| Condition | Mean compact artifact | Mean effective context after reduction | Governed model-task time | Generative calls | Classifier calls |
| --- | ---: | ---: | ---: | ---: | ---: |
| Recovery card | 1,600 estimated tokens | 3,265 estimated tokens | 128.0 seconds | 5 | 0 |
| Unconditional map | 834 estimated tokens | 2,499 estimated tokens | 74.8 seconds | 5 | 0 |
| Gated map | 798 estimated tokens | 2,464 estimated tokens | 69.8 seconds | 5 | 4 |

The map representation was approximately half the size of the recovery card and reduced total effective context by roughly one quarter on average. At the final boundary, the unconditional map artifact was 821 estimated tokens versus 1,258 for the recovery card, while effective context was 2,148 versus 2,585 tokens. All authored map source ranges resolved to canonical raw messages at every checkpoint. Recovery cards had no equivalent claim-level provenance. Structured map authoring was also faster in this run, but latency should not be treated as a stable model-cost estimate because output length and live-service variance differed; provider cost and prompt-cache measurements remain uncollected.

Through the first four boundaries, all three conditions preserved the main security constraint, selected Discourse identity architecture, per-service proxy choice, dedicated finance hostname, network change, and evolving diagnostic state without a material unsupported promotion. The recovery card was more verbose and repeatedly retained commands and configuration blocks. The maps were materially smaller, used conservative evidence-basis labels, revised open questions as the investigation moved, and removed obsolete proposals.

The final boundary exposed a real contract flaw rather than a subjective grading difference. Stepped authoring received only messages being evicted through sequence 69, while messages 70–71 remained as the raw tail. Both final maps therefore left “restore the auth proxy” active even though the retained user message said the proxy had already been reinstated successfully; the gated map also retained the direct-debug route as current. The effective provider context remained coherent because the newer raw tail corrected the stale map, but the map alone was not a truthful view of current session state. The recovery-card author already receives retained recent history as non-source lookahead and used it to resolve the final supersession, so its final artifact accurately recorded the successful proxy restoration and closed the earlier Caddy, redirect, browser, and proxy theories in favour of the Polars ARM incompatibility and `polars[rtcompat]` fix.

This result does not satisfy the Slice 4F exit gate yet. The maps meet the size, provenance, raw-preservation, and effective-context continuity goals, but the standalone current-state contract and map UI would be misleading at the newest boundary. The next narrow experiment is to give map authoring the retained recent tail as explicitly untrusted, non-citable lookahead used only to remove, close, or supersede stale claims; the tail itself remains verbatim context and should not be duplicated into the map. Add a deterministic scenario that proves a retained-tail completion cannot leave the older next action active, then rerun only the two map conditions against this frozen recovery baseline. After that, run the same bounded active-transcript retrieval questions against the three final effective histories and record tool calls, canonical windows, and grounded answers. Only if those checks pass should a second held-out transcript be used to test whether the result generalizes.

#### Slice 4F2 retained-tail correction and retrieval result

Map authoring now receives the retained canonical suffix in a separate `retained_recent_lookahead` prompt section. Lookahead carries message indexes, roles, and content but no source ranges. The author may use it only to omit state that newer retained messages complete, abandon, or supersede; it may not create or expand claims from lookahead or cite lookahead as provenance. Authoring-task metadata and lifecycle events record only its message count and canonical start/end boundaries. Deterministic validation proves that an evicted “create README” action is absent when the retained tail says the README is complete, that the raw tail remains verbatim, and that an attempted citation to a lookahead-only message fails provenance validation.

The two map conditions were rerun over the same held-out transcript and fixed boundaries without regenerating the recovery baseline. Neither final map retained the temporary direct-to-Marimo diagnostic route or the stale “restore the auth proxy” next action. Both retained the earlier durable security goal and constraint, per-service proxy decision, dedicated finance hostname, and `polars[rtcompat]` resolution. The successful proxy restoration remained only in the verbatim two-message tail, so the effective context was accurate without duplication. The final standalone maps were therefore accurate through their explicit consumed boundary but intentionally incomplete about newer retained state; the session-map UI must expose that boundary and should not imply that the map alone includes the live tail.

The corrected maps became smaller in this run. The unconditional map averaged 636 estimated artifact tokens and 2,301 effective tokens after reduction, with a final 669-token map and 1,996-token effective context. The gated map averaged 618 artifact tokens and 2,284 effective tokens, with a final 601-token map and 1,928-token effective context. Jev again authored at every boundary, with scores 0.85, 0.66, 0.76, and 0.87, so it added four classifications without saving a generative call in this change-dense replay.

The same explicit source-sensitive prompt was then run through the normal chat and `session_ops` paths against all three saved final states. Every condition used `search_transcript` followed by one or more bounded `get_transcript_window` calls and correctly identified the Polars ARM incompatibility, the `polars[rtcompat]` fix, the final Caddy → discourse-auth-proxy → Marimo request path, and the displaced Caddy, redirect, browser-state, and proxy hypotheses with correct canonical sequence anchors. The recovery-card condition used one search plus one window, the unconditional map used one search plus four windows, and the gated map used three searches plus three windows. This single stochastic sample proves evidence recoverability, not retrieval efficiency; it suggests that a smaller map can require more targeted retrieval work when the question spans several historical branches, while still remaining far below replaying the full transcript.

Slice 4F2 therefore passes its narrow correction and evidence-recovery gate. It does not yet establish the overall stepped-eviction exit gate: the result needs one additional held-out long transcript, and Jev still needs a steady-state small-eviction test because coarse, change-dense batches are expected to trigger authoring.

#### Slice 4F3 generalization protocol

Use the previously unexamined `Brainstorm coop intake system` transcript as the second held-out case. Its 33 canonical messages contain several explicit reversals and scope reductions rather than a debugging narrative: the initial custom-Discourse-plugin direction is rejected; the broad lifecycle system is narrowed to digital intake that leaves downstream committee work unchanged; bespoke YAML-defined questionnaires are reframed as an implementation detail; the product settles on a standardized common core with a bounded custom-question block; a concrete MVP and Django-based implementation path are proposed; the data model is accepted and extended with per-co-op unit-type availability; and the final schema is written to `PHHC/Intake form.md`. Replay cumulative batches ending at sequence indexes 14, 18, 22, 26, and 32 so each boundary follows a completed conversational group and the same five reductions can be compared across recovery cards, unconditional maps, and Jev-gated maps.

Freeze the following semantic checklist before generation: no custom Discourse plugin is current; the first product slice digitizes intake and deliberately leaves later committee workflow alone; co-op autonomy remains important; the form approach evolves from fully bespoke YAML schemas to a standardized core plus a small bounded custom block; the YAML/config layer is not itself the product value; the purpose-built intake backend, review surface, controlled data, and future lifecycle path are the product value; the working MVP estimate is roughly 8–12 days or two to three focused weeks, with a tighter one-co-op pilot estimated around one and a half to two weeks; Django and Django Admin are the recommended MVP stack; the accepted model separates co-op, versioned form config, application/applicant/household/housing need/eligibility/custom answers, communications, status history, and users; unit-type availability is configurable per co-op; the open behavior for currently closed unit types is explicitly deferred; and `PHHC/Intake form.md` is the final durable artifact. Score only claims established by each boundary and use the same omission, accurate-current-state, stale-labelled-history, stale-current-state, and unsupported-promotion categories as the primary replay. Do not alter prompts, models, thinking, gate threshold, or context settings after inspecting generated output.

#### Slice 4F3 generalization result and provenance correction

The second held-out replay completed with the frozen five boundaries and unchanged model settings. All three conditions preserved the 33 canonical raw messages. Recovery cards averaged 1,557 estimated artifact tokens and 3,769 effective tokens, with five generative calls taking 129.0 task-seconds. Unconditional maps averaged 673 artifact tokens and 3,041 effective tokens, with five author calls taking 60.0 task-seconds. Gated maps averaged 606 artifact tokens and 2,974 effective tokens, with four author calls plus four classifications taking 44.9 task-seconds. All syntactic map source ranges resolved.

Jev produced the first useful deferral observed in a controlled replay. Scores were 0.67, 0.70, 0.43, and 0.56 after the initial unconditional map. The 0.43 boundary contained the MVP-estimate and stack discussion but did not materially change the durable product map, so the unchanged map was defensible; the skipped evidence was carried forward. The next boundary added user acceptance of the schema direction and the per-co-op unit-availability constraint, scored 0.56, and triggered authoring from the cumulative evidence. This saves one generative call without losing the later evidence and supports the cumulative gate design, although one sample is not sufficient for threshold tuning.

The replay also disproved the safety of the non-citable retained-lookahead prompt contract. One gated final map claimed that `PHHC/Intake form.md` had been created and described its contents while citing only messages 27–28, which establish the unit-type design but not file creation; file creation occurs only in retained messages 29–32. The same map retained the older “form model remains under consideration” entry even though the retained tail crystallized and wrote the final schema. The source ranges therefore resolved mechanically but did not entail the tail-derived claim. This is provenance laundering, and stronger prompt wording cannot make the distinction enforceable.

Replace non-citable lookahead with citable retained evidence. Keep the eviction boundary, which controls what raw messages leave effective context, separate from an observed boundary recording how far the author read. Retained messages remain verbatim and should be represented in the map only when needed for salient current state, but any retained-tail claim must cite its actual canonical message IDs and pass the same provenance validator as evicted evidence. The map checkpoint and UI must expose both boundaries so a current map is not confused with the smaller consumed range. Add deterministic coverage proving that a retained completion can remove a stale action, a retained durable artifact can be recorded only with its true tail citation, and an unavailable source still fails. Rerun only the two map conditions on the frozen second corpus before any further gate tuning.

The correction now treats retained messages as first-class canonical evidence with one-message source ranges. Authoring, task metadata, checkpoint validation, and checkpoint metadata all use that contract. An authored checkpoint records both its eviction boundary and the newest message observed by the author; a deferred checkpoint advances the eviction boundary while retaining the prior authored-observation boundary. Deterministic scenarios prove a retained completion can replace a stale next action with a correctly cited artifact, unavailable provenance still fails the governed author task, the retained raw suffix is unchanged, and both boundaries persist distinctly.

The two map conditions were rerun without changing the corpus, five boundaries, author model, thinking level, or Jev threshold. The prior laundering failure did not recur. Both final maps evicted through message 28 and explicitly observed through message 32. The unconditional map cited the final file with ranges 27–29 and 31–32; the gated map cited the tool-confirmed creation at 31–32. The gated map classified the common-core/custom-block direction as `assistant_proposed`, avoiding unsupported promotion to a user-established decision. It again deferred the fourth boundary at 0.43 and authored the accumulated fifth boundary at 0.61. Artifact sizes for the corrected unconditional run were 439, 570, 665, 850, and 745 estimated tokens; the gated run used 658, 674, 767, 767, and 804. The final effective contexts were 4,606 and 4,665 tokens respectively, slightly larger than the frozen 4,508-token recovery result because stepped eviction retained the complete four-message file-write group rather than the recovery card’s three-message tail. Across the earlier five-boundary run, map contexts remained materially smaller on average than recovery cards.

Slice 4F now provides two-domain evidence that the map representation is substantially smaller, preserves raw evidence, supports exact retrieval, and can carry source-linked current state without relying on non-citable lookahead. It also provides one defensible cumulative Jev deferral. This is sufficient to continue the experiment, not to declare the strategy generally superior: the next decision boundary is a labelled steady-state gate probe with repetition, transient analysis, completion, and durable changes, followed by UI wording for observed versus evicted boundaries if the classifier behavior remains credible.

#### Slice 4F4 labelled steady-state gate result

A Jev-only probe exercised the production `session_map_classification` task and unchanged `session-map-gate-v1` question at the provisional 0.5 threshold. Eight preregistered cases ran three times each. Repetition scored 0.03–0.04; cumulative repetition plus minor clarification scored 0.06; transient brainstorming scored 0.15–0.17; and cumulative brainstorming plus an unaccepted assistant proposal scored 0.18–0.22. Cumulative evidence ending in explicit completion scored 0.79–0.81; cumulative evidence ending in user adoption of the proposal scored 0.80–0.82; an explicit constraint reversal scored 0.91 in all trials; and a durable artifact relocation scored 0.91 in all trials. All 24 governed tasks completed, and every score fell on the preregistered side of 0.5 with substantial separation.

Together with the natural replay’s 0.43 deferral followed by a 0.56/0.61 cumulative trigger, this established that the simple movement-only classifier could separate the tested cases at 0.5. It did not establish universal classifier accuracy, justify using Jev to assign map dimensions, or demonstrate enough avoided authoring to support permanent runtime adoption. The later Slice 4G decision therefore retires this gate after recording the result.

#### Slice 4F5 checkpoint inspection result

The session list now exposes a map action only when a session has at least one persisted session-map checkpoint. The action opens a read-only modal backed directly by the append-only context-checkpoint store rather than restoring the retired live-map database or patch-operation surfaces. The modal defaults to the latest checkpoint, permits inspection of every historical revision, groups typed entries by map dimension, shows evidence basis and canonical source ranges, and distinguishes the authored-observation boundary from the raw-history eviction boundary. Deferred revisions are labelled so an unchanged authored map is not mistaken for a newly generated one. The API enforces the same vault/session authorization as the existing session surfaces and returns `404` for a checkpoint that does not belong to the selected session.

Targeted deterministic validation proves that the session-list capability flag follows checkpoint presence, the endpoint defaults to the latest immutable revision, historical selection returns the original typed map, and both boundaries survive serialization. Frontend smoke coverage verifies controller availability and script ordering. The checkpoint, retained-evidence, and post-turn gate scenarios pass together, as do Ruff, Black, and mypy.

### Slice 4G: Retire the map-author gate

**Status:** Complete. Stepped eviction now authors unconditionally at every planned watermark crossing. Gate prompts, runtime dispatch, settings, task types, API scores, and executable gate experiments are removed. Settings repair strips persisted gate keys, while generic TypeSafe/Jev configuration and the provider-neutral decision runtime remain. A narrow compatibility reader rehydrates a pending canonical range from an already-deferred checkpoint into the next author pass; new checkpoints cannot write pending or classification state. Focused map, settings, API/UI, and generic decision-model validation passes, as do Ruff, Black, and mypy.

**Decision:** Remove the gate-specific production path. The experiment established that Jev can classify obvious movement reliably in the tested cases, but the gate changes only the timing of an inevitable map rewrite. Large or change-dense eviction batches trigger authoring consistently, while smaller quiet batches can be handled more simply by tuning the existing high-watermark and low-watermark interval. The single natural deferral and clean labelled probe do not demonstrate enough avoided generative work to justify permanent classifier settings, task orchestration, checkpoint state, failure handling, and UI concepts.

Retain the generic TypeSafe/Jev provider, encrypted secret integration, `decision` capability, provider-neutral decision adapter, and generic live decision probe. They are reusable infrastructure for retrieval experiments and a future general-purpose decision tool, and are not coupled to map maintenance. Retain the unconditional stepped-map author, provenance contract, eviction planner, checkpoint history, retrieval tools, and map inspection surface.

Remove the map-gate prompt and module, gate settings and accessors, classification task kind and label, compaction dispatch and deferral branch, classification result fields from new reduction results, gate scores from the map API and modal, and gate-specific deterministic and live experiment scenarios. Historical results remain in this plan, the experiment findings document, and Git history; executable experimental code is not the archive.

Persisted-state cleanup must be one-way and lossless. Remove the three gate settings through the normal settings-upgrade path. Existing append-only checkpoints may retain inert historical classification metadata. If the latest checkpoint contains a deferred pending-evidence range, the next stepped reduction must rehydrate that canonical range and include it in one unconditional authoring pass before writing a checkpoint without pending state. This narrow compatibility reader may remain until a later data-version boundary can prove no active checkpoint requires it; no new deferral or pending range may be written.

**Validation target:** Extend the unconditional post-turn scenario to prove that every planned eviction invokes one governed author task and no classification task. Add a focused legacy-checkpoint case proving that pending canonical evidence from a prior deferral is incorporated exactly once and then cleared. Cover removal of persisted gate settings, historical checkpoint readability, current modal rendering without gate fields, and generic Jev decision configuration. Run the focused map, settings-upgrade, API/UI, and decision-model scenarios plus the production Python quality gate.

### Slice 4H: Tune the retained-context watermark and harden real-history grouping

**Status:** Complete. The disposable comparison selected 20,000 tokens as the experimental default, and deterministic coverage now permits abandoned historical user turns to leave effective context without weakening active-turn or tool-history safety.

The comparison held the 150,000-token high watermark, Terra map author, low thinking effort, authoring prompt, and canonical replay boundaries constant while testing 20,000-token and 40,000-token low-watermark targets. A clean 360-message suffix of the 1065 redevelopment transcript was replayed through three reductions per condition. The 20,000-token run produced effective contexts of approximately 62,700, 43,900, and 17,300 tokens; the 40,000-token run produced approximately 62,600, 43,800, and 26,200. A clean driving-planning control produced approximately 25,400 and 25,700 tokens. Complete provider-history groups frequently quantized both settings to the same eviction boundary, and one tool-heavy retained group alone contained roughly 61,600 estimated tokens.

The 20,000-token maps remained semantically viable across the three successive rewrites. At the final checkpoint the map preserved the current compensation offer, side-letter protections, version-3 agreement issues, outstanding underpinning review, consultant arrangement, and future reciprocal-rights question with canonical provenance; it also replaced stale request-stage state with the later offer. No obvious stale-current-state or unsupported-promotion failure was found. When 20,000 and 40,000 selected the same boundary, their map differences reflected independent generative variation rather than the setting. Use 20,000 as the next opt-in live tuning value because it realizes the smaller context when group boundaries permit and safely degrades to retaining the newest complete group when they do not. Keep 40,000 available as a comparison setting rather than adding another runtime mode.

The replay uncovered a separate correctness issue in canonical history. The full 1065 transcript contains several abandoned user turns without assistant responses, and the earlier planner could not cross one after it entered the evictable prefix. The planner now recognizes that a later user or system input closes the earlier group for eviction purposes. The abandoned content remains canonical evidence, appears exactly once in its own outgoing envelope, and retains its source index. The newest group is still never evicted, and the existing tool-history validator continues to block malformed or unresolved tool exchanges.

**Validation result:** The planner and canonical-envelope scenarios prove the abandoned input is evicted exactly once with stable canonical provenance, the newest incomplete turn remains retained, and malformed tool history remains ineligible. The retained-evidence, pending-evidence, post-turn, and readiness scenarios pass with the correction. The experimental template and fallback default are now 20,000 tokens; settings loading preserves an existing user's explicit value. During live evaluation, record requested and actual post-reduction tokens, the size of the newest retained group, map tokens, retrieval count, and any stale-current-state or omitted-current-state finding.

### Slice 4I: Multi-turn live-path validation and retrieved provenance

**Status:** Complete. An initial 15-turn Terra-to-Terra conversation completed through the public chat-task API and normal task executor with eleven successful Compaction v2 checkpoints before a retrieval-heavy retrospective turn exposed a provenance boundary. The raw transcript contained 70 canonical messages because ordinary tool calls and results were preserved, and all three retrospective answers remained semantically accurate despite the later authoring failure and recovery-card fallback.

The failed twelfth author pass tried to cite canonical message 6 after a retained `get_transcript_window` result had recovered that old source. The author could see the source, but provenance validation admitted only the outgoing interval, retained outer messages, and prior-map sources. Compaction v2 now excludes the retrieval envelope itself, mechanically resolves same-session window sequence IDs back to canonical raw messages, presents those messages in a separate authoring evidence section, and admits only those resolved sources to provenance validation. A deterministic regression proves that the retrieval envelope is not citable while its canonical source is.

The corrected live rerun passed. It produced fourteen consecutive Compaction v2 checkpoints across fifteen completed turns, and all fourteen authoring tasks completed through the task executor. Retrieval-heavy revisions mechanically admitted thirteen and five canonical historical messages without citing their generated retrieval envelopes or switching to recovery-card history. The canonical transcript retained 46 messages and approximately 39,800 estimated tokens, while final effective context contained the latest map plus one user/assistant pair at approximately 2,100 tokens. The retrospective answers correctly recovered the replaced initial budget and site assumptions, the paused resident-fit workstream and its conditional resumption, and the current deliverable, settled constraints, and open temperature-dataset question. This establishes live-path feasibility across repeated reductions and retrieval; it does not yet establish long-horizon superiority over Compaction v1.

## Contract-Sensitive Areas

- `session_ops` input schema, descriptions, error messages, and operation-specific parameter validation.
- Vault/session authorization and current-session defaulting.
- SQLite migrations, FTS availability, backfill, triggers, cascade cleanup, and rebuild behavior.
- A strategy-neutral transcript-search result contract so lexical and later hybrid ranking return the same canonical anchors and bounded evidence.
- Canonical sequence anchors and behavior across session forks, imports, and purges.
- Tool-output size, continuation cursors, token estimates, and handling of oversized messages.
- Separation between raw canonical transcript access and effective provider-safe history.
- Prompt-injection treatment of retrieved content.
- Removal of persisted live-map settings and tables from already-used development databases.
- Removal of persisted map-gate settings without stranding deferred canonical evidence.
- Compatibility of existing session summary and deep-search consumers.

## Implementation Sequence

1. Slice 1A: retire live-map runtime wiring, settings, API/UI surfaces, tests, and derived tables while retaining generic decision infrastructure and the canonical range-read primitive; stop and verify the cleanup gate.
2. Slice 1B: add the transcript FTS migration, lifecycle triggers, rebuild operation, bounded vault-scoped retrieval service, and focused storage/service tests; stop and verify the storage gate.
3. Slice 1C: add `search_transcript` and `get_transcript_window` to `session_ops`, including operation-specific validation, cursor integrity, documentation, and real-tool coverage; stop and verify the tool gate.
4. Slice 1D: rebuild `search_sessions(mode="deep")` on the shared canonical index, add the cross-layer integration scenario, and run the full deterministic core profile; stop and verify the overall Slice 1 gate.
5. Slice 1E: harden derived source projection and provenance, exclude retrieval-generated envelopes from candidate generation, backfill the canonical index, and rerun the Slice 1 validation gate.
6. Close Slice 2 after live-path feasibility, remove its disposable probe, and defer hybrid ranking until an observed lexical recall failure justifies it.
7. Add the fixed Compaction v1 recovery-card transcript-search preamble and defer generated card references while Compaction v2 is evaluated.
8. Implement Slices 4A and 4B as deterministic planning and provenance foundations with no runtime activation.
9. Prove the bounded session-map artifact and unconditional authoring path before adding opt-in Compaction v2 runtime eviction.
10. Retire the map-author gate after the controlled evaluation, preserve one-way pending-evidence reconciliation, and keep the generic decision runtime for separately justified uses.
11. Tune Compaction v2 to a 20,000-token low-watermark default, harden abandoned historical turns, and continue direct live comparison against Compaction v1 before considering default promotion.
12. Validate repeated Compaction v2 checkpoints through a normal multi-turn chat, including retrospective transcript retrieval whose mechanically resolved canonical messages remain valid map provenance.

## Immediate Next Steps

Continue bounded opt-in comparison against Compaction v1 at the 20,000-token low-watermark default, focusing on repeated-reduction salience, stale current state, provenance recovery, total authoring and retrieval cost, and actual post-reduction size under whole-group constraints. Investigate the noisy but non-destructive first-turn maintenance case where a threshold-crossing tool-heavy turn contains no complete evictable prefix; it should defer cleanly rather than attempt an impossible recovery-card fallback. Do not make Compaction v2 the default or remove Compaction v1 until repeated live reductions demonstrate a clear operational advantage. No additional Jev memory experiments are planned on this branch.

## Evidence and Design Sources

### Repository and local design sources

- [ADR 0003: Chat Sessions Canonical SQLite](docs/development/adr/0003-chat-sessions-canonical-sqlite.md)
- [ADR 0006: Session Summaries as Derived Memory Indexes](docs/development/adr/0006-session-summaries-derived-memory-indexes.md)
- [ADR 0012: Chat History Broker](docs/development/adr/0012-chat-history-broker.md)
- `/home/codeman/docs/AssistantMD/chat-transcript-archive-retrieval-feature-sketch.md`
- `/home/codeman/docs/AssistantMD/jev-capability-memory-and-research-sketch.md`
- `/home/codeman/docs/AssistantMD/memory-authority-layer.md`

### External evidence informing later slices

- [Lost in the Middle](https://arxiv.org/abs/2307.03172) shows that merely fitting more material into context does not guarantee uniform use of that material; bounded retrieval can place the relevant evidence where it is usable.
- [LongMemEval](https://arxiv.org/abs/2410.10813) separates extraction, temporal reasoning, knowledge updates, assistant-originated information, and abstention, and supports evaluating provenance-aware retrieval rather than only summary resemblance.
- [MemGPT](https://arxiv.org/abs/2310.08560) is useful evidence for explicit movement between bounded working context and externally retrievable state, but does not establish AssistantMD's exact eviction or schema policy.
- [LangMem background memory](https://langchain-ai.github.io/langmem/background_quickstart/) supports separating durable transforms from the foreground response path, while its broader memory lifecycle is not required for transcript retrieval.
- [Graphiti](https://github.com/getzep/graphiti) provides relevant source-lineage and supersession patterns; its graph and embedding stack remain out of scope.
- [Pydantic AI decision models](https://ai.pydantic.dev/models/decision/) and [TypeSafe integration](https://ai.pydantic.dev/models/typesafe/) document generic platform capability retained outside the Compaction v2 design.

These sources motivate experiments; they do not prove that Compaction v2 will outperform Compaction v1. Repeated live comparison through the shared compaction boundary remains the promotion gate.
