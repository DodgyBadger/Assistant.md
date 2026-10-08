# Session Memory Branch Hardening Plan

## Resolved live regression: Stop followed by unresolved tool-call history

- Investigate session `Ashley_NCC_20261008_120834_479_m4ys`, whose next chat task failed with Pydantic AI's unprocessed-tool-calls admission error after Stop. Do not delete transcript messages or replay potentially effectful pending calls as a repair.
- Strengthen `integration/core/chat_cancellation` to stop a real Pydantic AI tool execution rather than a fake idle stream, verify vault rollback, and verify a successful next turn with protocol-valid persisted history. This case passes in `validation/runs/reports/20261008_233516_550845.md` and does not reproduce the reported failure.
- Logfire confirms that an approved inline-edit resume was cancelled at 23:30:11 UTC, its review became cancelled, and the next prompt loaded the same 146-message history and failed. The deferred-review terminal hook updates review status without resolving the already-persisted pending calls. This is independent of compaction.
- Add `integration/core/deferred_review_interruption` using real Pydantic AI approval/tool execution, cancelled and failed resume paths, vault rollback, interrupted-result semantics, no tool replay, and successful subsequent chat admission.
- Atomically append explicit interrupted tool replies and change the review status for cancelled/failed resumes. Only unanswered calls belonging to that review may be closed; preserve existing results and canonical evidence, and reject unrelated protocol damage. Completed resumes must not acquire synthetic results.
- Emit `chat_deferred_review_history_closed` after commit, with `session_id`, `vault_name`, `artifact_ref`, `status`, and `closed_call_count`. The synthesized result records lack of a durable outcome, not a claim that the tool succeeded or had no effects.
- Validate atomic rollback and repeated terminal-hook behavior as well as ordinary cancellation, deferred-review completion, and stream retry. Existing broken sessions with later prompts require a separate evidence-preserving repair decision; do not silently rewrite canonical history or replay their tools.
- Implemented in the shared deferred-review terminal boundary, including failed resume admission. The focused four-scenario profile passed in `validation/runs/reports/20261008_234330_769772.md`; the expanded settlement checks cover partial results, portable provider call IDs, duplicate hooks, transaction failure, and unrelated unresolved calls. The complete deterministic profile passed 133/133 in `validation/runs/reports/20261008_234442_538284.md`. Ruff, Black, and MyPy passed against the final production changes. No production session or fork was modified.

## Purpose

This document is the Stage 1 scope and contract map for hardening `dev/live-session-memory` before review and merge. It maps the surviving product design, its module owners, the boundaries where invariants can drift, the deterministic validation that currently protects those boundaries, and the order in which the branch should be hardened.

The comparison baseline is `origin/main` at `9b4dae6a7443893c734a4cd0ce5e503d2f5c351e`. At the start of hardening the branch contains more than 100 commits and changes 99 files, with approximately 15,158 additions and 834 deletions. That breadth makes boundary-first review more useful than reviewing commits or files in isolation.

## Branch Evolution and Surviving Scope

The commit history falls into distinct design eras. The early commits established recovery-card baselines, added generic decision-model support, and explored Jev-gated live maps. The middle of the branch retired the eager live-map experiment, added canonical transcript retrieval, and introduced stepped eviction with source-grounded session maps. Later commits promoted that design as Compaction V2, added strategy pinning and V1-to-V2 upgrades, made manual compaction strategy-aware, exposed map and canonical-history inspection, and corrected session forks to preserve canonical prefixes and safe checkpoint lineage.

The surviving product scope is:

- Compaction V1 remains available as recovery-card compaction.
- Compaction V2 uses a source-grounded session map plus a retained verbatim tail as effective model history while preserving all canonical raw messages.
- Sessions become pinned to the strategy established by their first checkpoint; changing the installation default does not silently reinterpret an existing session.
- A selected V1 session can be upgraded explicitly to V2 without replaying the whole conversation through the chat agent.
- Automatic, suggested, and manual compaction share the same strategy and task-governance rules.
- Canonical transcript search and bounded transcript windows let the agent recover exact evidence omitted from active context.
- The session-map modal exposes map revisions and canonical evicted history, including structured tool activity and fork controls.
- Forks copy canonical history through the selected assistant boundary, associated tool events, and only checkpoints whose complete observation boundary is safe at that fork point.
- Generic decision-model/Jev provider support remains as an independent capability, but Jev is not part of the Compaction V2 control loop.

Historical experiments are evidence, not active architecture. The eager live-session-map runtime, the Jev map-author gate, post-author judging, and reranking experiments must not leave production switches, persistence, task kinds, or unreachable branches behind. Compatibility migrations that add and then remove retired storage are an exception: they may still be required to advance installations created during branch testing safely.

## System Contract Map

### 1. Canonical Chat Persistence and Effective History

| Aspect | Authoritative owner | Connected modules | Contract to preserve |
| --- | --- | --- | --- |
| Canonical messages, sessions, tool events, checkpoints, and lineage metadata | `core/chat/chat_store.py` | `core/chat/schema.py`, `core/database_migrations.py` | Raw messages remain canonical and append-ordered; effective history is a projection, never a destructive rewrite of canonical evidence. |
| Storage schema and release migrations | `core/chat/schema.py` | startup migration service and settings database migration UI | Existing databases advance deterministically through migrations 1-9; FTS, checkpoint kind, and replacement-origin data are present before dependent runtime paths execute. |
| Effective versus raw reads | `ChatStore.get_history()` and `ChatStore.get_stored_messages()` | compaction, API inspection, transcript retrieval, fork creation | Callers choose `effective` or `raw` deliberately; no operation that promises canonical evidence may accidentally read the compacted projection. |
| Checkpoint replacement provenance | checkpoint rows and `replacement_source_sequence_indexes_json` | map inspection, fork-point projection, legacy fallback | Generated checkpoint context has no canonical origin; retained raw messages carry exact canonical sequence origins; ambiguous legacy reconstruction must fail closed. |
| Atomic multi-record mutations | `ChatStore.transaction()` and mutation methods | checkpoint commit, upgrade, fork | Messages, checkpoints, tool events, metadata, and lineage either commit together or remain unchanged. |

Primary hardening risks are partial mutation, accidental raw/effective mode confusion, migration ordering, legacy row compatibility, and oversized ownership in `chat_store.py` obscuring invariants. Refactoring should extract policy only when it creates a clearer authoritative boundary; database transaction ownership must remain centralized.

### 2. Compaction Policy and Orchestration

| Aspect | Authoritative owner | Connected modules | Contract to preserve |
| --- | --- | --- | --- |
| Public compaction operation and status | `core/chat/compaction.py` | API service, `chat_history_compact` tool, chat executor | Every entry point resolves the same pinned strategy, policy, author model, focus semantics, and failure result. |
| Automatic post-turn reduction | `maybe_auto_compact_after_turn()` | `core/chat/executor.py`, task execution | Reduction occurs only after a successfully persisted turn and may not corrupt or block the completed chat response when background authoring fails. |
| High/low watermark planning | `plan_stepped_history_eviction()` | readiness and settings | The high watermark triggers evaluation; the low watermark targets the post-reduction effective history; neither boundary may split a conversational turn or tool exchange. |
| Retained conversational floor | history grouping in `core/chat/compaction.py` | both recovery-card and session-map strategies | At least `compaction_retained_turns` complete newest turns remain verbatim even when that exceeds the low-watermark target. |
| Session lock and revision guard | `chat_session_history_lock()` plus stored history revision | map authoring, V1 compaction, upgrade | Concurrent chat, manual compaction, upgrade, and post-turn compaction cannot commit an output authored against stale history. |
| Strategy pinning | `resolve_session_compaction_strategy()` | readiness, status, manual and automatic paths, upgrade | A checkpoint pins the session; the installation default applies only before a session establishes checkpoint state; mixed V1/V2 checkpoint evolution is rejected except through the explicit upgrade contract. |

`core/chat/compaction.py` is the central integration hotspot at roughly 1,400 lines. Hardening must determine whether recovery-card authoring, eviction planning, and orchestration are genuinely separate responsibilities that should be extracted, while keeping one public operation contract and avoiding parallel service layers.

### 3. Session-Map Domain

