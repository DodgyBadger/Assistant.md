# Session Transcript Retrieval and Context Evolution Plan

## Status

Planning complete. Slice 1 and all five of its checkpoints are implemented and validated. Slice 2 established live-path feasibility and is closed without a larger trigger benchmark. Slice 3 makes recovery cards the conditional routing surface for transcript search and is complete without generated source references. Slice 4 begins a stepped-eviction map experiment behind the existing compaction boundary. The completed foundation removes the abandoned live-map runtime, indexes canonical transcripts, exposes bounded source retrieval, reuses the same backend for broad deep-session discovery, and keeps generated retrieval envelopes from becoming their own evidence.

## Decision Summary

AssistantMD's immediate long-session problem is not attention while relevant messages remain inside the model's effective context. It is the loss of detail, salience, and provenance after compaction replaces an older prefix with generated prose. Recovery cards remain useful for continuity, but repeated cards can reinterpret prior cards and cannot let the agent inspect the original evidence.

The first product slice will therefore add sparse, bounded access to the canonical raw transcript through the existing `session_ops` tool. Lexical FTS is the initial ranking baseline, not a permanent restriction: the retrieval boundary must allow a later hybrid lexical/semantic strategy to reuse AssistantMD's existing session-search vector machinery without changing the tool contract. Slice 1 will not add embeddings, an autonomous memory author, or a second context representation. The current recovery-card compaction path remains unchanged while retrieval reliability is measured.

A later experiment may replace checkpoint compaction with stepped eviction: keep a bounded recent transcript window, classify only the message groups leaving that window, preserve source-linked state derived from those groups, and retrieve raw evidence on demand. That direction becomes credible only after the retrieval primitive works reliably. It is not part of Slice 1.

The live session-map implementation will be removed rather than left dormant. The generic TypeSafe/Jev provider, `decision` model capability, secret integration, and provider-neutral decision runtime will remain because they are reusable platform capabilities and are not session-map code.

## User Outcomes

- An agent can recover exact facts, decisions, wording, drafts, and surrounding context from messages that compaction has removed from effective history.
- Retrieval returns small, explicit, source-linked excerpts rather than injecting an entire transcript.
- Current-session lookup is direct; older-session lookup composes existing session discovery with the same transcript operations.
- Results never cross the active vault or existing session-ownership boundary.
- Canonical messages remain authoritative. Summaries, recovery cards, search indexes, and any later map are derived aids whose claims can be checked against source messages.
- Normal chat, compaction, and server startup do not depend on Jev or another optional service.

## Scope

### In scope now

- Add `search_transcript` and `get_transcript_window` operations to `session_ops`.
- Add a persistent lexical index over canonical raw chat messages in `chat_sessions.db`.
- Rework `search_sessions(mode="deep")` to use the same raw-message index instead of building a temporary index over effective histories.
- Add bounded result contracts, stable message anchors, vault authorization, truncation/continuation behavior, and prompt-injection-safe tool guidance.
- Add concise system-owned retrieval guidance only after compaction by placing it in every recovery card.
- Remove the live session-map product experiment and its obsolete plan, while retaining generic decision-model infrastructure.

### Deferred

- Hybrid lexical/semantic transcript ranking, evaluated behind the same bounded retrieval interface using the existing session-search vector infrastructure where practical.
- Automatic retrieval on every turn.
- Generated source references inside recovery cards; transcript search is the recovery-card routing mechanism while source ranges are tested as mechanical map metadata.
- Sliding or stepped context eviction.
- A replacement session-state map derived only from evicted messages.
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
| Future context reduction | `core/chat/compaction.py` or a factored strategy within that boundary | Compare checkpoint compaction and stepped eviction later without creating a parallel compactor. |

`ChatStore.get_stored_messages_range(...)`, introduced during the map work, is a generally useful canonical range primitive. Retain or refactor it under the retrieval service rather than deleting it with the map package. Everything that embeds session-map policy into chat execution should be removed.

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

Recovery cards remain the durable fallback when the optional classifier or map author is unavailable. Their fixed preamble queues transcript search without exposing lower-level window mechanics. If recovery cards remain the primary continuity representation after Slice 4, direct references can be reconsidered with an explicit anchor-accuracy evaluation.

**Decision:** Deferred. Source ranges belong first on deterministic eviction envelopes and derived map entries, where the ranges are mechanically known rather than generated by the recovery-card author.