| Module | Owned responsibility | Boundary rule |
| --- | --- | --- |
| `core/memory/session_map/models.py` | Bounded schema, entry kinds/states/basis, source ranges, semantic admission, provenance validation | The model-authored result cannot invent references, promote unsupported proposals, or retain invalid lineage; schema validation is independent of API and persistence. |
| `core/memory/session_map/authoring.py` | Prompt payload construction | Prompt composition accepts typed prior map and evidence; it does not own execution, storage, or policy. |
| `core/memory/session_map/evidence.py` | Canonical message evidence and range resolution | Evidence comes from raw stored messages, is contiguous where required, and carries stable session, vault, revision, and digest identity. |
| `core/memory/session_map/service.py` | Governed generative authoring | Every model call runs as a normal execution task, validates structured output within the retry boundary, and returns no durable mutation itself. |
| `core/memory/session_map/checkpoints.py` | Context projection and atomic checkpoint commit/load | A validated map, consumed boundary, observed boundary, evidence metadata, replacement history, and canonical origins are committed consistently. |
| `core/memory/session_map/readiness.py` | Configuration and eligibility decision | Missing or invalid author configuration fails closed with an actionable reason; it does not silently select a different strategy. |
| `core/memory/session_map/__init__.py` | Stable package exports | Expose only contracts legitimately shared outside the domain package. |

The main semantic invariants are conservative state classification, preserved uncertainty, durable older references when claims remain unchanged, narrow citations, a bounded narrative trajectory, and source coverage across prior-map, newly evicted, retained-tail, and explicitly retrieved canonical evidence. The main failure invariant is that malformed model output may retry but can never create a checkpoint unless final provenance validation succeeds.

### 4. Transcript Retrieval

| Aspect | Authoritative owner | Connected modules | Contract to preserve |
| --- | --- | --- | --- |
| Canonical lexical index | `core/chat/schema.py` and chat-store writes | FTS helpers in `core/utils/fts.py` | Inserts, updates, deletes, migrations, replacements, and forks keep FTS consistent with canonical message text. |
| Search and window domain service | `core/chat/transcript_retrieval.py` | `session_ops`, Compaction V2 retrieved evidence | Results are active-session scoped, authority/vault safe, bounded by result and token limits, and source-linked to canonical sequence indexes. |
| Model-facing operations | `core/tools/session_ops.py` | tool registration and default context guidance | `search_transcript` finds candidates and `get_transcript_window` resolves bounded context; tool errors use model-retry semantics where correction is possible. |
| Recovery guidance | `core/constants.py` | recovery-card generation and default context | Retrieval is queued only after older history has left active context; guidance distinguishes active-session transcript retrieval from cross-session discovery. |
| Retrieved evidence admission | `core/chat/compaction.py` | session-map authoring | Only mechanically resolved canonical window messages become citable map evidence; the tool-result prose itself is not evidence. |

Hardening must cover query escaping, empty and pathological queries, cursor tampering, token bounds, stale or deleted sessions, tool-message inclusion, FTS backfill, forked copies, and whether the 839-line retrieval module has distinct search/window responsibilities worth separating without fragmenting its shared bounds and authorization contract.

### 5. Strategy Upgrade

| Aspect | Authoritative owner | Connected modules | Contract to preserve |
| --- | --- | --- | --- |
| Eligibility and status | `core/chat/context_strategy_upgrade.py` | session list/detail API and upgrade button | Only sessions with a V1 recovery checkpoint and no established V2 state are offered the upgrade; stale UI state is revalidated server-side. |
| Upgrade execution | upgrade service through runtime task runner | API task start and dashboard task discovery | Upgrade is an explicit per-session governed task, not a request-bound model call or direct persistence shortcut. |
| Map construction | session-map service and canonical evidence builders | author model and checkpoint commit | Upgrade authors from canonical evidence and creates one honest current V2 checkpoint; it does not fabricate historical maps that never informed subsequent turns. |
| Failure and retry | upgrade task lifecycle and checkpoint transaction | UI reload/retry | Invalid model references, task failure, process restart, or concurrent history change leave V1 state usable and permit a later retry. |

The branch intentionally supports V1-to-V2 only. V2-to-V1 reconstruction and bulk migration are deferred. Hardening must make the one-way contract explicit in current product documentation and ensure duplicate button presses, simultaneous upgrades, and upgrade-versus-chat races are deterministic.

### 6. Session Forking and Lineage

| Aspect | Authoritative owner | Connected modules | Contract to preserve |
| --- | --- | --- | --- |
| Canonical fork mutation | `ChatStore.fork_session()` | API service | A child is a physical copy of the canonical prefix through an existing safe assistant boundary and is independently mutable afterward. |
| Fork-point admission | `api/services/chat_sessions.py` plus stored replacement origins | current chat and evicted-history modal | Only protocol-complete assistant boundaries with an unambiguous canonical sequence are exposed and accepted. |
| Checkpoint inheritance | store checkpoint eligibility | V1 and V2 effective history | A checkpoint crosses only when its full author observation boundary is at or before the fork point; child checkpoints receive new IDs and origin metadata. |
| Tool-event inheritance | store transaction | tool detail UI | Only events associated with copied message tool-call IDs cross into the child; message/event copying is atomic. |
| Logical lineage | session metadata | future cross-session memory | Immediate parent, root session, branch point, child-owned boundary, and inherited count are recorded without yet changing search ranking or deduplication. |
| User confirmation | `static/js/session-controls.js` | all fork entry points | The generic confirmation explains compaction inheritance before any fork mutation; cancellation makes no request. |

Forking is a critical data-integrity boundary. Hardening must cover nested forks, earlier-than-checkpoint forks, latest-message forks, legacy checkpoints without origins, ambiguous provider-message equality, reused tool-call IDs, transaction rollback, vault ownership, and UI navigation from the evicted transcript.

### 7. API and Transport Contracts

| Surface | Primary files | Contract to review |
| --- | --- | --- |
| Session list/detail flags | `api/models.py`, `api/services/chat_sessions.py` | `context_strategy`, `has_session_map`, upgrade availability, checkpoint kind, and canonical fork metadata agree with durable state. |
| Session-map endpoint | `api/endpoints.py`, `api/services/chat_sessions.py` | Revision selection, transcript paging, message/tool projection, and empty/not-found errors are bounded and vault-safe. |
| Upgrade endpoint | endpoints and chat-session service | Starts one governed task and returns durable task identity; it does not imply completion. |
| Manual compaction endpoint | endpoints and chat-session service | Uses core compaction policy and preserves focus without duplicating strategy logic. |
| Fork endpoint | endpoints and chat-session service | Validates access and boundary, then delegates one atomic domain mutation. |
| Execution-task projection | `api/services/execution_tasks.py`, dashboard API | New task kinds are discoverable and update consistently on desktop and mobile. |

`api/services/chat_sessions.py` is roughly 1,500 lines and now owns session maps, transcript projection, forks, summaries, detail rendering, compaction, and upgrades. Structure hardening should test whether map inspection payload construction and fork admission have stable contracts worth extracting, while leaving authorization resolution and HTTP translation in the API layer.

### 8. Frontend and Operator Experience

| Surface | Primary files | State and interaction contract |
| --- | --- | --- |
| Session list actions | `static/js/session-controls.js`, `static/js/icons.js` | Map and upgrade controls appear from server state, work across reloads, and do not compete with title/delete/fork controls. |
| Map modal | `static/js/session-map.js`, `static/app.css` | The current revision opens by default; map and canonical history are both inspectable; revision and transcript paging preserve sensible collapse state. |
| Canonical-history rendering | map controller plus chat/tool renderers | User, assistant, and tool messages keep coherent canonical numbering; tool groups are collapsed and individual calls reuse the existing detail modal. |
| Tool-detail navigation | `static/js/chat-tool-details.js` | Opening a tool from the map and pressing Back restores the same map revision, page, and expanded/collapsed state. |
| Fork controls | `static/js/chat-message-controls.js`, `static/js/session-controls.js`, map modal | Current and evicted assistant messages invoke the same confirmation and backend contract. |
| Compaction notice | `static/js/chat-history-rendering.js` | Generated map JSON is not dumped into chat; a normal-width notice links to the inspectable map. |
| Task discovery | `static/js/dashboard-view.js` | Polling reflects newly created tasks on desktop and mobile without leaking timers after navigation. |

Hardening must inspect loading, empty, error, retry, double-click, stale revision, modal stacking, Back behavior, focus restoration, keyboard access, mobile layout, dark mode, and polling cleanup. Frontend checks should protect controller contracts rather than freeze incidental wording or CSS structure.

### 9. Settings, Models, Providers, and Dependency Boundary

| Aspect | Primary files | Contract to preserve |
| --- | --- | --- |
| Compaction settings | `core/settings/__init__.py`, `config_editor.py`, `settings.template.yaml`, `upgrades.py` | Names, defaults, bounds, old-name upgrades, and cross-setting validation agree; author model falls back to `default_model`. |
| Generative versus decision capability | model configuration, `core/llm/model_factory.py`, `core/llm/decision.py` | Decision-only aliases cannot enter chat or map authoring; generative aliases cannot be assumed to implement the decision protocol. |
| Typesafe/Jev provider | model template, secrets, provider policy, decision runtime | The provider remains optional; absence of its secret cannot impair ordinary chat or either compaction strategy. |
| Pydantic AI upgrade | `pyproject.toml`, `uv.lock`, model factory | The jump from Pydantic AI 2.19 to 2.49 and the `httpx2` transport path must be treated as a branch-wide compatibility surface, not merely a Jev detail. |
| Retry transport parity | `core/llm/model_factory.py` | HTTP and HTTPX2 providers share bounded retry semantics, lifecycle logging, timeouts, and owned-client cleanup. |

Generic decision support is retained intentionally for future classification and decision-tool work, but no Compaction V2 module should import or require it. The live Jev probe and decision tests should be assessed as independent capability coverage, not evidence for session-memory correctness.

### 10. Context Composition and Tool Registration

| Aspect | Primary files | Contract to preserve |
| --- | --- | --- |
| Default context composition | context templates and `validation/scenarios/integration/core/default_context.py` | Retrieval guidance appears in the right layer and only when useful; the session map is part of effective history rather than duplicated into static context. |
| Tool availability | tool registry, `core/tools/session_ops.py`, `core/tools/chat_history_compact.py` | Search, window retrieval, manual compaction, and focus are exposed through existing tool boundaries and governed by captured authority. |
| Tool-output shaping | cache capability, message utilities | Large retrieval results remain bounded and do not undermine the context savings that retrieval and compaction provide. |
| Message grouping/projection | `core/utils/messages.py`, `core/utils/tokens.py` | Role extraction, protocol-complete turn grouping, token estimation, and canonical display agree across persistence, compaction, retrieval, and UI. |

This is the main composability boundary: Compaction V2 must cooperate with the normal context template, tool schemas, active prompt, retrieved cache content, and provider protocol without assuming it controls the model’s whole context budget.

## Durable State and Lifecycle Map

The branch changes or depends on these durable records:

- `chat_sessions`: owner, title, activity, context-strategy and fork metadata.
- `chat_messages`: immutable canonical sequence, provider JSON, projected role/text, and FTS source rows.
- `chat_tool_events`: detailed tool lifecycle associated by session, vault, and tool-call ID.
- `chat_compaction_checkpoints`: append-only V1/V2 replacement projections, observation and consumption boundaries, author/task metadata, and canonical replacement origins.
- `chat_messages_fts`: rebuildable lexical index over canonical transcript text.
- execution-task records/projections: task kind, source, parent, state, and metadata for map authoring and strategy upgrade.
- settings and encrypted secrets: compaction policy, model selection, provider registration, and optional Typesafe credential.

The hardening review must trace each affected lifecycle through success, retry, cancellation, timeout, concurrency, reload, restart, and partial failure:

1. A completed chat turn is persisted, evaluated against compaction policy, and either left unchanged or reduced under the session lock.
2. V2 plans complete-turn eviction, resolves canonical evidence, authors a replacement map through a child execution task, verifies the history revision, and atomically commits a checkpoint.
3. Manual compaction enters the same core operation with a task-owned source and optional salience focus.
4. Upgrade validates a pinned V1 session, authors from canonical evidence, and either commits one V2 checkpoint or leaves V1 fully usable.
5. Transcript search resolves bounded canonical results without changing context; a later window read may become explicit evidence for a future map rewrite.
6. Forking validates a canonical assistant boundary and atomically copies the prefix, safe checkpoints, applicable tool events, and lineage.
7. The map modal reads durable revisions and canonical pages without mutating the session except when the user explicitly forks or starts an upgrade.

## Deterministic Validation Ownership

| Contract area | Existing primary scenarios/tests | Hardening questions |
| --- | --- | --- |
| V1 compaction and repeated recovery cards | `chat_history_compaction.py`, `repeated_chat_history_compaction.py`, `auto_chat_history_compaction.py` | Does V1 behavior remain unchanged except for intentional retained-turn and retrieval guidance changes? |
| Eviction grouping and canonical envelopes | `stepped_eviction_planner.py`, `stepped_eviction_envelopes.py` | Are provider protocol boundaries, tool exchanges, minimum turns, and source ranges exhaustive? |
| Map schema and provenance | `session_map_schema.py`, `session_map_retained_evidence.py` | Are uncertainty, basis, lineage, narrow sources, and retrieved evidence enforced without prompt-text assertions? |
| Author task and checkpoint atomicity | `session_map_authoring_task.py`, `session_map_checkpoint.py`, `session_map_pending_evidence.py` | Do retry, invalid output, cancellation, stale revision, and legacy pending evidence fail safely? |
| Readiness and post-turn routing | `session_map_readiness.py`, `stepped_session_map_post_turn.py` | Are policy, pinning, author capability, missing configuration, and background failure reasons stable? |
| Manual compaction | `manual_session_map_compaction.py` | Do API, tool, focus, pinned strategy, and task ownership converge on one core contract? |
| Transcript storage and tool path | `transcript_retrieval_storage.py`, `session_ops_transcript_retrieval.py` | Are authorization, FTS lifecycle, cursor integrity, token bounds, and canonical provenance covered? |
| Strategy upgrade | `session_context_strategy_upgrade.py` | Are eligibility, idempotency, concurrency, restart/failure, and stale UI cases covered? |
| Fork lineage | `chat_session_fork_lineage.py` | Are nested forks, safe checkpoint cutoff, rollback, legacy origin ambiguity, and tool-event copying covered? |
| Migrations | `system_database_migrations.py`, `system_startup_migrations.py` | Does a realistic pre-branch database reach the final schema, including retired experimental migrations? |
| API/UI contracts | `validation/test_session_map.py`, `test_chat_rendering_modules.py`, `test_app_shell.py`, `test_dashboard_view.py` | Do tests assert stable controller/data contracts rather than incidental DOM or prose? |
| Decision capability | `decision_model_configuration.py`, `validation/test_decision_runtime.py` | Is this independently correct and optional, with no hidden session-memory dependency? |

The live scenarios `compaction_v2_live_conversation.py` and `jev_decision_runtime_probe.py` are opt-in experimental evidence and are not merge gates. `repeated_compaction_live_probe.py` is deleted on the branch and should remain absent. Hardening should decide whether the surviving probes remain useful, are better moved to an external research area, or should be removed before merge.

## Documentation Ownership and Gaps

Current product documentation covers the `session_ops` and manual compaction tool surfaces, and ADR 0048 records canonical fork lineage. The architecture overview mentions fork storage but does not yet describe the V1/V2 effective-history model, checkpoint invariants, transcript retrieval boundary, strategy pinning, or upgrade lifecycle. Compaction V2 therefore needs a current-contract architecture record or ADR before merge.

The root-level experiment and implementation documents preserve valuable branch reasoning, but they are not all appropriate permanent product documentation. During cleanup, retain or move durable research findings to the external AssistantMD design library, convert accepted decisions into current-contract architecture/ADR text, and remove obsolete implementation plans from the merge diff when they no longer serve repository users. Migration-specific behavior belongs only in the explicitly migration-focused plan or operator guidance.

## Cross-Cutting Hardening Risks

The initial inspection identifies these review priorities; they are not yet claims that a defect exists:

1. **Critical boundary: canonical versus effective history.** Every map, retrieval, upgrade, inspection, and fork path must use the correct projection explicitly; a single mode error can lose evidence or leak future state into a fork.
2. **Critical boundary: atomic checkpoint and fork mutations.** Multi-table mutations must roll back completely under malformed model output, duplicate identifiers, interrupted copies, and stale revisions.
3. **High boundary: task-governed inference.** Map authoring and upgrade inference must always flow through the normal task runner with authority, parentage, cancellation, activity, and failure state intact.
4. **High boundary: strategy consistency.** Automatic, suggested, manual, upgrade, reload, and fork paths must not disagree about whether a session is V1, V2, or unassigned.
5. **High boundary: provenance and observation cutoff.** Consumed, observed, retained, retrieved, and canonical source boundaries have distinct meanings; naming and validation must prevent them from drifting together.
6. **High boundary: dependency upgrade blast radius.** Pydantic AI and HTTP transport changes affect every model provider and require broader validation than the memory feature alone.
7. **Medium boundary: oversized integration owners.** The largest touched modules can hide duplicated validation and payload construction; refactor only where a stable policy boundary emerges.
8. **Medium boundary: legacy compatibility.** Old settings names, legacy checkpoint rows, experimental schema migrations, and ambiguous origin reconstruction need deliberate keep/remove decisions with tests.
9. **Medium boundary: frontend async state.** Revision paging, transcript paging, nested modals, polling, and forks can race against reloads or session changes.
10. **Low-to-medium boundary: branch residue.** Experimental docs, probes, retired terminology, prompt-version history, and the already-shipped Vault Explorer hotfix should not obscure the final feature diff.