## Slice 4: Stepped Eviction Experiment

Compare checkpoint compaction with a sliding high-watermark/low-watermark strategy through independently testable checkpoints. The experiment remains opt-in until the final comparison proves it safe and useful; ordinary sessions continue to use recovery-card compaction.

The candidate design is stepped eviction, not naive FIFO truncation:

1. Keep complete recent messages until a high token watermark is crossed.
2. Select a coherent oldest prefix of complete turn/tool groups to remove until the low watermark is restored.
3. Before removal, append the outgoing groups to a cumulative pending-evidence buffer with mechanically derived source ranges.
4. Use a cheap decision model to detect whether the cumulative evidence materially moves the current map, and invoke a generative whole-map update only when the scalar probability crosses a tunable threshold.
5. Commit the derived update and eviction checkpoint atomically enough that no canonical message leaves effective context without either retained recent context or a durable pointer/state representation.
6. Keep every raw message in canonical SQLite and make it retrievable through Slice 1.

Jev's plausible role shifts from repeatedly judging the entire live conversation to cheaply detecting material movement in cumulative evidence leaving the active window. The classifier does not author prose, decide which map fields may change, or become a required dependency. A provider-neutral interface must continue to admit another hosted or local decision model.

The state representation for this experiment must be designed from eviction evidence rather than copied from the retired live map. It should remain bounded, favor active state and provenance over narrative history, and compact or archive superseded entries deterministically. If classification or authoring is unavailable, the system must fall back to existing recovery-card compaction without losing the chat turn.

### Slice 4A: Deterministic eviction planner

**Status:** Complete. A pure planning boundary accepts provider-native effective history plus high and low token watermarks and returns an oldest-prefix boundary with token and grouping audit metadata without mutating storage. It plans only after the high watermark is crossed, preserves chronological order, retains the newest group, and never splits a complete user/assistant turn or tool-call/result exchange. It reports stable no-op reasons for history below the high watermark, invalid tool history, an incomplete evictable prefix, or a history with no older group to evict. When the newest indivisible group alone exceeds the low watermark, it safely evicts older complete groups and reports that the target remains exceeded.

The deterministic scenario covers ordinary turns, multiple tool calls and returns in one exchange, oversized indivisible groups, an existing recovery-card message, incomplete turns, malformed tool history, watermark validation, and stable token accounting. Slice 4A does not invoke Jev, author a map, change effective history, add settings, or persist state.

### Slice 4B: Provenance-bearing eviction envelopes

Resolve every planned outgoing group to canonical raw sequence ranges before it can leave effective context. Persist or expose an immutable experiment envelope containing the source ranges, projected text, protocol grouping, token estimate, and history revision. A range is mechanically derived from storage identity; neither Jev nor a generative model may invent it. Repeated planning must not classify or map the same canonical range twice.

### Slice 4C: Optional classification through task execution

Route eviction classification through the normal task executor using the provider-neutral `decision` capability. Jev receives the bounded current map plus all cumulative unincorporated eviction envelopes and returns one `material_map_update_probability`. It does not classify dimensions or constrain the later author. Runs remain visible and governed like other tasks. Failure, missing configuration, timeout, or an excessive pending-evidence buffer leaves effective history untouched and routes the session to existing recovery-card compaction.

An ephemeral Jev probe over six transitions from the private 1065 redevelopment transcript supports using the classifier as a conservative authoring gate, but not as an authority that discards evidence. Bundled six-field decisions were fast and repeatable: calls completed in roughly 0.25–0.43 seconds, and identical reruns generally moved scores by only a few hundredths. Clear artifact creation and major legal-state changes separated from an already-represented negative control, while subtler active-draft and narrative changes clustered in the ambiguous middle. Cumulative evidence correctly kept major courtyard and compensation changes elevated, but scores were not monotonic as more assistant analysis arrived. Independent field calls strengthened artifact detection but also produced a likely false decision update, showing that removing neighboring map context can trade one kind of interference for another. These results justify testing a movement gate; they do not establish a benefit from retaining dimensional classification.