## Hardening Sequence

### Stage 1 — Scope and Contract Map

- Use this document as the branch inventory and contract map.
- Verify the map against the current diff after any merge from `main`.
- Record discovered findings with severity, concrete evidence, consequence, and smallest correction.

Exit evidence: every changed production module, durable record, API/tool boundary, automation path, and user-visible surface belongs to an owner above.

### Stage 2 — Structure and Ownership

- Audit retired Jev/live-map symbols, temporary compatibility shims, experiment-only hooks, unused exports, and duplicated policy.
- Review the five large integration modules—`compaction.py`, `chat_store.py`, `task_execution.py`, `api/services/chat_sessions.py`, and `api/endpoints.py`—for mixed ownership and bypass paths.
- Centralize strategy resolution, canonical-boundary validation, settings normalization, provenance checks, and API payload construction where copies could drift.
- Run the production Python quality gate and focused static/frontend checks after each structural batch.

Exit evidence: no unexplained dead runtime path, parallel policy implementation, broad type escape, or production quality finding remains.

#### Initial Stage 2 Findings

The initial structural audits found three High-severity correctness risks and six Medium-severity ownership or contract inconsistencies. They are recorded here as the baseline for the correction batches; each requires a focused deterministic regression check before closure.

- **High — V1 stale commit:** Recovery-card compaction can commit an author result after its source history has changed, because the commit path does not enforce the history-revision precondition used by V2. The affected flow is `core/chat/compaction.py` and `ChatStore.add_compaction_checkpoint()`; retain the captured source revision through authoring and make checkpoint insertion reject stale history atomically.
- **High — Malformed checkpoint replacement fails open:** `ChatStore._checkpoint_replacement_messages()` logs malformed replacement history and returns an empty replacement (`core/chat/chat_store.py`). Effective history then continues from after the checkpoint boundary, silently omitting its recovery card or map. Treat an invalid effective checkpoint as an explicit integrity failure or use a deliberate canonical-history recovery path; do not silently return an empty replacement.
- **High — Malformed raw row is skipped during fork:** The raw-row decoder omits an undecodable canonical message rather than rejecting the fork (`core/chat/chat_store.py`). The child can therefore be committed without the exact prefix the user selected. Validate the complete source prefix before inserting the child and fail the transaction on any malformed row.
- **Medium — Runtime-absent automatic-compaction bypass:** `maybe_auto_compact_after_turn()` invokes compaction directly when no runtime context exists (`core/chat/compaction.py`), bypassing the governed task path used when runtime is available. Make task ownership consistent across supported execution environments or constrain and document the runtime-free path explicitly.
- **Medium — Shared author validation differs by strategy:** Recovery-card and session-map authoring do not apply one shared model-readiness contract (`core/chat/compaction.py`, `core/memory/session_map/readiness.py`). Centralize the common author-model checks while preserving strategy-specific requirements.
- **Medium — Manual no-op behavior drifts:** Manual compaction entry points do not consistently represent the no-safe-reduction case (`core/chat/compaction.py`, API/tool adapters). Define one no-op result contract and have all adapters preserve it.
- **Medium — Task envelopes are duplicated:** Compaction paths assemble overlapping task metadata and lifecycle envelopes at more than one boundary (`core/chat/compaction.py`, `core/memory/session_map/service.py`, and task adapters). Keep one authoritative task envelope builder so task identity, source, parentage, and result projection cannot drift.
- **Medium — Checkpoint observation cutoff validation is permissive:** Fork checkpoint eligibility derives its cutoff from loosely typed checkpoint metadata (`core/chat/chat_store.py`). Reject malformed or out-of-range observation boundaries instead of allowing a fallback that can misclassify checkpoint safety.
- **Medium — Fork boundary policy is split across layers:** The API service validates the assistant fork point while `ChatStore.fork_session()` owns the canonical mutation (`api/services/chat_sessions.py`, `core/chat/chat_store.py`). Put the invariant at the core mutation boundary and keep transport validation as early feedback.

Low-severity findings and retained residue:

- **Low — Retired-settings test coverage is partial:** The cleanup scenario seeds the former live-memory mode and three gate keys, while `RETIRED_SETTINGS` includes a larger retired-key set (`validation/scenarios/integration/core/decision_model_configuration.py`, `core/settings/upgrades.py`). Extend the scenario to cover every retired setting.
- **Low — Retired-map teardown intentionally drops derived state:** Migration 5 removes the old live-map tables (`core/chat/schema.py`); existing migration tests use empty placeholder tables (`validation/scenarios/integration/core/system_database_migrations.py`). Startup requests a database backup, and canonical messages remain intact, but pre-existing live-map rows are no longer available in the application. Seed representative rows and verify the backup recovery path, while keeping the historical migration chain intact.
- **Low — Fork plan wording is stale:** `SESSION_FORK_LINEAGE_IMPLEMENTATION_PLAN.md` labels the fixed bug “Current Defect” and retains a “Next Step” checklist despite completed implementation and deterministic validation. Mark the defect as historical and reconcile the remaining manual-verification status.
- No active production Jev map-gate dispatch, task kind, runtime setting, decision checkpoint state, or UI/API field remains. Gate references are limited to retired-setting cleanup and tests asserting absence.
- Generic decision-model support, the Jev provider/secret configuration, and the opt-in live decision probe remain intentionally available as provider-neutral platform capability; Compaction V2 does not depend on them.
- Historical migrations that create and later remove live-map tables, plus settings cleanup for retired keys, remain necessary for databases and settings files created by earlier branch versions. Do not delete or renumber those compatibility steps.
- The Compaction V2 live-conversation experiment remains an opt-in validation probe; completed Jev gate/reranking experiments are documented as closed and have no active production hooks.

The production Python static quality baseline is clean. This does not close the behavioral findings above; their corrections and focused regression checks remain part of Stages 2 and 3.

#### Stage 2 Correction Status

The first correction batch is implemented and covered by focused deterministic checks:

- V1 recovery-card authoring now captures and enforces the source history revision, so a concurrent append or deletion cannot commit stale replacement history.
- Invalid checkpoint replacement payloads and malformed canonical message rows now raise an explicit chat-history integrity error instead of silently dropping context or producing a gapped fork.
- Fork-point safety is enforced at the core mutation boundary and reused by the API projection, including complete tool-call cycles and ambiguous legacy origins.
- Checkpoint observation metadata is parsed through one fail-closed contract, new evidence ranges must be contiguous, and explicit observation boundaries cannot fall below consumed history or exceed canonical history.
- Manual and automatic compaction now use one task-owned orchestration operation. Automatic compaction runs as a child of the active chat task and no longer has a runtime-free inference bypass.
- The current-chat fork renderer accepts only an explicit canonical fork origin; it no longer falls back to a display-only sequence index.
- Retired settings and destructive compatibility migrations now have complete cleanup and backup-path coverage, and the fork implementation plan reflects current rather than historical status.
- Dashboard task polling now remains active while the dashboard is visible, ignores out-of-order responses, and preserves visibly labelled last-known state across a temporary refresh failure.

The remaining shared-author-readiness and manual no-op observations are lower-risk consistency reviews rather than demonstrated data-integrity failures. They remain open for the next structural pass.

#### Initial Stage 3 Retrieval Findings

The retrieval audit identified four concrete boundary defects and one internationalization gap:

- The token limit was applied to an internal compact payload, while `session_ops` added guidance and pretty-printed the final result. The actual model-facing tool output could therefore exceed its requested budget.
- A bounded transcript fragment admitted as map evidence was reduced to a sequence index and then expanded back to the complete canonical message. This could undo retrieval bounds and admit unrelated content from a long source message.
- Copied transcript-window results in a fork identify the parent session, so otherwise valid evidence inherited within the safe fork prefix is not currently admissible in the child.
- SQLite operational failures are translated into correctable tool-input errors, obscuring infrastructure failures as model retries.
- FTS query normalization was ASCII-only despite the canonical SQLite index using Unicode tokenization. Accented Latin and non-Latin queries could be mangled or rejected.

The retrieval correction batch is complete. Unicode-aware NFC query normalization now matches the index's Unicode behavior. The exact model-facing serializer defines transcript-window budgeting, including wrapper guidance and the reported estimate. Map admission verifies selected fragments and offsets against only their canonical rows, caps aggregate retrieved evidence explicitly, and accepts fork-ancestor windows only through the narrowest inherited prefix. SQLite infrastructure failures retain their operational identity instead of being represented as correctable model-input errors.

#### Initial Stage 5 Frontend Findings

The first frontend correction batch closes four demonstrated async or accessibility gaps:

- Dashboard polling remains active while the dashboard is visible, ignores out-of-order responses, and preserves visibly labelled last-known task state on a temporary request failure.
- A completed V1-to-V2 upgrade remains a reported success if the subsequent session-list or active-chat refresh fails; refresh failure is presented as a separate warning with reload guidance.
- Session-map and tool-detail dialogs receive focus on open, close on Escape, and restore a still-connected invoking control.
- Returning from canonical-history tool detail restores the same map checkpoint, transcript page, and disclosure state, then focuses the restored map dialog.

The remaining frontend work is browser-level manual verification of 320/375-pixel layout and dark-mode rendering. Automated controller coverage should continue to protect behavior without freezing incidental markup.

#### Stage 5 Verification Status

The focused frontend suite passes 21/21 and covers map-modal focus and Escape behavior, tool-detail Back restoration, stale revision and transcript-page responses, shared fork confirmation cancellation, dashboard polling cleanup, and stale task-response handling. A real-browser pass through Obscura 0.2.3 and Chrome CDP against an isolated FastAPI fixture verified revision switching, transcript paging across 110 canonical entries, disclosure-state preservation, nested tool-detail inspection and Back restoration, evicted-message fork cancellation and acceptance, inherited canonical messages and map checkpoint state in the accepted child, and desktop task appearance and removal without a page refresh. Obscura does not toggle native `details` elements even on a blank page, so the paging and nested-modal checks opened those elements through the DOM before exercising the application controls. Intermittent Obscura bootstrap stalls prevented a reliable 320/375-pixel and dark-mode pass; those rendering checks remain manual, with no concrete application defect found.

#### Initial Stage 4 Activity Findings

The activity audit found that successful operation records are generally content-safe and use counts, ranges, and identifiers rather than prompts, queries, transcripts, map output, or summaries. The meaningful gaps are lifecycle consistency and failure correlation:

- Warning deduplication keys use logger tag, message, and `issue`, but map-authoring, upgrade, and compaction failure warnings omit `issue`. Failures from distinct sessions can therefore collapse into one visible warning.
- Forking emits a useful completion record but no start/failure lifecycle, and the completion event lacks an explicit status.
- Unexpected `session_ops` failures log the unresolved request argument rather than the resolved active session and omit vault, stable event/status, and issue identity.
- Long upgrades emit session-map author start/completion activity for every historical pass in addition to aggregate upgrade events, creating user-facing noise proportional to transcript age.
- Domain events inconsistently include explicit status and outer task correlation, while safe no-op map reductions have no stable skipped event.
- Failure events in the reviewed operation families include unbounded `str(exc)`. Provider or SDK messages may be large or contain request/response detail; Activity should retain a bounded safe summary, error type, stable reason, and correlation identity.

The first logging correction batch will address fork lifecycle and resolved transcript-retrieval failure correlation. Failure deduplication, bounded error projection, per-pass authoring noise, and consistent task/status fields should then be corrected together so operation-family contracts remain coherent.

#### Stage 3 Lifecycle Correction Status

The second lifecycle batch establishes these additional contracts:

- Both compaction strategies use one author-readiness decision for text capability, thinking validity, and credential availability.
- Explicit V1 and V2 requests with no safely reducible older history return the same completed `unavailable / retained_turn_floor` result without inference, checkpoint writes, or history-revision changes.
- Concurrent V1-to-V2 upgrade starts for one owned session reuse one active execution task rather than enqueueing redundant inference.
- An upgrade queued behind another session task revalidates its recovery checkpoint before inference; stale queued work fails without author calls.
- Cross-store append or deletion during upgrade authoring aborts before another authoring pass and leaves no partial V2 state.
- Cancellation, runtime shutdown, restart, and retry leave V1 usable, release process-local operation/lock state, and allow one later successful upgrade.
- If manual compaction selected V1 before waiting behind an upgrade, it detects the newly pinned V2 checkpoint, re-resolves once after releasing the V1 lock, and continues under V2 inside the same governed task.

#### Stage 4 Logging Correction Status

Fork operations now emit correlated start/completed/failed records with explicit status, canonical boundary, source/child identity where applicable, bounded generic failure text, error type, and unique issue identity. Unexpected `session_ops` failures now use resolved active vault/session identity, stable event/status/error type/issue fields, and exclude raw query/result data. Deterministic Activity assertions cover both contracts.

Map authoring, context-strategy upgrade, and both compaction strategies now emit explicit lifecycle status and outer task, parent, source, session, and vault correlation. Warning issue identities include the operation task so distinct failures survive Activity deduplication. Domain failures project fixed reason categories and bounded diagnostics rather than exception text, while validation and Logfire retain the detail needed to diagnose individual authoring passes.

Historical authoring passes during an upgrade no longer emit successful per-pass start/completed records to user-facing Activity. Ordinary authoring retains those records, reconstruction failures remain visible, and one successful upgrade emits a constant three upgrade-domain Activity records regardless of pass count. The generic task spine still emits child lifecycle rows for every pass; changing that requires a task-runner-wide quiet-child contract and is not part of this domain correction.

#### Initial Stage 6 Documentation Findings

The product-facing tool documentation, compaction settings descriptions, and ADRs 0003, 0017, and 0048 remain consistent with the current contracts. The material documentation gap is `docs/development/architecture.md`: it should describe both compaction strategies, canonical versus effective history, per-session strategy pinning, author-model fallback, watermark and retained-turn behavior, transcript retrieval, and explicit V1-to-V2 upgrade without implying that every session has a map or that V2 is the default.

The root implementation plans are working records rather than durable product documentation. Before merge, reconcile any remaining manual-verification status, preserve useful research in the external design library, and remove or archive completed implementation plans so historical experiments are not mistaken for current contracts.

Generic execution-task failure records also currently project raw exception strings. That is a cross-cutting Activity concern affecting every task family, not a session-memory-only correction. Keep the session-memory domain events content-safe in this branch and evaluate generic task failure redaction as one coordinated task-runner contract change rather than modifying it incidentally in individual operation families.

#### Stage 6 Documentation Correction Status

The architecture overview now describes canonical versus effective chat history, recovery-card and session-map strategies, watermark and retained-turn behavior, per-session strategy pinning, shared author-model fallback, canonical transcript retrieval, and explicit per-session V1-to-V2 upgrade. ADR 0049 records checkpoint-derived effective history, append-only reduction boundaries, strategy pinning, governed authoring, and explicit upgrade policy. ADR 0050 records the foundational Compaction V2 representation: bounded sparse whole-map replacement, conservative typed state, narrative trajectory, canonical provenance, complete revision, and transcript-backed inspectability. Product-facing tool documentation remains the authority for model-visible `chat_history_compact` and `session_ops` operation details.

#### Stage 6 Dependency and Residue Findings

Pydantic AI 2.49 is required for the retained TypeSafe decision-model integration, and the exact pin currently passes the decision runtime and provider-error scenarios. The dependency refresh also advances the provider SDKs and FastAPI/Starlette, so the complete deterministic profile remains required before merge. The branch imports `httpx2` directly for provider transports; it is now declared directly rather than relied on as a transitive dependency.

Pydantic AI 2.49 warns that its legacy httpx retry transport and legacy OpenAI-compatible clients will be removed in v3. The exact v2 pin makes this forward-compatibility work rather than a current merge blocker. Migrate the remaining Google, Mistral, OpenAI, OpenRouter, and local-provider client construction together in a focused provider-transport change so OAuth and retry semantics are not altered piecemeal.

No tracked benchmark dataset, generated validation output, debug code, or active Jev compaction gate remains. Retired settings and their validation are deliberate upgrade compatibility. The optional Jev runtime probe exercises the retained generic decision capability rather than session memory; keep it only as independent live-provider coverage. Completed root implementation and experiment plans should be archived externally or removed before merge after their remaining manual-verification notes are reconciled.