Begin Slice 4C with one scalar movement decision over the current map plus cumulative unincorporated evidence. Use one tunable threshold and keep pending evidence until a successful whole-map update incorporates its canonical source ranges. Before runtime activation, build a larger labelled eviction corpus that distinguishes persistent state changes from transient completed actions, assistant proposals, and ordinary draft refinement. Reconsider dimensional classification only if the scalar gate exhibits a repeatable class-specific miss that decomposition could plausibly correct.

### Slice 4D: Bounded eviction-derived map

Design the smallest state schema from labelled eviction evidence, then author updates only for dimensions the classifier marked as changed. Entries carry canonical source ranges, explicit active/superseded/closed state where applicable, and deterministic size limits or archival rules. The map favors current goals, decisions, constraints, unresolved questions, and live artifacts over narrative history. This slice must not reintroduce the retired map schema by default.

### Slice 4E: Opt-in effective-history strategy

Compose the bounded map with the recent retained suffix and activate stepped eviction only under an explicit setting with a ready classification model. Commit the map update and eviction checkpoint so a source group cannot disappear from effective context before its durable envelope and any required state update exist. Preserve raw canonical SQLite messages and all Slice 1 retrieval operations. On any pre-commit failure, keep the old effective history and use recovery-card compaction.

### Slice 4F: Comparative live validation

Compare two primary conditions over long, multi-compaction conversations:

- Current recovery-card compaction plus bounded transcript retrieval.
- Stepped eviction plus an eviction-derived state representation and bounded transcript retrieval.

Score current-task continuity, exact-evidence recovery, semantic and salience drift, unsupported claims, effective-context size, model calls, latency, cost, and prompt-cache behavior. Include the retained recent tail as a separately measured contributor because earlier testing showed that it can mask weaknesses in the compact representation.

Keep recovery-card reference generation outside the comparison unless the first two conditions expose a specific evidence-recovery failure that search cannot address. Score current-task continuity, exact-evidence recovery, semantic and salience drift, unsupported claims, effective-context size, model calls, latency, cost, and prompt-cache behavior. Include the retained recent tail as a separately measured contributor because earlier testing showed that it can mask weaknesses in the compact representation.

**Exit gate:** Stepped eviction advances only if it meets or exceeds recovery-card compaction on continuity and evidence fidelity, maintains strict context bounds, and has a simpler or clearly more valuable operating profile. Otherwise retain checkpoint compaction and retrieval and remove experimental runtime code.

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
- Compatibility of existing session summary and deep-search consumers.

## Implementation Sequence

1. Slice 1A: retire live-map runtime wiring, settings, API/UI surfaces, tests, and derived tables while retaining generic decision infrastructure and the canonical range-read primitive; stop and verify the cleanup gate.
2. Slice 1B: add the transcript FTS migration, lifecycle triggers, rebuild operation, bounded vault-scoped retrieval service, and focused storage/service tests; stop and verify the storage gate.
3. Slice 1C: add `search_transcript` and `get_transcript_window` to `session_ops`, including operation-specific validation, cursor integrity, documentation, and real-tool coverage; stop and verify the tool gate.
4. Slice 1D: rebuild `search_sessions(mode="deep")` on the shared canonical index, add the cross-layer integration scenario, and run the full deterministic core profile; stop and verify the overall Slice 1 gate.
5. Slice 1E: harden derived source projection and provenance, exclude retrieval-generated envelopes from candidate generation, backfill the canonical index, and rerun the Slice 1 validation gate.
6. Close Slice 2 after live-path feasibility, remove its disposable probe, and defer hybrid ranking until an observed lexical recall failure justifies it.
7. Add the fixed recovery-card transcript-search preamble and defer generated card references while stepped eviction is evaluated.
8. Implement Slice 4A as a pure deterministic planner with no runtime activation, then stop at each subsequent Slice 4 checkpoint for evidence-driven review.

## Immediate Next Steps

Begin Slice 4B by resolving a planned effective-history prefix to immutable canonical raw sequence ranges without changing runtime history. Do not add Jev calls, map persistence, settings, or runtime routing until envelope identity, revision, and idempotency contracts are proven.

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
- [Pydantic AI decision models](https://ai.pydantic.dev/models/decision/) and [TypeSafe integration](https://ai.pydantic.dev/models/typesafe/) remain references for future eviction classification, not dependencies of Slice 1.

These sources motivate experiments; they do not prove that stepped eviction will outperform AssistantMD's existing compaction. The comparison in Slice 4 is the decision boundary.