#### Validation Evidence

- The combined directly affected profile passed 14/14 integration scenarios in `20261002_184700_439367`, and the separately included map-authoring lifecycle scenario passed in `20261002_184911_095396`.
- The affected frontend controller suite passed 18/18 tests after the final changes.
- The production quality gate passes: Ruff clean, Black clean across 499 files, and MyPy clean across 288 production source files.
- The complete deterministic pre-merge profile passed 130/130 scenarios in `20261002_190522_519299`.
- The first two full-profile attempts exposed a stable validation isolation defect in `mcp_oauth_coordinator`: it assumed its bootstrap service root was also the process-wide Activity destination. The scenario now resolves the logger's actual runtime-owned root; the exact preceding MCP scenario sequence and the full profile both pass.

Remaining browser-only verification is limited to the explicitly recorded responsive and dark-mode rendering checks. Remaining cross-cutting follow-ups are generic execution-task exception projection and the Pydantic AI v3 provider-transport migration; neither is a demonstrated Compaction V2 correctness failure under the pinned dependencies.

#### Post-Validation Structural Review

The final structural review found no critical or high-severity ownership defect, but it identified several medium-severity drift and scaling risks that should be addressed before promotion:

- `core/chat/compaction.py` has become an integration god module. It owns V1 authoring, task-adjacent strategy dispatch, automatic policy, V2 planning, evidence projection, transcript-window verification, checkpoint orchestration, and lifecycle logging. The clearest extraction seam is V2 retained and retrieved evidence projection; orchestration and locking should remain in the chat layer.
- Ordinary V2 reduction and V1-to-V2 upgrade apply different retained-evidence rules. Ordinary reduction excludes `session_ops` retrieval results and separately admits verified bounded fragments, while upgrade currently supplies every retained raw message as citable evidence. Both paths need one shared canonical projector with explicit per-part retrieval handling so upgrade cannot bypass the normal trust and budget policy and mixed tool-return batches do not lose unrelated evidence.
- Strategy status, upgrade admission, and V2 readiness perform overlapping but different configuration checks. One shared strategy and author-readiness result should drive UI eligibility and execution admission while upgrade-specific checkpoint and ownership checks remain separate.
- Session-map reduction emits start, plan, and completion records but lacks its own failure and cancellation envelope. Manual failures therefore fall back to generic task logging while automatic reduction adds a separate deferred event. The V2 operation should own a symmetrical, content-safe terminal lifecycle.
- `core/tools/session_ops.py` is a genuine god module: one tool closure dispatches transcript retrieval, cross-session discovery, summary CRUD, generative summarization, embeddings, artifacts, validation, and error translation. Keep one public tool contract, but move operations behind cohesive internal handlers or services rather than growing the conditional further.
- `api/services/chat_sessions.py` assembles `ChatSessionInfo` separately for listing and fork responses. Extract one projector so workspace, summary, map, strategy, and upgrade fields cannot drift. The module's map/transcript projection is the strongest later extraction candidate.
- Canonical transcript paging currently bounds response size but not backend work: the map endpoint loads and projects the complete canonical prefix before slicing one page. Page from canonical storage or incrementally group only enough rows for the requested page.
- Session-map revision/page loads and ordinary session/vault loads lack request freshness guards. Older responses can overwrite newer selections, report stale errors, or clear the busy state for a newer load. Use request generations or abort controllers and guard success, error, and finalization mutations.

Lower-priority cleanup includes removing the `updateTitleRow()` alias and duplicate renders, collapsing the system-status forwarding chain, removing the unused `vault_path` deletion parameter, and deciding whether compatibility-only aliases remain public. `ChatStore` is large but remains a coherent persistence and transaction boundary; do not split its atomic fork operation merely to reduce line count. Task, API, tool, and authoring wrappers generally enforce real authority, lifecycle, serialization, or prompt-contract boundaries and should remain.

The preferred implementation order is: shared evidence projection, shared upgrade/readiness policy, V2 terminal lifecycle, frontend request freshness, shared API session projection, and backend-bounded transcript paging. Extract the evidence seam from `compaction.py` as part of those corrections. Defer a broader `session_ops` decomposition to a focused refactor unless the corrective work yields an obviously stable internal boundary.

The corrective batch is complete. Ordinary V2 and upgrades now share one retained/retrieved evidence projector with part-level filtering and canonical transcript-window verification; upgrade status and admission share the Compaction V2 author/configuration readiness contract; V2 reduction owns explicit skipped, failed, and cancelled lifecycle events; session-map, vault-list, and session-load requests reject stale success, error, and finalization callbacks; list and fork responses share one `ChatSessionInfo` projector; and transcript pages hydrate only the selected display rows and their tool events. Exact transcript `total_entries` still scans compact SQLite message metadata because adjacent provider tool traffic is grouped into one display row, but complete message JSON and tool-event payloads no longer scale with the full checkpoint prefix. Accidental low-level compaction package exports, one duplicate title render, and the unused session-delete vault-path argument were also removed. The broader `session_ops` decomposition remains deferred to a focused refactor because this batch did not expose a sufficiently narrow seam that justified expanding the change.

Post-correction validation passed the full 130-scenario deterministic `integration/core` profile in report `validation/runs/reports/20261002_194640_720440.md`, the nine focused frontend controller tests, affected session-map/upgrade/fork scenarios, and scoped Ruff, Black, MyPy, JavaScript syntax, and diff checks.

#### Iterative Peer-Review Hardening

The branch received repeated fresh-agent review after the initial correction batches rather than relying on one broad audit. Dedicated passes covered retired Jev residue, canonical/effective persistence and fork lineage, governed task failure handling, map evidence admission and retrieval, strategy/readiness parity, tool-protocol integrity, and manual API/Activity projection. Reviewers reproduced findings against isolated runtime roots, corrections received focused regressions, and the affected boundary was then reviewed again by a fresh agent until it returned mostly clean.

- The retired Jev map gate, runtime settings, task kinds, persistence, API fields, and UI controls are absent. Compaction V2 has no decision-runtime import or Jev dependency. Generic provider-neutral decision infrastructure, Jev model/secret configuration, deterministic decision tests, and one opt-in live provider probe remain intentionally as an independent platform capability.
- Fork creation now reserves one SQLite writer snapshot before source reads, validates every inherited checkpoint replacement before creating the child, shares ordered tool-event eligibility with API projections, and fails closed on malformed explicit replacement origins. Canonical-origin replacement messages retain exact persistence identity while synthetic map/card messages and model replay follow their own configured normalization policies.
- Full-history, paged-transcript, and integrity-check consumers now share one compact tool-protocol state machine. It handles exact opaque IDs, call/reply tool-name agreement, missing IDs, ordinary and native parts, named retry replies, adjacency, duplicate invocations, earlier safe prefixes, and pending calls without hydrating the full canonical transcript for paging.
- Map evidence envelopes now separate contiguous consumed intervals from citable ranges. Ordinary and native `session_ops` returns are removed from author-visible evidence, verified source windows remain available from evicted, retained, pending-repair, and upgrade paths, legacy retrieval-containing citations fail closed, and checkpoint admission metadata preserves safely authored mixed-message citations across later revisions and forks.
- Manual status, upgrade eligibility, upgrade admission, and execution share reduction prerequisites and forced-watermark policy. Exact high-watermark equality is eligible, impossible upgrades reject before task creation, author readiness is reflected in manual availability, and full boundary selection remains outside session-list projection. Malformed tool histories and valid model-retry histories are distinguished before inference.
- Automatic compaction suppresses only failures already owned by an admitted failed compaction task. Pre-admission failures reach one safe parent fallback warning, completed chat turns survive background reduction failures, and task/API/Activity read surfaces redact content-bearing failure text for the three compaction-related task kinds.
- Manual compaction translates unexpected V1/V2 operation failures at the service adapter into a fixed `ChatHistoryCompactionFailed` API contract. Provider/checkpoint sentinels and tracebacks remain absent from debug and non-debug responses, retained Activity query/export, and task list/detail while domain and task correlation remains available.

Fresh post-correction reviews reported no actionable finding for map evidence admission, strategy/readiness and tool-history handling, or manual compaction API projection. The final fork review reported one deferred P3 corruption-projection mismatch: the metadata-only paged scanner can advertise a later fork point across structurally invalid but syntactically valid non-tool message JSON, while the core fork mutation rehydrates the prefix and rejects it with `ChatHistoryCorruptionError`. Detecting that corruption during projection would require full-prefix model hydration or duplicated Pydantic schema logic, so mutation remains the fail-closed authority. Tool-event rows also still lack durable invocation sequence identity; legacy history missing its original diagnostics can leave a later same-ID event pair indistinguishable. Both limitations are bounded to inspection details and do not permit a partial child mutation.

Focused validation reports for this iterative pass include `20261002_232001_447346`, `20261002_234223_336773`, `20261002_234454_480425`, `20261003_000323_972726`, `20261003_000650_476937`, `20261003_001516_658904`, and `20261003_001558_104924`. The consolidated tree passed all 131 scenarios in the complete deterministic `integration/core` profile in report `validation/runs/reports/20261003_001948_764981.md`, along with the complete Ruff, Black, MyPy, and diff-quality gates.

### Stage 3 — State, Lifecycle, and Failure Paths

- Trace and harden automatic/manual V1 and V2 compaction, map retries, upgrades, retrieval, and forks through the lifecycle matrix above.
- Add deterministic scenarios for material gaps before correcting them.
- Exercise stale revision, duplicate action, cancellation, timeout, process restart, malformed persisted state, and transaction rollback where relevant.

Exit evidence: focused scenarios cover each consequential failure boundary and leave no stuck task, lock, misleading checkpoint, or partial mutation.

### Stage 4 — Activity Logging and Observability

- Inventory actual start/decision/completion/skip/failure records for compaction, map authoring, upgrade, retrieval, and fork operations.
- Check stable event/status fields, task/session/vault correlation, warning issue identity, redaction, and System Activity noise.
- Add deterministic assertions for operation-family logging where missing.

Exit evidence: an operator can locate a failed or skipped operation from user-known session/task identity without inspecting prompts or sensitive content.

#### Stage 4 Results

Repeated fresh-agent review traced retained System Activity across recovery-card and session-map compaction, map authoring, strategy upgrades, forks, transcript retrieval failures, and the shared execution-task spine. Domain lifecycles now publish one correlated terminal outcome for cancellation, no-op, completion, readiness rejection, and failure; V2 failures retain safe stage-specific reasons; expected fork rejections retain their semantic error type; upgrade admission failures remain searchable before task creation; and `session_ops` failures carry run/tool-call correlation while allowlisting the model-supplied operation name. Prompt text, transcript content, query text, tool arguments, provider exception content, and model output remain excluded.

The audit also removed two misleading post-mutation failure edges. Effective-history projection and token estimation now complete before a V2 checkpoint write, and checkpoint materialization is verified inside the same SQLite transaction rather than through a fallible read after commit. Planner no-op reasons survive into the API and Activity result, successful V2 completion identifies its strategy, and retained-Activity regressions cover cancellation, tool-history warnings, readiness failures, semantic fork rejection, and untrusted operation-name redaction. The consolidated focused profile passed 7/7 scenarios in report `validation/runs/reports/20261003_064542_467802.md`, and the settled tree passed all 131 scenarios in the full deterministic profile in report `validation/runs/reports/20261003_064741_359881.md`.

#### Focused `session_ops` Hardening

The broader legacy `session_ops` surface received a separate operation-by-operation audit after the Compaction V2 boundaries were already stable. The review covered session listing, summary reads and writes, generative summarization, cross-session search, transcript search and windows, vector refresh, artifact propagation, failure envelopes, and execution-task governance without attempting the deferred module decomposition.

The correction establishes authority mediation for every model-selected session target and for session listing, conceals inaccessible summaries, rejects orphan summary writes, and keeps the contextual active session distinct from an optional selected session when resolving `workspace: current` or workspace boosts. Workspace filtering now uses one summary-first, canonical-session fallback across list, lexical/vector summary search, deep transcript search, and ranking. Cross-session result counts and payload projections are bounded; full metadata and artifact arrays remain available only through the explicit summary read operation.

Generative summarization now captures and verifies one source history revision before and after inference so concurrent appends cannot be stamped as current. Its first pass projects only system checkpoint text and explicit user/assistant prose, excludes raw tool calls and returns, and applies both a global ceiling and the selected model's declared context window with response headroom before inference. The same model-aware bound protects source extraction. All prompts label retained evidence as untrusted and cap stored titles. Source extraction uses only a small allowlist of source-bearing tools and bounded locator fields; shell arguments, write bodies, arbitrary tool arguments, raw result bodies, URL credentials/query strings/fragments, and unsafe artifact references are not forwarded to the selectable summary provider. Explicit artifact payloads are fully validated before summary mutation, and a later artifact-store failure is reported as partial success rather than a false total failure. Generated chat titles and mutation-derived artifacts remain explicit best-effort auxiliary propagation with content-safe warnings.

Session-summary vector replacement is atomic in SQLite, including clear-all updates, so a failed replacement preserves the exact prior index and an empty replacement cannot leave stale semantic hits. All production summary writers and deletes share one per-session mutation gate across the relational row, FTS, artifacts, and vector work. Cancellation restores the prior row, nullable title, and FTS state before propagating; metadata inheritance occurs under that gate; and deletion initializes an absent vector table when necessary before removing every summary representation in one SQLite transaction. Full-session deletion removes derived summary state before canonical chat, so a cleanup failure leaves the canonical transcript intact and retryable. Transcript retrieval removes only prior `session_ops` return parts from mixed messages, preserves unrelated tool evidence and provenance, rejects retrieval-only matches, and strips recursive retrieval output from transcript windows and continuations. Search queries, candidates, excerpts, detail projections, metadata, artifact lists, workspace paths, and deep-search titles are bounded. Persisted tool failures and System Activity retain stable classifications and contextual correlation identifiers without raw exception, query, requested session id, operation, prompt, or provider content.

The remaining retrieval bound is explicit: the raw FTS candidate scan ranges from 100 rows for the default five-result request to an absolute maximum of 4,000 rows. Pure retrieval echoes are excluded before that bound, but enough higher-ranked mixed messages containing both legitimate content and prior `session_ops` output can still conceal a later match. Eliminating that residual requires a retrieval-safe derived FTS projection and migration rather than unbounded overfetch, so it remains deferred.

The summarization calls remain inside the caller's governed CHAT or WORKFLOW task and propagate cancellation; separate child tasks for the internal model passes are deferred observability/control work rather than an authority bypass. `list_sessions` still uses best-effort mutable offset pagination, so concurrent activity can duplicate or omit rows across pages; replacing it with a request-bound keyset or snapshot cursor remains a later cross-session browsing improvement.

Focused correction evidence includes `integration/core/session_ops_transcript_retrieval`, `integration/core/session_ops_chat_tool`, `integration/core/session_summary_vector_index_atomicity`, `integration/core/transcript_retrieval_storage`, `integration/core/chat_history_compaction`, and `integration/core/api_endpoints`. The settled focused batch passed 6/6 in `validation/runs/reports/20261003_081415_971569.md`; earlier read-side regressions passed in reports `20261003_080719_191715` and `20261003_080748_456632`, and mutation, cancellation, and deletion regressions passed 3/3 in `20261003_075942_261055`. A legacy workflow scenario was updated to assert the hardened redacted failure contract rather than raw provider error retention, then passed independently in `validation/runs/reports/20261003_082303_212638.md`. The final settled tree passed all 132 scenarios in the complete deterministic profile in `validation/runs/reports/20261003_082331_763036.md`; the complete Ruff, Black, MyPy, and diff-quality gates were also clean.

#### Live-Build Session Loading and Reference Regressions

Live-build testing exposed three read-path regressions after the first settled hardening profile. Session listing evaluated exact V1-to-V2 eligibility by hydrating and token-counting every recovery-card transcript, while the async API endpoint ran that synchronous work on the event loop. Legacy checkpoints without explicit replacement origins also hydrated the complete evicted canonical prefix during ordinary session loading. Together these costs delayed the session list and could postpone the active-task lookup that begins detached-stream reconnection even though the compact replay and SSE handoff remained intact. Rendered `@path` parsing had a separate greedy-directory pattern that could merge two adjacent references and their joining prose into one missing path.

The corrected list projection derives forced-upgrade eligibility from a compact SQLite history shape: message count, conversational-group count, and custom-tool protocol identities. Actual upgrade admission still runs the complete canonical planner and validation. The session list and session detail services now run through the API thread pool so SQLite and legacy projection work cannot stall task event delivery. Legacy origin recovery selects only canonical rows whose serialized message JSON could match a retained replacement and preserves the existing unique, ordered, ambiguity-safe origin rule without deserializing the evicted prefix. Session-map presence uses the already-derived pinned strategy rather than loading every map checkpoint. The path parser now treats a subsequent `@` as a new reference boundary.

Disposable synthetic profiling reduced warm eligibility projection from about 10 ms to 3 ms for 20 messages, 70 ms to 6 ms for 200 messages, and 680 ms to 40 ms for 2,000 messages; 18 structural and tool-history cases produced no eligibility mismatch. Legacy origin recovery fell from about 57 ms to 3 ms for 1,000 messages and from 844 ms to 24 ms for 10,000 messages, while 4,800 exhaustive canonical/replacement combinations matched the prior semantics. The consolidated API, persistence, upgrade, and fork profile passed 4/4 in `validation/runs/reports/20261003_201815_512362.md`, the file-reference plus chat-rendering module suite passed 7/7 with the reported two-link sentence as an exact regression case, and the complete deterministic profile passed 132/132 in `validation/runs/reports/20261003_202004_073150.md`. A fully JSON-valid but schema-invalid canonical message is no longer Pydantic-decoded merely to render the session list; canonical history loading and any actual upgrade operation remain the fail-closed validation boundaries.

#### Tool-Heavy Turn Rendering and Terminal Fork Handoff

Live-build testing found that persisted chat rendering treated each provider-level model response in a tool-heavy turn as a separate visible assistant message. Intermediate tool-call responses containing text or retained thinking therefore became partial or reasoning-only bubbles before the terminal answer; the same structure could appear visually empty when reasoning was collapsed. Persisted rendering now accumulates intermediate assistant text, thinking, and tool identities through the complete conversational turn and renders one terminal assistant bubble with the canonical fork origin and all tool summaries attached.

The live streaming bubble also lacked a fork action because its transient render context had no canonical sequence index. The backend now returns the final assistant origin in the terminal `done` event only after the canonical commit and the existing protocol-completeness check succeed. The frontend applies that explicit origin before finalization. It does not fall back to an effective-history display index, so incomplete tool cycles and ambiguous checkpoint replacements remain non-forkable. Fork-origin projection runs inside the canonical commit transaction so a projection failure cannot leave a committed response paired with a failed task. The focused event-stream scenario passed 1/1 in `validation/runs/reports/20261003_211259_232389.md`, including safe-terminal, executable fork, unresolved-tool, and atomic projection-failure assertions. The six chat-rendering module checks cover completed-turn grouping, incomplete tool-only tails, explicit persisted origins, and terminal live-origin hydration. The complete deterministic profile passed 132/132 against the final corrected tree in `validation/runs/reports/20261003_211336_041098.md`.

#### Split Parallel Tool-Return Admission

Live telemetry exposed a persisted fork and compaction admission failure after one valid parallel tool batch. The assistant issued one `file_read` and three `file_write` calls in a single response; the fast read result was persisted in one reply-only request and the three write results in the next. The provider accepted the history and every call was resolved, but the shared protocol validator treated the second reply message as non-adjacent. That warning suppressed every later canonical fork point and caused V2 reduction planning to reject the same effective history as `invalid_tool_history`.

The shared validator now permits one parallel call batch to drain across consecutive reply-only request messages. Ordinary user or system content, a message gap, mismatched identity, an orphan, or a new unresolved call still closes that continuation and remains fail-closed. Both hydrated history and the compact SQLite metadata scan use the same reply-only rule, so API projection, fork mutation, upgrade/list eligibility, and compaction planning remain aligned. A provider-shaped split-return fixture now exercises integrity, stepped eviction, compact fork-point projection, fork mutation, and protocol parity. Ruff, Black, and MyPy were clean; the three focused scenarios passed in `validation/runs/reports/20261003_223530_999168.md`; and the complete deterministic profile passed 132/132 in `validation/runs/reports/20261003_223643_501044.md`.

#### Paged Transcript Projection Recovery

Live inspection of a four-revision map exposed drift between the compact SQLite display-row paginator and the hydrated canonical transcript projector. The paginator selected one collapsed tool boundary while broader page hydration merged or split that range differently, and an unchecked dictionary lookup turned the recoverable discrepancy into a `KeyError`. The page contract now treats each compact boundary as authoritative: ordinary pages retain the batched projection, while a missing boundary is reprojected from its exact canonical interval. An interval that still cannot be represented fails with a stable 409 projection error rather than an internal exception, and successful recovery emits a content-free, deduplicable diagnostic containing only session, checkpoint, page, range, and row kind.

The System Activity viewer now keeps compact previews for ordinary metadata but renders long or multiline values as expandable details containing the complete escaped value. Tracebacks therefore remain readable without making every activity row permanently tall. The focused session-map scenario covers a page whose adjacent tool rows would merge under broader hydration, and the configuration module suite verifies full long-value retention and HTML escaping. The focused scenario passed in `validation/runs/reports/20261004_013932_806680.md`, all six configuration module checks passed, Ruff, Black, MyPy, JavaScript syntax, and diff-quality checks were clean, and the complete deterministic profile passed 132/132 in `validation/runs/reports/20261004_014113_871781.md`.

Live retesting against the original session database showed that interval reprojection was only a safety net, not the underlying correction. Named Pydantic AI `retry-prompt` parts were classified as tool replies by the hydrated projector but as ordinary messages by the compact SQL pager, producing impossible standalone rows at retry-only requests. The pager now mirrors the persisted display contract for custom calls, custom and provider-native returns, and named retry replies. The exact four-revision production snapshot now yields identical SQL and hydrated boundaries on the formerly failing page, and the deterministic scenario includes a retry-only tool response regression plus the content-free System Activity contract for any remaining irreconcilable boundary. The focused scenario passed in `validation/runs/reports/20261004_020941_114884.md`; Ruff, Black, MyPy, and diff-quality checks were clean; and the complete deterministic profile passed 132/132 in `validation/runs/reports/20261004_020314_936477.md`.

#### Longitudinal Trajectory Prompt Review

A semantic review of the only four-revision production map chain found that typed entries, stable source lineage, and total map size remained coherent, but the trajectory became progressively scoped to the newest revision rather than the whole session. Its final rewrite also framed required publication work as a displaced “more ambitious alternative,” despite no genuine strategic alternative in the evidence. The authoring contract now requires a durable session-level throughline across whole-map replacements, relates recent developments to that longer arc, and makes pivots, displaced alternatives, and evidence-driven reinterpretations conditional on genuine supporting evidence. The internal authoring prompt contract advances to `eviction-map-v11`; Compaction V2 and its user-facing context contract remain version 2. The focused schema and governed-authoring scenarios passed 2/2 in `validation/runs/reports/20261004_022546_436353.md`, and the complete Ruff, Black, and MyPy production gate was clean.

### Stage 5 — Frontend and Operator Experience

- Manually exercise desktop and mobile session-map inspection, revision and transcript paging, tool detail Back navigation, upgrade state, compaction notice, current/evicted forks, and dashboard polling.
- Tighten loading, empty, stale, error, retry, double-action, focus, keyboard, and modal cleanup behavior.
- Run JavaScript syntax/controller tests and rebuild CSS after style changes.

Exit evidence: automated frontend contracts pass and any remaining browser-only checks are listed explicitly.

### Stage 6 — Documentation, Validation, and Release Readiness

- Add the missing current-contract architecture/ADR coverage and reconcile tool/settings descriptions.
- Remove or relocate obsolete branch plans and experimental artifacts while retaining useful external research evidence.
- Run all directly affected deterministic scenarios, the production Python quality gate, and `python validation/run_validation.py run integration/core` once the behavior is stable.
- Inspect dependency freshness and compatibility without broad unrelated upgrades.

Exit evidence: current docs and code agree, the deterministic pre-merge profile passes, manual checks are recorded, and remaining deferred work is explicit.

## Deferred Beyond This Branch

- Cross-session memory ranking, fork-family deduplication, and vault-to-session linkage.
- Replacing nightly session summaries with map/checkpoint discovery.
- Semantic or hybrid transcript retrieval and embedding-free cross-session search design.
- Retrieval-context admission, reranking, claim-source audits, and a general-purpose decision tool.
- V2-to-V1 reconstruction, bulk migration, and automatic strategy conversion.
- Alternative map schemas chosen dynamically by a classifier.
- Treating map citations as an exhaustive evidence index or preserving privileged direct user quotations outside canonical history.

These directions may influence data portability and naming, but they must not expand this hardening pass into new memory features.

## Immediate Next Step

Run the remaining responsive and dark-mode rendering checks in a conventional browser, then decide which completed root implementation plans should be archived externally or removed from the product branch before final merge preparation.
