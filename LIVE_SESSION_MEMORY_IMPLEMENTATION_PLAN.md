# Live Session Memory Implementation Plan

## Status

Approved for incremental implementation. Slices 0 through 5 have established the map schema, deterministic patch boundary, authoring path, and cumulative Jev salience gate; Slice 6 is the next implementation slice. The selected first runtime hypothesis is cumulative Jev-gated maintenance: frequent cheap decision checks control less frequent generative reconciliation. Post-author model judging and regeneration are deliberately deferred so initial observe-mode tuning isolates the salience gate and author rather than coupling three probabilistic systems. This plan defines the first session-memory slice and intentionally leaves cross-session recall, vault recall, capability scouting, and research-artifact evaluation out of scope.

## Problem

Compaction currently preserves the canonical raw transcript but constructs each new effective-history recovery card from the previous generated card plus recent messages. Repeated compaction therefore passes durable session state through a chain of lossy prose rewrites. Omissions, reinterpretations, and stale details can compound even though the original evidence remains in SQLite.

The first slice must make durable current-session state independent of compaction. Compaction should manage model context and immediate resumption; it should not be the sole author of long-horizon session memory.

## Slice 0 Findings

The deterministic corpus and repeated-compaction contract are now frozen, and a model-generated proxy baseline plus private, ignored production-history stress replays have exercised the rubric. The production-derived artifacts and database remain under ignored validation data and are evidence for design decisions, not repository fixtures.

The evidence supports the architectural concern without establishing a production prevalence or long-horizon drift rate. Five-round proxy replays of two shorter sessions showed two distinct failures: inherited workflow policy displaced the newest handoff state, and assistant-proposed implementation details gradually acquired the status of completed or verified facts. A separate naturally compacted long session showed the related pattern of salience, lifecycle, and interpretation drift rather than wholesale factual corruption. Later rounds were not uniformly worse, so the plan must not assume monotonic degradation or tune to a single session's score trajectory.

The retained canonical message tail materially compensated for weak cards. Effective continuation could remain acceptable even when the card omitted the current task or misstated claim status because recent raw messages restored enough local context. This is useful runtime redundancy, but it masks card defects if evaluation measures only end-to-end continuation. Every later experiment must therefore score the derived artifact alone, the retained tail alone where practical, and the effective artifact-plus-tail history. Tail width and checkpoint placement are experimental variables, not hidden constants.

These findings justify moving forward with the safe foundation. They do not justify freezing map budgets, authoring prompts, classifier thresholds, or maintenance cadence. Those remain hypotheses behind later evidence gates.

## User-Visible Outcome

- Live session memory is disabled by default. With it disabled, or without compatible and ready decision and author models, chat and compaction use the current recovery-card behavior unchanged. The first live policy requires both roles; provider-neutral boundaries permit a future local or hosted classifier to replace Jev without changing map persistence or orchestration.
- When live session memory is explicitly enabled and its configured decision model is ready, a long-running session retains its objective, current focus, decisions, constraints, significant artifacts, completed milestones, and open work across repeated compactions.
- While the feature is active, the agent receives a small live session map on ordinary turns, including after compaction.
- The map exposes how current it is and which canonical messages support its entries.
- A failed or delayed map update does not fail the completed chat turn; the map remains visibly stale and can be retried.
- Raw chat messages remain canonical and independently retrievable. The map and any legacy recovery card remain derived artifacts.

## Core Invariants

1. Canonical `chat_messages` rows are the evidence boundary. A prior recovery card is never treated as canonical evidence for session-map facts.
2. Every map revision records a canonical raw-message high-water mark as `updated_through_sequence_index`.
3. Every durable map entry carries one or more source message sequence indexes. Assistant assertions, user statements, and tool outcomes retain distinguishable provenance.
4. An unchanged entry is copied forward deterministically rather than paraphrased by another generative pass.
5. A changed entry is patched only from canonical messages after the entry or map high-water mark, with explicit replacement or supersession metadata where applicable.
6. Map persistence and revision selection are atomic per session. A slower background update cannot overwrite a newer revision.
7. Map maintenance is off the response-critical path. Failure degrades to a stale prior revision and produces an observable retryable state.
8. The routine prompt receives one bounded current map, not the revision history or full supporting transcript.
9. In the live-memory path, the bounded map projection preserves durable session state and the retained canonical tail preserves immediate conversational continuity. Compaction only removes covered messages from effective context; it does not generate a second summary artifact.
10. Purging a chat session removes its map revisions and pending maintenance records through the session ownership boundary.
11. Attention fields reference canonical map entry IDs instead of duplicating their prose, and every referenced ID must exist in an admissible lifecycle state.
12. Unknown, unanswered, assumed, disputed, and superseded state remains distinguishable; absence of an entry is not evidence that a question was answered or a fact is false.
13. The feature is opt-in. In `off` mode, no map is maintained or injected and compaction follows the existing recovery-card path without depending on a decision service. `observe` may maintain an unused map, `context` may additionally admit it on ordinary turns, and only `compaction` may replace recovery-card generation with coverage-gated pruning backed by the admitted map and retained canonical tail.
14. The live-memory compaction path is admissible only when the configured author alias declares generative text capability, every model role required by the selected maintenance policy has a ready provider and credentials, and a committed map revision covers every canonical message to be removed from effective context. If any condition fails, compaction uses the existing recovery-card path wholesale.
15. Session-memory orchestration keeps provider-neutral decision classification and generative authoring as separate configured roles. Jev is the first decision adapter and the primary control mechanism for the first live policy, while Terra and Sol are author candidates through the ordinary text-model factory; none is a permanent dependency of the map, persistence, or compaction contracts.
16. Every completed turn durably advances pending turn and token accounting before maintenance is dispatched. At each configurable eligibility interval, the classifier receives the current accepted map and the entire canonical range after its coverage watermark through the frozen attempt watermark. A confident skip never consumes that range, advances map coverage, or resets cumulative hard counters; the next check includes the earlier messages again plus every newer completed message.
17. An ordinary next turn never waits for background reconciliation. Its effective history contains the last committed map and retains the exact canonical message range newer than that map's coverage watermark once, without duplicating that range in a second prompt block.
18. No live-memory compaction, checkpoint, sliding window, adaptive window, or future context reducer may remove a canonical message range that is not covered by the committed map. If synchronous catch-up cannot produce an admissible revision, the operation exits the live-memory path and the current recovery-card contract remains responsible for continuity. A successful live-memory reduction renders the committed map directly and does not create a generated recovery card.
19. Pending maintenance is recoverable from canonical history and durable watermarks after restart. An in-memory task or queue is never the sole record of outstanding work.
20. The first slice is deterministic session state, not RAG memory. It maintains and admits exactly one current map by session identity and sequence watermark; embeddings, vector storage, similarity search, ranking, top-k retrieval, graph traversal, and cross-session recall remain outside its runtime path.
21. Lifecycle, adoption, epistemic, and verification status must be supported by canonical source references independently of an entry's descriptive text. Repetition in a prior map or recovery card is never evidence that a proposal was adopted, an action completed, or an artifact verified.
22. Evaluation reports map-only fidelity separately from retained-tail-only and combined effective-history continuation. A retained raw tail may improve runtime safety, but it cannot make a stale or unsupported map pass its own quality gate.
23. Every runtime classifier, authoring, audit, and catch-up operation executes as an `ExecutionTaskRunner` task. The completed-turn hook may durably mark and dispatch pending work, but it never invokes a memory model inline or spawns an untracked coroutine. Session-memory work inherits the task runner's authority, task identity, cancellation, timeout, failure, per-session gate, bounded-concurrency, result, and observability contracts.
24. Decision progress and map coverage are distinct. `decision_checked_through_sequence_index` records scheduling and diagnostics only; only a deterministically validated, atomically committed map revision advances `updated_through_sequence_index` and resets the cumulative pending range.
25. The initial observe-mode policy has one probabilistic gate before authoring and no model-based post-author judge or regeneration loop. The author's typed operations must still pass deterministic schema, provenance, transition, budget, and concurrency validation before commit. Post-author model judging remains a separately gated future experiment after salience-gate and author behavior can be measured independently in live operation.

## Proposed First-Slice Map

Use a compact dialogue information state rather than a free-form summary. The proposed fields are grounded in several established lines of work:

| Research result | Schema consequence |
| --- | --- |
| Schema-Guided Dialogue Tracking represents active intent, accumulated user-goal constraints, and requested slots separately, and predicts turn-level state deltas rather than regenerating the whole state. | Separate active attention, goals, constraints, and unanswered information; make patches the update primitive. |
| CoALA distinguishes persistent working memory from episodic, semantic, and procedural long-term memory; its working memory contains active goals, active knowledge, perceptual input, and intermediate reasoning results. | Keep this map session-local and action-oriented; do not turn it into transcript history or general long-term memory. |
| MemGPT separates a writable working context from a rolling message queue and durable recall storage, and uses working context for current objectives, responsibilities, key facts, and preferences. | Admit the map routinely while keeping raw messages canonical and separately retrievable. |
| Generative Agents stores observations, reflections, and plans separately, and finds that explicit plans and replanning improve longer-horizon coherence. | Represent active work and next actions explicitly instead of inferring them repeatedly from facts. |
| LongMemEval evaluates information extraction, multi-session reasoning, temporal reasoning, knowledge updates, and abstention; it also finds that fact-only compression can lose useful context. | Preserve source references, update and temporal state, and explicit unknowns; never treat the map as a substitute for raw evidence. |
| Zep/Graphiti links derived facts back to source episodes and distinguishes ingestion time from the interval in which a fact is valid. | Record source sequence provenance and support optional effective validity separately from revision history. |
| Mem0 uses bounded `ADD`, `UPDATE`, `DELETE`, and `NOOP` operations after comparing new candidates with existing memory. | Use a small patch vocabulary, but represent contradiction through supersession or invalidation rather than destructive deletion. |

These studies address different problems, and several focus on cross-session personal memory or simulated agents rather than tool-heavy knowledge work. The first slice should borrow their recurring state distinctions and update invariants without importing a knowledge graph, reflection tree, retrieval score, vector index, memory-search step, or general persona store. Map admission is deterministic whenever the feature is active; it is never the result of a retrieval query.

The map should have a non-duplicative attention layer that points to stable entries. `active_goal_ids` and `active_work_item_id` identify what is foregrounded without maintaining another paraphrase of the same objective.

```yaml
schema_version: 1
session_id: session_abc
revision: 7
updated_through_sequence_index: 842
observed_source_content_revision: 219

attention:
  active_goal_ids: [goal_01]
  active_work_item_id: work_02
  changed_at_sequence_index: 842

goals:
  - id: goal_01
    text: Design the first live session-memory slice.
    status: active
    source_refs:
      - sequence_index: 801
        role: user

work_items:
  - id: work_02
    goal_ids: [goal_01]
    text: Define and validate the anti-drift map schema.
    status: in_progress
    next_action: Compare the proposed fields with representative sessions.
    next_action_owner: assistant
    blocker_ids: []
    state_source_refs:
      - sequence_index: 842
        role: user
    source_refs:
      - sequence_index: 842
        role: user

decisions:
  - id: decision_03
    text: Compaction is context management, not the source of session memory.
    status: active
    adoption_status: user_directed
    scope: session
    state_source_refs:
      - sequence_index: 806
        role: user
    source_refs:
      - sequence_index: 806
        role: user

constraints:
  - id: constraint_04
    text: Do not implement cross-session recall in this slice.
    status: active
    scope: goal_01
    active_from_sequence_index: 819
    active_until_sequence_index: null
    effective_time: null
    superseded_by: null
    source_refs:
      - sequence_index: 819
        role: user

commitments:
  - id: commitment_05
    actor: assistant
    text: Produce a research-grounded schema before implementation.
    status: open
    source_refs:
      - sequence_index: 842
        role: assistant

open_questions:
  - id: question_06
    text: What entry and token limits preserve utility without recreating context bloat?
    status: open
    owner: assistant
    source_refs:
      - sequence_index: 842
        role: assistant

artifacts:
  - id: artifact_07
    ref: LIVE_SESSION_MEMORY_IMPLEMENTATION_PLAN.md
    kind: implementation_plan
    status: active
    verification_status: observed
    status_detail: Approved plan updated with Slice 0 evidence.
    state_source_refs:
      - sequence_index: 842
        role: assistant
    source_refs:
      - sequence_index: 842
        role: assistant

observations:
  - id: observation_08
    text: Current compaction consumes the prior generated recovery card.
    epistemic_status: observed
    relevance: required_for_active_work
    source_refs:
      - sequence_index: 810
        role: tool
```

The collections have distinct jobs:

- `goals` describe desired outcomes, not methods or session-hygiene operations.
- `work_items` describe the active plan, progress, blockers, and next action. Completed or cancelled items remain only while they explain current state, then age out of the routine map while remaining recoverable from revisions and canonical messages.
- `decisions` record choices that govern later work.
- `constraints` record boundaries on acceptable outcomes or methods, including their scope and optional period of validity.
- `commitments` record who promised to do what, preventing an assistant plan from being misrepresented as a user requirement.
- `open_questions` represent requested or missing information explicitly, supporting correct abstention instead of invented closure.
- `artifacts` identify files, goals, jobs, external references, or other work products and their current status.
- `observations` hold only confirmed, assumed, or disputed working knowledge that materially affects the active goal, such as a tool outcome or environmental fact. This is not a general facts collection.

Each entry has a stable ID, concise text or reference, a type-specific lifecycle state, and role-bearing canonical source references. Mutable entries also carry `state_source_refs` for the evidence supporting their current lifecycle, adoption, or verification state; descriptive evidence alone cannot prove that a proposed action occurred. Decisions distinguish proposed, user-directed, accepted, rejected, and superseded states as applicable. Artifacts distinguish proposed, created, observed, verified, and failed states without implying that file creation proves operational correctness. Entries that can change over time support `active_from_sequence_index`, `active_until_sequence_index`, `last_state_change_sequence_index`, and `superseded_by` on the conversational transaction timeline. An optional `effective_time` interval records real-world validity only when the source states or deterministically implies it. Revision timestamps describe when AssistantMD recorded a change and must not be conflated with either timeline.

Rendering and authoring use an explicit priority order rather than an opaque importance score: the active work item and its next action, the newest lifecycle transition, blockers and unanswered questions, active constraints and decisions required for that work, then other bounded durable context. The renderer must not silently drop the current handoff while preserving lower-priority background. Exact per-section budgets remain an experiment, but overflow is deterministic and observable.

The stored revision envelope should also include an observed source-content revision, creation and update timestamps, authoring status, prompt-contract version, decision and authoring model identities, and optional error metadata. `updated_through_sequence_index` describes content coverage; the source-content revision is a concurrency token for the frozen source view. AssistantMD's current broad `history_revision` also advances for compaction checkpoints and failure metadata, so implementation must either add a content-specific revision or accept safe but unnecessary retries when using the broader value. These operational fields do not need to enter normal model context.

Do not add retrieval-oriented `importance`, `recency`, or `last_accessed` scores to the first-slice map. Those are useful for selecting among large memory collections, while this artifact is already the bounded working set admitted on every turn. Do not add graph entities and relations until a demonstrated session-continuity case requires relationship traversal.

## Anti-Drift Update Model

The maintenance pipeline should process only canonical raw messages newer than the selected map revision's high-water mark. That range is cumulative until a replacement map is deterministically validated and committed; classifier checks do not consume it.

```text
completed persisted turn
  -> durably accumulate pending turns/tokens after the accepted map watermark
  -> every X eligible turns, freeze the current cumulative canonical delta
  -> run parallel Jev adequacy checks for every map field plus global attention/completeness
  -> if every check is confidently stable, record the check and keep the delta pending
  -> otherwise produce bounded add/update/supersede/resolve operations with the author model
  -> validate every operation against delta source references
  -> apply operations deterministically to unchanged prior entries
  -> compare-and-swap a new revision at the observed high-water mark
```

The generative component should propose typed `ADD`, `UPDATE`, `SUPERSEDE`, `RESOLVE`, `CHANGE_ATTENTION`, or `NOOP` operations, not rewrite the whole map. `UPDATE` is restricted by per-entry mutable-field allowlists to lifecycle, attention, ownership, blocker, next-action, relevance, and verification state; it cannot change identity-bearing text, scope, actor, goal linkage, artifact reference, or original source evidence. A substantive identity change must use `SUPERSEDE`, which creates a new stable entry and records the replaced ID in the append-only patch audit. Ordinary code validates identifiers, source ranges, lifecycle transitions, size limits, enum values, referential integrity, and optimistic-concurrency preconditions before constructing the next revision. The current working set may omit the replaced entry because prior revisions and the patch audit preserve it; supersession chains are never injected into routine context. `RESOLVE` closes an entry without a replacement. This is the main protection against accumulated paraphrase drift.

The initial implementation should support a forced full audit from canonical raw history for validation and repair. Routine updates may consume deltas, but correctness must not depend on an unbroken chain of prior generated prose. An update that changes lifecycle, adoption, or verification status must cite delta evidence for that transition; carrying the prior assertion forward cannot promote its status.

## Persistence Boundary

The memory subsystem should own the live session map as the working-memory layer of the broader architecture described in the exploratory sketch. The map is related to session synopses, future candidate indexes, consolidated memory, vault recall, and context admission, even though the first slice performs no retrieval. Keeping these representations under `core/memory` gives the family one conceptual home without forcing them into one schema, lifecycle, or selection algorithm.

Physical persistence follows the artifact's transactional requirements. Append-only map revisions and the maintenance-state record belong in `chat_sessions.db` because they are keyed to canonical chat sequence indexes, must advance atomically with a completed turn, and must cascade with session deletion. This colocated storage does not make the map a `core/chat` domain object: `core/chat` owns the canonical source and transaction boundary, while `core/memory/session_map` owns the derived representation and its rules.

The first-slice fork policy is rebuild: a fork does not copy a source session's map or maintenance state because `ChatStore` rewrites the fork's message sequence indexes. The fork can author its own source-linked map later from its canonical copied transcript.

The current revision may be selected as the highest completed revision for the session. A maintenance record should track the observed source high-water mark, `decision_checked_through_sequence_index`, observed source-content revision, cumulative pending turn and token counts measured from the accepted map watermark, frozen attempt range, processing status, decision metadata, authoring attempt metadata, deterministic validation outcome, and last error without making process-local execution tasks durable domain state. Canonical history plus this record must be sufficient to reconstruct pending work after restart; the task runner is only a dispatch mechanism. Recording a confident classifier skip updates decision diagnostics but does not alter the map watermark or pending-range origin.

This slice changes persistent system state under the configured system root. Local validation must use isolated temporary system roots and must not reuse repository `system/` runtime data.

## Runtime Flow

1. When the assistant/tool side of a turn completes successfully, append those canonical messages and advance the session-memory pending watermark and turn/token counters in the same `ChatStore` transaction. The accepted user request remains durably written before model execution under the existing failure and retry contract.
2. Deliver response completion to the UI without waiting for classification or authoring.
3. On each configurable `X`-turn eligibility boundary, schedule or coalesce a session-scoped maintenance task adjacent to the existing post-turn automatic-compaction hook. `X` is an experimental setting, not a schema constant.
4. Inside the tracked task, freeze the accepted map revision, source-content revision, and cumulative canonical delta from `map.updated_through_sequence_index + 1` through the observed source high-water mark. Messages arriving after that watermark remain pending for the next attempt.
5. Ask the decision model, in parallel where the adapter permits, whether attention or any map collection needs reconciliation in light of the entire frozen cumulative delta, together with one broad reconciliation question. Authoring is skipped only when every score remains below its configured trigger threshold. Persist raw probabilities, thresholds, resolved model identity, usage, latency, and `decision_checked_through_sequence_index`; do not advance map coverage or clear the delta.
6. Trigger generative reconciliation when any required stability score crosses its calibrated change threshold or an independent cumulative turn/token ceiling is reached. The hard ceiling prevents indefinite classifier growth and cumulative micro-change from remaining unincorporated. If the classifier fails or becomes unavailable, leave the prior map and pending range intact, record a retryable failure, and keep ordinary chat on the existing fallback behavior.
7. Validate and apply the author's typed operations against the frozen evidence. Any schema, provenance, lifecycle, budget, or patch-application failure leaves the prior map and cumulative delta intact for a later retry; the initial policy does not repair, regenerate, or ask a second model to judge the proposal.
8. Persist the deterministically validated revision only if its predecessor and frozen source view still match; otherwise discard or retry against the newer state. A successful compare-and-swap advances map coverage and resets pending accounting to messages after the committed watermark.
9. During preparation of the next ordinary chat turn, load the latest completed map and render it before effective conversation history while retaining the exact canonical raw-message range newer than its coverage watermark once in that history. Do not wait for background maintenance or inject a duplicate delta block.
10. Before any context-reduction policy removes messages from effective history, require a map revision covering that range or use the current recovery-card path that preserves continuity without live memory.

## Relationship to Compaction

The first integration must retain the existing compaction implementation as the default and fallback path. When live session memory is explicitly enabled, its configured decision model is compatible and ready, and a committed map revision covers every canonical message about to leave effective context, the live-memory path does not generate a recovery card. It renders the bounded current map and keeps a recent canonical message tail; together these provide durable state and immediate conversational continuity without a second lossy representation.

Live-memory compaction is therefore a context-pruning operation over derived effective history, not a memory-authoring operation. Before pruning, it must force or await bounded map catch-up through the proposed removal boundary. It may then replace the covered prefix with a deterministic map projection or update checkpoint metadata so the already-admitted projection remains singular. It must never duplicate the map, duplicate the unmapped tail, mutate canonical messages, or derive map state from a prior recovery card.

If enablement, model readiness, required map freshness, rendering, or catch-up fails at the compaction boundary, the operation must run the current recovery-card implementation wholesale rather than producing a partially initialized live-memory history. Recovery cards remain supported for `off`, fallback, and rollback behavior; they are not generated on a successful live-memory path.

The experimental track should compare the current recovery-card history with map projection plus retained canonical tail at identical checkpoints. Score the map alone, tail alone where practical, and combined effective history for active goal, focus, blocker, next action, newest lifecycle transition, volatile artifact and verification state, historical leakage, stale-state retention, unsupported claims, prompt size, and continuation quality. Deterministic integration tests should assert coverage, source selection, singular admission, pruning boundaries, and fallback rather than exact generated prose.

## Jev Boundary

Use Pydantic AI's native TypeSafe integration rather than building a parallel Jev wire client. Place that integration behind a narrow provider-neutral decision-classifier contract consumed by session memory. Upgrade the pinned Pydantic AI dependency from `2.19.0` to a tagged release that contains `TypeSafeModel`, enable the `typesafe` extra, and construct a normal `Agent` with a typed Pydantic `output_type`. As of this plan update, `2.49.0` is the latest tagged release and the first available target in this repository's upgrade path; recheck the tagged API immediately before implementation because upstream `main` already contains post-release `DecisionModel` changes.

Represent the pre-author salience gate as one typed output model with direct `needs_reconciliation` probabilities for attention, goals, work items, decisions, constraints, commitments, open questions, artifacts, and observations, plus one broad reconciliation question. Put the shared task framing in agent instructions, put one atomic question in each field description, and pass the accepted current map plus the entire cumulative canonical delta since its coverage watermark as the state being judged. Every field evaluates the same frozen delta; a prior confident skip does not shorten the next request. Do not put questions in the run prompt or a system-prompt history item because Pydantic AI sends those as state, not as Jev questions.

Prefer bounded `float` fields from `0` to `1` expressing confidence that the corresponding field needs reconciliation. Pydantic AI maps each such field to Jev's unrounded probability of yes, allowing AssistantMD to tune broad and per-dimension trigger thresholds without inverse-probability ambiguity. Any score crossing its configured threshold triggers authoring; thresholds should intentionally favor false-positive author calls over false-negative stale maps. A `bool` output would round through one model-level boolean threshold and is less suitable for offline tuning or asymmetric error costs.

Persist each bounded-float output as the raw yes-probability. Read confidence and option distributions from response metadata only for output kinds that provide them, and capture resolved model identity, token usage, and latency through the ordinary Pydantic AI result. Use `jev-latest` only while gathering labelled experimental data; pin the versioned Jev model before adopting calibrated thresholds because the alias moves between releases.

The configured decision classifier is the primary control loop: it estimates whether accumulated changes require reconciliation in any map field or the map overall. It does not write map prose, validate authority, apply patches, or select canonical sources. A generative Pydantic AI agent remains responsible for proposing typed map patch operations when a score crosses its trigger threshold or the hard ceiling fires, and ordinary code validates and applies those operations.

The initial live policy stops after deterministic patch validation and compare-and-swap. It does not send the proposed map through a second Jev contract and does not regenerate an authored patch. A post-author judge may be investigated later as an isolated experiment using retained maps and frozen source ranges, but it is not a dependency of observe-mode maintenance.

The application-facing decision results contain named typed scores, the frozen source range and map revision they judged, resolved model identity, latency, usage when available, and optional confidence metadata; adapters normalize provider-specific results without claiming that scores from different models are calibrated equivalently. Prefer one typed request whose independent questions the decision service can evaluate in parallel; if an adapter implements multiple calls, they remain children of the same tracked session-memory task and share its timeout and cancellation boundary.

Jev is the first adapter because Pydantic AI supplies its native question and probability mapping. Future adapters may use another hosted classifier, a local model, or a structured-output generative model, provided they satisfy the same bounded-input and typed-result contract. Threshold and calibration profiles are keyed by adapter and resolved model version rather than treated as universal Jev constants.

The TypeSafe credential and any base-URL override must enter through AssistantMD's existing principal-owned secret and settings boundaries. Never persist the credential in map metadata, model traces, validation artifacts, or `system/secrets.yaml`. Remote disclosure policy must be explicit because the bounded canonical delta is sent to TypeSafe's API.

## Provider, Model, and Secret Configuration

Seed TypeSafe as a built-in provider and `jev` as a reusable decision-model alias in `core/settings/settings.template.yaml`. Session memory selects that alias through its own setting, while other bounded classification features may use the same decision capability and runtime. The initial configuration contract is:

```yaml
settings:
  live_session_memory_mode:
    value: off
    description: "Live session-memory policy. The initial runtime supports off and observe."
    category: "Session Memory"
    restart_required: false
  live_session_memory_decision_model:
    value: jev
    description: "Decision-capable model alias used for live session-memory classification."
    category: "Session Memory"
    restart_required: false
  live_session_memory_author_model:
    value: gpt-mini
    description: "Generative text model alias used to author session-map patches."
    category: "Session Memory"
    restart_required: false
  live_session_memory_eligibility_turns:
    value: 3
    category: "Session Memory"
    restart_required: false
  live_session_memory_broad_change_threshold:
    value: 0.25
    category: "Session Memory"
    restart_required: false
  live_session_memory_field_change_threshold:
    value: 0.50
    category: "Session Memory"
    restart_required: false
  live_session_memory_max_pending_turns:
    value: 30
    category: "Session Memory"
    restart_required: false
  live_session_memory_max_pending_tokens:
    value: 80000
    category: "Session Memory"
    restart_required: false
  live_session_memory_task_timeout_seconds:
    value: 180
    category: "Session Memory"
    restart_required: false
  live_session_memory_max_concurrent_tasks:
    value: 2
    category: "Session Memory"
    restart_required: false

models:
  jev:
    provider: typesafe
    model_string: jev-latest
    capabilities: ["decision"]
    description: "Default Jev decision model for classification workloads"
    user_editable: false

providers:
  typesafe:
    api_key: TYPESAFE_API_KEY
    base_url: null
    user_editable: false
```

`TYPESAFE_API_KEY` is a secret-name pointer, not a credential embedded in settings. Its value must be written and read through the existing principal-owned encrypted secrets store and current Secrets API/UI. Provider and model status may expose the pointer name and a boolean indicating whether it has a value, but must never return, log, trace, or write the value to validation artifacts. A custom TypeSafe endpoint should be represented by the existing `base_url` field only if the tagged Pydantic AI integration actually supports one; otherwise omit that unsupported surface.

Use `jev-latest` only during labelled calibration, then change `model_string` to the selected versioned Jev identity before thresholds become a supported policy. Runtime code must resolve the reusable `jev` alias through the normal model/provider configuration and secret readiness mechanisms, while constructing it through a dedicated Pydantic AI decision-model path rather than the generative chat-model factory.

The session-memory service reads `live_session_memory_decision_model`; it must not hard-code the Jev alias. Any non-`off` mode requires the selected alias to exist, declare `decision`, and pass adapter-specific provider and credential readiness. This alias indirection is the extension point for future hosted or local classifier adapters.

The existing capability normalization and chat selector currently treat every non-embedding model as text-capable. Extend the configuration contract so `decision` remains a decision-only capability, and make chat selection require an explicit `text` capability. This keeps the Jev model visible to configuration and readiness reporting without offering it as a conversational model.

If the feature is disabled, do not schedule map maintenance or inject a persisted map. If the selected provider, alias, adapter, dependency, or secret is unavailable, mark live memory unavailable, keep any last successful map as inactive derived state, and retry only under bounded policy. Missing decision-model configuration must not fail or hide ordinary chat, and compaction must use the current recovery-card path. Deleting or clearing `TYPESAFE_API_KEY` must invalidate provider and model readiness on reload without requiring process access to the plaintext value.

## Affected Areas

### Ownership and Reuse Boundaries

The boundaries are sufficiently clear to implement the safe foundation without inventing a parallel architecture. `core/llm` owns reusable decision-model execution, `core/memory` owns the family of derived memory representations, and `core/chat` owns the canonical transcript plus the lifecycle seams that must be atomic with it. The live map is the session-working-memory member of that family; existing session summaries are discovery-oriented synopses, and future candidate, consolidated, and vault-memory layers may build on common provenance and context-admission concepts without sharing the live map's update path.

This placement deliberately distinguishes conceptual cohesion from premature abstraction. The first slice should not introduce a generic memory base class, universal memory record, common retrieval pipeline, or shared database merely to anticipate later layers. Shared contracts should be extracted only when a second implemented memory layer needs the same semantics. Canonical source references and authority vocabulary are the likely first candidates; map patches, freshness policy, and deterministic admission remain specific to the session-map subdomain.

| Concern | Owning boundary | Existing seam to reuse | Boundary to preserve |
| --- | --- | --- | --- |
| Provider, model alias, capability, and readiness | `core/settings` and `core/llm/model_utils.py` | Existing provider/model registry, capability lookup, configuration status, and secret-name resolution | Do not add Jev-specific settings stores, endpoints, or plaintext secret handling. |
| Decision-model construction and invocation | A new, initially single `core/llm/decision.py` module | Pydantic AI model integration, resolved aliases, existing provider policy, and ordinary result/usage handling | Keep `core/llm/model_factory.py` generative-chat-only so decision-only models cannot leak into chat selection. Split provider adapters into a package only when a second real adapter or module size demonstrates the need. |
| Secret values | Existing `core/secrets` service through the current settings/configuration facade | Principal-owned encrypted SQLite, current Secrets API/UI, and readiness redaction | Do not add another secret manager, persist credentials in session-memory rows, or populate `system/secrets.yaml`. |
| Memory-family architecture | `core/memory` | Existing session-summary/synopsis code and the sketch's source-authority model | Treat live maps, synopses, candidates, consolidation, and recall as related but distinct subdomains. Do not retrofit retrieval behavior into the live map or force the existing summary schema to serve working memory. |
| Map domain | `core/memory/session_map/models.py` | Pydantic models and canonical chat sequence identities | Own schema, lifecycle rules, patch validation, deterministic application, and bounded rendering here; do not mix SQL, task dispatch, retrieval, or provider calls into the domain model. |
| Map persistence | `core/memory/session_map/store.py` with its physical migration registered against `chat_sessions.db` | Session foreign keys, `ChatStore.transaction()`, and append-only canonical messages | Keep map SQL out of the already broad `ChatStore`; accept an existing SQLite connection for mutations that must join the successful-turn transaction. Do not create a second database, duplicate session deletion logic, or move unrelated summary/retrieval tables into the chat database. |
| Raw source reads | `ChatStore` | Stored-message metadata and ordered sequence indexes | Add only a narrow canonical range/read API if the current methods are insufficient. Session-memory code must not issue private `chat_messages` queries or use `ChatHistoryService`'s normalized public representation when exact source identity is required. |
| Authoring and classification policy | `core/memory/session_map/authoring.py`, introduced only with the offline experiments | Provider-neutral decision runtime, typed Pydantic outputs, and canonical range reads | Own prompts, change-signal schema, patch proposal, and authoring diagnostics here. The decision adapter classifies; it does not mutate the map or choose evidence. |
| Runtime orchestration | `core/memory/session_map/service.py`, introduced with shadow maintenance | `RuntimeContext` composition, `ExecutionTaskRunner.start_background(...)`, keyed `ExecutionGatePolicy`, bounded `ExecutionConcurrencyPolicy`, task hooks, and durable maintenance rows | The service owns coalescing, freshness, compare-and-swap, and failure isolation, but every classifier, author, audit, and catch-up run remains an ordinary tracked execution task. Do not invoke a model from the completion hook, call the author directly from runtime code outside task ownership, spawn an untracked coroutine, add a worker framework, use a process-local queue as durable state, or put memory policy in `RuntimeContext`. |
| Successful-turn hook | `core/chat/task_execution.py` | The existing transaction that commits the successful assistant/tool result and clears turn failure | In that transaction, call the store's narrow `record_completed_turn(...)` mutation. Keep classification and authoring outside the transaction and off the response-critical path. The earlier accepted-user write remains unchanged. |
| Prompt admission | `core/chat/executor.py` with composition in `core/chat/instructions.py` | Primary chat preparation, instruction layers, effective history, and canonical history APIs | The session-memory service returns a bounded rendered block and the required uncovered range; executor owns assembling each exactly once. Do not implement map admission as a selectable LLM capability or generic history tool. |
| Compaction strategy | `core/chat/compaction.py` | Existing thresholds, task-runner execution, checkpoint writes, and recovery-card fallback | Factor source selection behind an explicit strategy inside the existing compaction boundary. Do not create a second compactor or weaken the legacy tool-result preservation contract. |
| API/UI | Existing configuration surfaces; a thin status projection only when runtime modes need it | Generic provider/model/secret editors and status serializers | Do not add session-memory CRUD or a Jev-specific API in the first slices. |
| Validation | Existing focused tests and `validation/scenarios/integration/core` or `experiments` | Current compaction, persistence, secrets, runtime-task, and configuration scenarios | Put deterministic contracts in integration/core and live-model probes in experiments; do not make network evidence a merge gate. |

The `core/memory/session_map` package should be created incrementally rather than scaffolded in full. Slice 3 adds `models.py` and `store.py`; Slice 5 adds `authoring.py` only when the prompt experiment exists; Slice 6 adds `service.py` only when runtime orchestration exists. Its `__init__.py` should expose the small public surface used by chat execution and, later, by explicitly designed promotion or recall flows. This gives each uncertain layer a replaceable boundary without producing placeholder modules.

The atomic seam does not require rewriting current message persistence. The accepted user request is already committed before model execution. On successful completion, `core/chat/task_execution.py` opens a `ChatStore.transaction()` and appends the remaining assistant/tool messages. The same connection can be passed to `SessionMapStore.record_completed_turn(...)` so pending counters and the completed source range advance atomically with that successful commit. The post-commit hook then schedules maintenance through the existing runtime task runner.

The exact prompt representation remains an experimental decision, but its module ownership does not: the session-memory service renders data, `core/chat/instructions.py` composes any system-owned map layer, and `core/chat/executor.py` selects the canonical uncovered history exactly once. Changing from an instruction layer to another provider-stable context representation therefore does not move domain or persistence responsibilities.

### Concrete Files Affected

- `core/chat/schema.py`: register the physical map revision and maintenance-state migrations in `chat_sessions.db` so foreign keys and transactional initialization remain coherent.
- `core/chat/chat_store.py`: only narrow canonical range reads and the existing shared transaction boundary; map CRUD belongs to `core/memory/session_map/store.py`.
- Existing `core/memory/` and a new incremental `core/memory/session_map/` package: establish the broader memory-family boundary, with deterministic map models and storage first and authoring and service modules only in their later slices. Existing `session_summary` behavior is not rewritten in the first slice.
- `core/chat/task_execution.py`, `core/chat/executor.py`, and `core/chat/instructions.py`: successful-turn recording, post-commit scheduling, and per-turn context admission.
- `core/chat/compaction.py`: select a map-backed source strategy only after the standalone path works, retaining the current strategy as default and fallback.
- `core/runtime/context.py` and `core/runtime/bootstrap.py`: compose one session-memory service and reuse `ExecutionTaskRunner`; no new task subsystem.
- `pyproject.toml` and `uv.lock`: upgrade the exact Pydantic AI pin and enable its `typesafe` extra.
- `core/settings/settings.template.yaml`, `core/settings/store.py`, `core/settings/config_editor.py`, `core/llm/provider_policy.py`, and configuration API/UI surfaces: built-in `typesafe` provider, reusable `jev` alias, decision-only capability, safe readiness reporting, and exclusion from chat selection.
- Existing `core/secrets` and settings/configuration facades: store the value named by `providers.typesafe.api_key` (`TYPESAFE_API_KEY`) without introducing a second credential path.
- `core/llm/model_utils.py` and new `core/llm/decision.py`: resolve the configured alias, validate decision capability and adapter readiness, and use Pydantic AI's native TypeSafe integration without treating TypeSafe as a generic OpenAI-compatible endpoint.
- `docs/development/architecture.md` and a new ADR: the related memory-layer topology, source-linked patch semantics, physical colocation with canonical chat state, task-runner reuse, and separation of live-map, synopsis, retrieval, consolidation, and compaction responsibilities.
- Validation scenarios and focused unit tests for configuration, decision-runtime contracts, persistence, update races, prompt admission, failure degradation, purge behavior, and repeated compaction.

The memory subsystem owns map semantics, durable maintenance state, reconciliation, freshness, and rendering. The chat subsystem owns the completion hook, canonical message authority, the enclosing transaction, and the no-unmapped-eviction enforcement point. Existing access services retain authority checks, while runtime owns only service composition and shared task-execution primitives. Model prompts and schemas may later be exposed through a narrow `session_memory` authoring type, but a generic post-turn script is not allowed to own these lifecycle guarantees.

## Slice Strategy

Each slice must be independently testable, leave the current recovery-card behavior intact unless its own explicit experimental mode is selected, and avoid assuming that a later hypothesis will succeed. Deterministic platform and persistence work lands before live-model experiments. Experimental slices first produce retained evidence without affecting prompts, then affect prompts without affecting compaction, and only finally become eligible to replace compaction. A failed evidence gate sends the design back to that slice; it does not get compensated for by adding complexity downstream.

The reusable decision-model platform is intentionally separate from session memory. Provider configuration, the `decision` capability, model resolution, and Pydantic AI adapters may support future classification features even if the session-map experiment is stopped.

## Testable Delivery Slices

### Slice 0: Baseline and Evaluation Corpus

**Status:** Complete. The deterministic corpus, replay validator, live-service probe, proxy baseline, and private ignored stress-replay evidence are in place. The stress evidence validates the rubric's sensitivity to salience, lifecycle, and epistemic drift while remaining explicitly unsuitable for estimating production rates.

**Build:** Lock down the current recovery-card contract with the existing repeated-compaction scenario and assemble privacy-safe representative session fixtures containing goal changes, corrections, constraints, tool observations, artifacts, open questions, failed turns, and at least three compactions. Record separate expected outputs for map state, classifier field changes, and immediate recovery-card state so one representation is not used to grade another.

**Verify:** Run the existing deterministic compaction scenario unchanged; prove the fixtures can be replayed without a live service; capture baseline continuation quality, unsupported-claim rate, historical leakage, prompt size, and repeated-card drift. For model-generated stress probes, report artifact-only fidelity separately from effective-history continuation and record retained-tail width at every checkpoint.

**Exit gate:** Met. The corpus and rubric were reviewed before any session-map prompt or threshold tuning, and the slice changed no product behavior. Production-derived data remains ignored and is not a distributable fixture.

### Slice 1: Reusable Decision-Model Configuration

**Status:** Complete. The decision-only capability, built-in TypeSafe provider, Jev alias, encrypted-secret readiness, settings repair, configuration API/UI metadata, and generative-chat exclusion are implemented without session-memory hooks or external model calls.

**Build:** Add `decision` as a first-class model capability, preserve it without implicitly adding `text`, and make chat selection require explicit `text`. Seed the built-in `typesafe` provider, reusable `jev` model alias, and `TYPESAFE_API_KEY` pointer. Treat `typesafe` as a native provider shape that does not require the generic OpenAI-compatible `base_url`. Reuse the existing encrypted principal-owned secret store and configuration status surfaces; do not add session-memory hooks or make an external model call.

**Verify:** Add focused settings, API, and UI tests for capability normalization, chat exclusion, provider/model readiness with populated and missing secrets, secret clearing, safe status serialization, template upgrade behavior, and absence of plaintext in settings or logs.

**Exit gate:** `jev` is safely configurable and visible in readiness reporting, cannot be selected for chat, and has no effect on existing sessions or compaction. This is the first implementation slice and is useful independently of live memory.

### Slice 2: Provider-Neutral Pydantic AI Decision Runtime

**Status:** Complete. Pydantic AI is pinned to `2.49.0` with the `typesafe` extra, and `core/llm/decision.py` provides one typed provider-neutral request/result contract with a TypeSafe adapter, alias and capability validation, encrypted-secret resolution, timeout propagation, resolved model identity, usage, latency, supported confidence metadata, sanitized errors, and lifecycle logging. The deterministic transport contract proves Jev question placement, response normalization, timeout classification, and credential redaction; the in-process fake proves another adapter can satisfy the contract without TypeSafe configuration. The dependency upgrade's `httpx2` transition is covered for Anthropic retry construction, and deferred-tool visibility regression coverage now follows Pydantic AI's separate authored-intent and current-visibility fields.

An opt-in live smoke against the configured `jev` alias succeeded with a synthetic conversation delta. The moving alias resolved to `jev-1.13.0` for that run and returned two bounded probabilities plus token usage in one request. This is connectivity evidence only; it does not establish classifier thresholds or session-memory quality, which remain Slice 4 questions.

**Build:** Upgrade to a tagged Pydantic AI release with native TypeSafe support and enable the `typesafe` extra. Add the narrow provider-neutral classifier interface and TypeSafe/Jev construction in `core/llm/decision.py`; keep the in-process fake in test support unless production configuration later needs it. Resolve aliases, capability, credentials, timeout, model identity, usage, latency, and provider errors through normal configuration boundaries. Keep `core/llm/model_factory.py` limited to generative chat models, and do not add a generic user-facing classification tool or session-memory lifecycle hook yet.

**Verify:** Contract-test both adapters with the same typed fixtures; mock the TypeSafe transport for deterministic construction, request-shape, result, timeout, and redaction tests; add regression coverage for every existing Pydantic AI provider and test model affected by the dependency upgrade; add one opt-in live-service smoke scenario that proves the configured `jev` alias can return typed bounded values. Verify that another adapter can satisfy the interface without TypeSafe imports or credentials, and run the full deterministic core profile before considering the dependency-upgrade slice stable.

**Exit gate:** Application code can invoke a configured decision-capable alias through one stable interface, and every deterministic test passes without network access. The live smoke is evidence, not a merge gate.

### Slice 3: Deterministic Session-Map Domain and Storage

**Status:** Complete. `core/memory/session_map` now owns a strict source-linked working-set schema, stable typed entries, bounded `ADD`, `UPDATE`, `SUPERSEDE`, `RESOLVE`, `CHANGE_ATTENTION`, and `NOOP` patches, per-entry mutable-field allowlists, lifecycle transition validation, deterministic application, and bounded priority rendering. Identity-bearing changes require supersession; replaced entries remain recoverable from append-only revisions and patch audits rather than accumulating in routine context. The chat database migration adds revision and maintenance tables with cascade ownership, compare-and-swap commits, canonical source-role validation, pending counters, frozen source ranges, arrivals-during-processing preservation, sanitized failure state, and restart recovery. Forks intentionally rebuild because chat sequence indexes are rewritten. `ChatStore` exposes only the narrow canonical raw range read required by later authoring. No model, scheduling, prompt-admission, or compaction hook is active.

**Build:** Create `core/memory/session_map/models.py` for the provisional typed map schema, stable entry IDs, source references, lifecycle states, bounded patch operations, validation, deterministic application, and bounded rendering. Create `core/memory/session_map/store.py` for append-only revisions, maintenance state, and compare-and-swap persistence in `chat_sessions.db`; its atomic mutation accepts the existing `ChatStore` transaction connection. Register the physical schema through the chat database migration boundary and add only the narrow canonical range-read method needed to `ChatStore`. Atomically advance durable pending watermarks and counters with the successful assistant/tool commit. Implement restart reconstruction and frozen attempt ranges, but do not call a model, schedule background authoring, inject a map, or alter compaction.

**Verify:** Apply hand-authored patches over Slice 0 fixtures. Test illegal transitions, invalid source ranges, unsupported lifecycle/adoption/verification promotion, stale writers, messages arriving after a frozen range, restart recovery, fork policy selected for the slice, size bounds, deterministic priority overflow, and session purge. Assert that current handoff state survives when lower-priority durable background exceeds the render budget. Decide whether a content-specific source revision is necessary based on tests against the current broader `history_revision`.

**Exit gate:** All state transitions and persistence invariants are deterministic and model-independent. The feature remains inert and disabled.

### Slice 4: Offline Change-Detection Experiment

**Hypothesis:** A decision classifier can identify which map dimensions materially changed with enough recall to reduce unnecessary generative reconciliation without hiding important corrections or open work.

**Predeclared evaluation policy:** Use `research_memo_corrections` and `read_only_migration_diagnosis` only for calibration, and hold out `goal_switch_and_cancellation` until one global threshold has been selected. Search thresholds from `0.10` through `0.90` in `0.05` increments, reject calibration candidates below `0.65` micro precision, maximize micro F2 to weight recall, and break ties in favor of the higher threshold. Do not tune separate per-dimension thresholds on this nine-batch corpus. Promotion requires holdout consequential recall of `1.00` across goals, decisions, constraints, commitments, and open questions, overall holdout macro recall of at least `0.80`, overall holdout micro precision of at least `0.70`, median request latency no greater than `1.5` seconds, p95 latency no greater than `3.0` seconds, and no more than `5,000` input tokens across the nine requests. Because the holdout is small, passing these gates only permits the offline authoring experiment; it does not establish production calibration. A failed gate triggers question or batching redesign without relabelling or tuning against the holdout.

**Status:** Complete as an offline experiment, without promoting a per-turn classifier policy. The first labelled `session-map-change-v1` run failed its frozen promotion gate and must not be retuned into a pass. Jev resolved to `jev-1.13.0`; calibration selected a global threshold of `0.30`. Holdout macro recall was `0.944`, but consequential recall was `0.875` because the explicit goal establishment in the first held-out batch scored `0.25`. Holdout micro precision was `0.682`, below the `0.70` gate, with false positives concentrated in semantically adjacent dimensions. Latency was strong at `0.253` seconds median and `0.629` seconds p95. Total input usage was `15,348` tokens, more than three times the predeclared budget, showing that the nine-question request shape is not lightweight even though execution is fast. The complete raw probabilities and usage are retained locally under ignored validation data at `validation/data/live_session_memory/results/jev_change_detection_v1.json`.

This result does not distinguish model weakness from contract ambiguity well enough to justify a threshold change. Goal, attention, work-item, decision, constraint, commitment, artifact, and observation labels overlap in ordinary language, and the v1 request supplies only a delta rather than the prior structured state against which “changed” is defined. Before a v2 run, expand and freeze the labelled corpus with a new untouched holdout, define sharper dimension-boundary examples from calibration data only, and compare the classifier with a simple deterministic cadence on avoided authoring calls and total tokens. The existing held-out case is now development evidence and cannot serve as the untouched v2 promotion set. No runtime maintenance hook is admissible from v1.

**V2 predeclared evaluation policy:** Replace the overlapping nine-field scheduling question with one broad `reconciliation_needed` probability judged against both a compact current map and its canonical delta. Use the synthetic `project_delivery` and `diagnosis` cases only for calibration, and keep `editorial_pivot` and `release_investigation` untouched until one global threshold has been selected. The frozen twenty-batch corpus includes durable changes, exact repetitions of mapped evidence, acknowledgements, status queries, and session mechanics. Search thresholds from `0.10` through `0.90` in `0.05` increments, reject calibration candidates below `0.75` precision, maximize binary F2, and break ties in favor of the higher threshold. Promotion requires holdout recall of at least `0.90`, precision of at least `0.80`, and recall of `1.00` for consequential goal, decision, constraint, commitment, open-question, and attention changes. Median request latency must not exceed `1.0` second, p95 latency must not exceed `2.0` seconds, and total input usage must not exceed `8,000` tokens across twenty requests. Report predicted reconciliation count beside reconcile-every-batch and classifier-plus-cumulative-hard-cadence-every-three-batches baselines. A negative classification never resets the hard counter. Passing permits only the Slice 5 offline authoring experiment; failure requires another contract or scheduling design and cannot be repaired by relabelling or threshold-tuning against the holdout.

The `session-map-reconcile-v2` run cleanly separated all ten calibration and all ten untouched holdout batches at the calibration-selected threshold of `0.25`: holdout precision, recall, and consequential recall were each `1.00`. Jev again resolved to `jev-1.13.0`; latency was `0.237` seconds median and `0.269` seconds p95. The broad single question reduced average input usage from about `1,705` tokens per v1 request to about `525`, but its `10,505` total input tokens still failed the frozen `8,000`-token gate. It would avoid eight of twenty authoring calls by classifier judgment alone, or six when unioned with the illustrative every-three-batch hard cadence, while still paying for twenty classifier calls. Raw results are retained locally at `validation/data/live_session_memory/results/jev_reconciliation_v2.json`.

The result supports the broad relative-to-current-map question and rejects calling it after every completed turn. Slice 5 may proceed because its offline authoring quality is independently testable, but no classifier threshold or runtime hook is promoted. Runtime scheduling must batch canonical deltas on a deterministic minimum cadence before invoking the classifier, retain an independent hard turn/token trigger, and measure combined classifier-plus-authoring cost once authoring cost is known. The exposed v2 holdout cannot be used to claim that the future batched policy is calibrated.

The subsequent design decision is to make frequent batched Jev checks the primary maintenance control loop rather than an optional optimization around deterministic authoring. V2 remains useful evidence that relative-to-current-map classification is fast and discriminative, but its single broad question and independent batches do not test the selected contract. The next classifier corpus must use cumulative prefixes: after a labelled stable decision, the following request presents the same accepted map and the prior unmapped messages again together with the newly completed messages. It must label field adequacy and missing concepts, then evaluate high-confidence skip thresholds with false-negative stale-map errors weighted more heavily than avoidable author calls.

**V3 predeclared evaluation policy:** Use `session-map-cumulative-adequacy-v3` over eight fixed-map epochs with five cumulative checkpoints each. Four cases are calibration-only and four remain untouched holdouts until the first live run. Every request contains the same accepted map plus all canonical turn groups from the start of its epoch through that checkpoint. The typed output asks whether attention, every map collection, and global coverage remain adequate; policy invokes the author when any score is below one global calibration threshold. Search thresholds from `0.10` through `0.95` in `0.05` increments, reject calibration candidates below `0.75` reconciliation precision, maximize binary F2, and break ties toward the higher threshold to favor recall. Promotion requires holdout consequential recall of `1.00`, overall holdout recall of at least `0.90`, overall holdout precision of at least `0.75`, and macro recall of at least `0.80` across fields with labelled inadequate examples. Median latency must not exceed `1.0` second, p95 latency must not exceed `2.0` seconds, average input usage must not exceed `1,500` tokens, and total input usage across forty requests must not exceed `60,000` tokens. Retain raw field scores and compare eligibility intervals of one, two, and three turns with a five-turn hard ceiling, reporting classifier calls, classifier versus hard-ceiling triggers, false-early triggers, and maximum detection lag. Passing permits the coupled author experiment but does not establish production thresholds; failure returns to question, field, or cadence design without relabelling the holdout.

The first V3 live run completed all forty requests on `jev-1.13.0` without transport or schema failure, but failed the promotion gate. Median latency was `0.264` seconds and p95 was `0.416` seconds, confirming that Jev is fast enough for frequent checks. The calibration-selected global adequacy threshold was `0.20`; holdout precision was `1.00` and recall was `0.917`, but consequential recall was `0.917` because the new written-confirmation requirement was not detected until the later confirmation resolved the underlying question. Calibration itself exposed a sharper tradeoff: the selected threshold reached only `0.583` recall at `0.778` precision, while thresholds high enough to reach full calibration recall reduced precision to `0.60`. The gate also missed the draft-artifact epoch at the selected threshold and produced early false triggers for individually minor formatting preferences.

The ten-field adequacy request consumed an average of `2,591` input tokens and `103,655` total input tokens, failing both cost gates despite low wall-clock latency. Several irrelevant dimensions emitted low adequacy scores, so taking the minimum across ten questions amplified field noise; conversely, some still-unmapped artifact scores increased as later acknowledgements were appended. The V3 holdout is now exposed and must not be relabelled or used to tune a passing threshold. Raw scores are retained locally at the ignored path `validation/data/live_session_memory/results/jev_cumulative_adequacy_v3.json`. The next calibration-only revision should compare direct change polarity, a broad global gate with field scores used only for diagnosis or author guidance, and field-specific state projection before freezing a new holdout. It should preserve cumulative prefixes and the hard ceiling rather than retreating to independent deltas.

**V4 calibration-only policy:** Reuse only the four V3 calibration epochs and do not inspect or invoke the exposed V3 holdout. Ask direct yes-probability questions for reconciliation of each map field plus one broad `reconciliation_needed` question against the same cumulative prefixes. Separately select a global threshold for the broad score and for the maximum field score using the V3 calibration procedure: require at least `0.75` precision, maximize F2, and break ties toward the higher threshold. This diagnostic advances only if a strategy reaches `1.00` calibration recall and at least `0.75` precision; it cannot support production selection or a holdout claim. Report latency and usage to distinguish decision polarity from the cost of asking ten questions.

V4 direct-change calibration passed both candidate strategies on its first frozen run. The broad score selected `0.25` and the maximum field score selected `0.50`; each produced `1.00` recall and `0.75` precision over twenty calibration checkpoints. The four false positives were the deliberately minor-but-accumulating prefixes, so their operational effect would be safe early authoring rather than stale memory. Median latency was `0.266` seconds and p95 was `0.345` seconds. Average input remained high at `2,405` tokens because the request still asks ten questions, separating the polarity improvement from request-shape cost. Raw calibration output is retained at the ignored path `validation/data/live_session_memory/results/jev_cumulative_change_v4_calibration.json`.

**V4 fresh-holdout policy:** Freeze four new five-turn epochs covering scope narrowing, correction of an accepted observation, verification requiring multiple checks, and a blocker-driven work handoff. Run the unchanged V4 prompt once using the calibration-selected broad threshold `0.25` and maximum-field threshold `0.50`; do not search or alter thresholds. Each strategy must achieve consequential recall `1.00`, overall recall of at least `0.90`, and precision of at least `0.75`. Failure rejects that aggregation strategy; passing establishes only that cumulative Jev gating is promising enough to couple to authoring and later test on production-derived long-session windows.

V4 passed its fresh holdout without threshold changes. Both the broad `reconciliation_needed >= 0.25` strategy and the maximum field score `>= 0.50` strategy produced `1.00` precision, `1.00` recall, and `1.00` consequential recall across twenty checkpoints. Stable broad scores ranged from `0.07` to `0.21`, while changed-state scores ranged from `0.78` to `0.94`; maximum field scores showed comparable separation. The cases covered explicit scope narrowing, correction of an accepted fact, verification emerging only after several cumulative checks, and a blocker-driven handoff. This is strong evidence that direct cumulative change polarity can gate reconciliation, but the corpus remains synthetic and small. Raw results are retained at the ignored path `validation/data/live_session_memory/results/jev_cumulative_change_v4_holdout.json`. The broad score is the simpler scheduling signal; field scores remain valuable for diagnostics and later author/verifier guidance, but their incremental value must justify the repeated-input cost on production-derived windows.

A first production-derived check used accepted incremental maps from the 1065 redevelopment session. Against the map through message 161, the next ten canonical messages contained a new expert assessment, changed immediate work, and a substantive WFC response; V4 triggered with broad probability `0.65` and maximum field probability `0.77`. Against the map through message 267, the next completed assistant turn read and then materially edited the primary agreement-review artifact and changed its action list; at the actual completed-turn boundary through message 274, V4 triggered at `0.41` broad and `0.51` maximum-field probability. A smaller read-only search prefix scored `0.08` and `0.12`, respectively. Intermediate tool-call prefixes produced non-monotonic scores, but they are not runtime eligibility points because maintenance runs only after the assistant turn completes. These checks are qualitative rather than labelled holdouts, yet they show the frozen thresholds distinguishing a realistic no-write prefix from two materially changed long-session windows. Private raw results remain under ignored validation data.

**Build:** Define the typed session-delta signal schema and run the configured decision runtime over labelled Slice 0 deltas. Persist raw per-field probabilities, model version, latency, usage, expected labels, and prompt-contract version. Compare per-turn classification with deterministic batching and hard triggers; do not connect results to runtime maintenance.

**Verify:** Measure field-level precision and recall, especially false stable judgments for corrections, decisions, constraints, commitments, and open questions. Recalculate thresholds offline from retained probabilities. Confirm that confident stable judgments update decision diagnostics without advancing map coverage, truncating the cumulative delta, or resetting hard turn/token counters. Repeat against a pinned Jev version and the fake adapter.

**Exit gate:** Predeclare acceptable miss rates and cost/latency bounds before the labelled run. If no useful operating point exists, simplify the questions, change the adapter/model, or proceed with deterministic cadence rather than building more gating logic.

### Slice 5: Offline Map-Authoring Experiment

**Hypothesis:** A generative model can propose source-supported typed patches that preserve unchanged entries and drift less than repeated prose summarization.

**Predeclared first-pass policy:** Use `gpt-mini` (`gpt-5.6-terra`) to author three sequential patch sets for each frozen representative session. Treat `research_memo_corrections` and `read_only_migration_diagnosis` as prompt-development cases, and do not inspect `goal_switch_and_cancellation` authoring output until the prompt and scorer are frozen. Every patch must pass typed schema validation, cite only canonical messages in its exact delta with the correct role, match the supplied revision and watermarks, and apply through the deterministic domain layer; any repair prompt or discarded invalid response counts as a failed patch. Promotion requires all nine patches to apply, source-reference precision of `1.00`, unchanged-entry mutation rate of `0`, stale-active rate of `0`, no unsupported lifecycle/adoption/verification promotion, final required-entry recall of at least `0.90`, and unsupported-entry rate no greater than `0.05` on the untouched case. Report map entry and rendered-character growth at every checkpoint, latency and token usage separately for authoring, and semantic-map quality independently from recovery-card continuation quality. The frozen rubric terms `superseded` and `resolved` are evaluated against their schema-equivalent inactive, terminal, or reclassified state plus revision audit history; they do not require historical tombstones in the bounded current map. This first pass may expose prompt or schema defects but cannot establish repeat-run stability.

**First-pass result:** `session-map-author-v1` failed before semantic grading and is not promotable. Through the normal OpenAI OAuth path, only the first `read_only_migration_diagnosis` patch applied. The first research-memo patch produced attention pointing at a non-active work item, and the next migration patch attempted to place the applier-owned `last_state_change_sequence_index` inside a generic update `changes` object; both were correctly rejected by the deterministic domain layer. The runner also reached the nominal holdout despite calibration failure, where streamed structured-output validation failed, so `goal_switch_and_cancellation` is now development evidence rather than an untouched authoring holdout. No response was repaired or silently discarded into a passing count. The v2 prompt may clarify the exact update allowlists and attention eligibility demonstrated by the calibration failures, but any later promotion decision requires a newly frozen holdout and an authoring output schema that makes legal update fields more discoverable than an unconstrained JSON dictionary.

The `session-map-author-v2` calibration-only rerun confirmed that prompt clarification was necessary but insufficient. All three sequential migration patches applied without repair, including the update pattern that v1 got wrong, while the first research-memo response failed streamed structured-output validation. The three valid calls consumed `7,019`, `7,435`, and `7,758` input tokens and took `15.3`, `9.5`, and `17.4` seconds, respectively. This is too much fixed schema overhead for a supposedly compact map author even before long-session growth. The next revision must expose a compact authoring proposal rather than the full persistence patch schema: the model supplies semantic entry fields and evidence sequence indexes once, while deterministic code supplies source roles, revision and coverage controls, and applier-owned bookkeeping before converting to the existing strict `MapPatchSet`. This is a contract-size and reliability redesign, not a relaxation of source or lifecycle validation.

`session-map-author-v3` implements that compact proposal boundary. It represents each entry shape once, combines addition and supersession as `put`, accepts only evidence sequence indexes, and omits persistence watermarks and entry bookkeeping from model output. Deterministic compilation resolves canonical roles, supplies revision and coverage controls, constructs the strict entry and patch types, and then runs the unchanged domain validator and applier. The serialized output schema is approximately `10,086` characters versus `17,942` for the direct `MapPatchSet`, a `44%` reduction before accounting for the newly compact prior-map projection.

The v3 calibration demonstrated the expected cost reduction: its first applicable research-memo patch used `1,570` input tokens rather than the `7,019` to `7,758` range observed for valid v2 calls. The remaining calibration failures were both entry-ID schema omissions: one overlong identifier and one identifier containing hyphens reached deterministic entry validation. `session-map-author-v4` therefore exposes the existing domain ID pattern on every model-authored entry and entry reference. It does not sanitize, truncate, or repair identifiers after generation.

V4 eliminated the ID failures and applied all three migration batches at `1,911`, `2,035`, and `2,223` input tokens. Its research response mixed a `noop` operation with real changes, which the strict domain patch correctly rejected. Because no-op is an envelope outcome rather than one operation among changes, `session-map-author-v5` removes it from the operation union: an empty proposal operation list deterministically compiles to the domain's single `NoopPatch`. This makes mixed no-op output unrepresentable without weakening the domain invariant.

V5 again applied every migration batch and applied the first research batch. The second research patch correctly identified completion of source-note collection, but the domain rejected a direct `planned` to `completed` transition. Canonical evidence can establish completion without an intermediate map revision, so the domain now permits that transition while continuing to reject reopening completed work. This is a lifecycle correction exposed by authoring, not a synthetic intermediate state or model-output repair.

After that correction, one V5 calibration run applied all three research batches but its final map exposed salience ambiguity: it retained a completed intermediate drafting step, treated the explicit final editorial review as merely planned rather than foreground work, and did not close the earlier preservation commitment. The same run failed the final migration patch because it attempted to `put` an ID already present in the current map. A focused migration repeat applied all three patches and produced a coherent compact result, proving that the duplicate-ID failure was stochastic rather than a deterministic domain defect; the passing repeat does not erase the failed first response. The frozen expected maps also use rubric-level inactive and superseded concepts that intentionally do not map one-to-one onto the live working-set representation, so authoring must be graded semantically rather than by exact entry IDs or tombstone presence.

`session-map-author-v6` addresses the general ambiguities exposed by calibration without weakening validation or repairing output. It makes the current map authoritative for identity, explicitly forbids `put` with an existing ID, requires a new ID for supersession, asks for the minimum sufficient working-set change, treats explicit verification needs as open questions and user output requirements as constraints, and treats a declared immediate next action as in-progress focus. V5 is not promotable, and the newly exposed authoring holdout remains unavailable for a future promotion claim.

The first V6 calibration run applied all six patches without repair. Research-map growth was `4`, `6`, then `10` entries and `549`, `785`, then `1,444` rendered characters; migration-map growth was `4`, `7`, then `7` entries and `695`, `1,192`, then `1,203` rendered characters. Input usage ranged from `1,985` to `2,377` tokens per call, and latency ranged from `6.56` to `14.44` seconds. Both final attention states were correct: editorial review was foreground work for the still-active memo goal, while completed migration diagnosis cleared attention. The research map retained supported outline and drafting artifacts/work that may be denser than necessary, and the migration map carried the confirmed cause through its answered verification question and completed diagnosis rather than duplicating it as a separate decision. Those are semantic compression judgments for the frozen scorer, not reasons to weaken the typed boundary or tune toward exact fixture IDs. V6 now freezes the calibration prompt; Slice 5 still requires a semantic scoring protocol, repeat-run evidence, and a new untouched holdout before any promotion claim.

The frozen semantic review protocol is evaluation-only and separates human meaning judgments from deterministic calculation. A reviewer must account for every expected concept and may map it to one or more actual entries; one compact actual entry may satisfy multiple expected concepts. Meaning, lifecycle equivalence, and provenance are judged independently, while unsupported actual entries, unsupported individual source references, stale-active entries, unsupported state promotions, unchanged-entry checks and mutations, and attention correctness are declared explicitly. Deterministic code validates that every referenced expected concept, actual entry, and source reference exists, then calculates the predeclared rates. Exact entry IDs, entry counts, and historical tombstone presence are therefore not semantic success criteria, but unsupported density still contributes to the unsupported-entry rate.

Two additional frozen-V6 calibration repeats applied all twelve first-response patches without repair and ended with correct attention. Research-map final size stabilized at eight entries in both repeats, versus ten in the first run; migration-map final size was seven in all three runs. The repeats nevertheless exposed semantic instability relevant to the recall gate: one migration run omitted the explicitly named diagnosis-report path, while another retained it, and one research run duplicated active requirements as an open commitment. V6 therefore passes structural reliability but is not promotable on semantic stability. `session-map-author-v7` adds two general working-set rules before a new holdout exists: retain concrete handoff-relevant artifact paths with honest verification state, and create a separate commitment only when actor accountability adds meaning beyond a goal, work item, or constraint, resolving any retained commitment when supported. No fixture IDs, expected entry counts, or exact expected prose are supplied to the author.

Before further prompt tuning, compare model strength under the frozen V7 contract. Run one first-response pass for `gpt-mini` (`gpt-5.6-terra`) and one for `gpt` (`gpt-5.6-sol`) over the same six calibration patches through the same OpenAI OAuth provider path. Do not retry, repair, or vary instructions between models. Compare typed application success first, then required semantic coverage, unsupported or duplicative density, lifecycle and attention correctness, entry and rendered-character growth, input/output usage, and latency. This small paired calibration comparison may select a model for repeat testing, but cannot establish production superiority or replace the new untouched holdout gate.

Both V7 model passes applied all six first responses and ended with correct attention. Terra's final research and migration maps contained `9` and `8` entries (`1,445` and `1,372` rendered characters), while Sol's contained `12` and `8` (`1,728` and `1,288`). Terra used `13,592` input and `2,909` output tokens across the six calls; Sol used `13,862` input and `3,273` output tokens. Median latency was `10.00` seconds for Terra and `11.67` for Sol, although one Terra migration call took `28.02` seconds and made its total elapsed model time higher (`76.72` versus `70.53` seconds). Sol's migration map made stronger lifecycle and outcome distinctions: it retired the completed diagnosis's read-only constraint, preserved the explicitly named report, and represented the confirmed root cause without retaining a separate candidate-cause observation. Terra's research map was more disciplined: it omitted completed intermediate drafting and generic unlocated artifacts, and it correctly marked the tool-created notes as observed; Sol retained three additional supported but arguably nonessential work/artifact entries and conservatively left the tool-created notes unverified. The paired result shows that model selection affects semantic policy: Sol favored narrative coverage and lifecycle resolution, while Terra favored a smaller working set. Neither model had unsupported hard-fact promotion in this pass, and one calibration run is insufficient to select the production author without repeat scoring and an untouched holdout.

The Sol pass also exposed duplicate state evidence when two legal operations in one patch cited the same canonical messages for one entry. Deterministic patch application now deduplicates accumulated state references by sequence index and role while preserving first-seen order and the existing sixteen-reference bound. Repeated citations therefore cannot consume an entry's bounded evidence capacity; this correction is model-independent and does not change semantic authoring policy.

The existing authoring calibration windows are intentionally small contract probes, not representative maintenance-cadence evidence. They contain only three to five canonical messages each, while the configured recovery-card compaction threshold in the development environment is `150,000` estimated tokens. A longer coherent span may improve reconciliation by exposing the complete narrative arc, resolved intermediate work, and the relative salience of early constraints; it may instead reduce evidence precision, overload structured extraction, or encourage dense historical retention. Neither outcome may be inferred from the current small-window results.

The primary Slice 5 experiment established the cumulative Jev pipeline. At each candidate interval `X`, Jev judges whether the map needs reconciliation against the entire unmapped canonical delta. A confident stable result leaves that delta pending, so the next request contains the original accepted map, all previously checked messages, and the newly completed messages. A change score crossing its calibrated threshold invokes the author over that same cumulative range. An independent maximum pending-turn or token ceiling forces authoring regardless of classifier output.

Test candidate values of `X`, field questions, and trigger thresholds first on labelled cumulative prefixes, optimizing for recall of consequential changes before avoided author calls. Compare the selected Jev gate with reconcile-every-eligible-batch and hard-cadence-only baselines. Measure false stable decisions, accumulated micro-change detection, maximum freshness lag, classifier and author call counts, cumulative classifier input growth, combined tokens and latency, and final semantic map quality. A confident stable decision never resets or truncates the source range.

Post-author Jev judging is deferred beyond the initial observe-mode slice. If live evidence later shows that deterministic validation plus authoring admits materially unsupported or incomplete maps, freeze those failures as a separate corpus and compare no judge, judge-only rejection, and one bounded judge-guided regeneration without changing the salience gate at the same time.

The existing span-size harness remains relevant for selecting the independent hard ceiling, characterizing author behavior after several skips, and diagnosing accumulation versus rebase behavior. Continue comparing small, medium, natural compaction-scale, and full-prefix inputs where useful, but do not treat deterministic span cadence as the intended runtime policy. Model and explicit-thinking comparisons should run only after the cumulative gate identifies representative authoring ranges, so author selection is evaluated under the workload Jev actually creates.

The first private span screen used the 1065 redevelopment session through its third compaction checkpoint: 375 canonical messages and approximately `204,037` projected source tokens after retaining visible user/assistant text, tool arguments, and tool results while excluding 37 persisted private-thinking parts. The three natural map intervals were approximately `58,231`, `66,296`, and `79,510` source tokens. Terra with explicit low thinking applied every first response without repair, consuming `65,319`, `73,544`, and `88,151` input tokens and producing a final 20-entry, 5,947-character map. The three calls took approximately `22.6`, `20.5`, and `18.8` seconds. This establishes that real compaction-scale canonical deltas are structurally viable and that the earlier 64-message/20,000-character author boundary was an artifact of the small fixtures, not a suitable production limit.

A single low-thinking Terra rebase from the identical 204,037-token full prefix also applied without repair, using `220,835` input and `673` output tokens in `15.1` seconds. It produced only seven entries and 1,483 rendered characters, compared with the incremental map's 20 entries and 5,947 characters. The rebase captured the immediate WSP consultant-funding handoff accurately but omitted several still-live parallel workstreams retained by the incremental map and recovery card, including detailed compensation, City-review, legal-scope, security/protection, and consultant-fallback questions. The smaller result is therefore not yet equivalent fidelity. This comparison also confounds source-span length with rebase behavior: an empty-map full audit may discard supported historical entries, while incremental patches currently lack an explicit pruning/rebase operation. Medium-span incremental runs and repeated full-prefix audits remain necessary to locate the useful boundary.

The span harness now makes map-update mode explicit and includes a `full_prefix_rebase` regime. At each selected real compaction checkpoint, that regime starts from an empty map and supplies the complete canonical prefix from sequence zero through the checkpoint watermark. Its output can therefore be compared checkpoint-for-checkpoint with the natural incremental regime, separating repeated rebase behavior from the earlier one-shot final-prefix result. This remains an experimental harness path and does not imply that production revisions should discard their prior map.

Each span artifact records the exact predecessor revision supplied to the author, true output entry counts and counts by kind, source-reference density, rendering omissions, ID-level structural churn between consecutive outputs, and whole-regime request, token, latency, and final-size totals. Structural churn is descriptive only: matching or differing generated IDs does not establish semantic equivalence, which remains a separate review judgment.

Applying those structural metrics offline to the successful 1065 outputs showed that natural incremental authoring grew from 9 to 16 to 20 entries. The second and third revisions added seven and four entries, removed none, and changed only one existing entry apiece; total evidence references grew from 26 to 62 to 80, with 56 unique referenced messages in the final map. The one-shot final-prefix rebase selected seven entries with seven references spanning only four unique messages. This does not by itself grade either map, but it shows that the present incremental author behaves conservatively by accumulation while the rebase behaves as aggressive current-state selection. Bounded maintenance therefore needs an explicit reconciliation or pruning policy if ordinary incremental patches continue to retain every still-supported entry.

Follow-up full-prefix calls for Terra at medium thinking and Sol at low thinking did not reach model-quality evaluation. Both failed with HTTP 401 after the OpenAI runtime presented an invalid service credential, even though configuration still reported `auth_mode=oauth`, OAuth connected, and API-key fallback disabled. They count as infrastructure failures and were not retried into passes; no model or thinking-effort conclusion may be drawn from them. The ignored private artifacts retain the successful maps, recovery cards, source fingerprints, usage, and the failed request diagnostics without exposing source content in repository files.

**Build:** Add `core/memory/session_map/authoring.py` to author patches from a prior structured map plus a bounded canonical delta, then validate and apply them through the domain layer. Add forced full-history audit/rebase support for experiments. Keep authoring in a harness with no post-turn hook, prompt injection, or compaction effect.

**Verify:** Compare authored maps with the separately labelled expected maps across repeated updates and supersessions. Measure unsupported sources, missed changes, accidental mutation of unchanged entries, entry growth, full-audit disagreement, lifecycle/adoption/verification promotion without new evidence, and stability across multiple runs. Test the schema and prompt independently so a poor prompt does not force premature schema expansion.

**Exit gate:** The authoring path must outperform the recovery-card baseline on durable-state preservation without unacceptable unsupported claims or map growth. Otherwise revise the schema/prompt or stop before runtime integration.

### Slice 6: Opt-In Shadow Maintenance

**Status:** The first observe-mode implementation is in place. Successful completed turns account for new canonical messages inside the chat persistence transaction, then dispatch eligible maintenance from the still-active chat task. Maintenance runs as a detached `session_memory` child through `ExecutionTaskRunner`, with a session-specific gate, global bounded-concurrency lane, timeout/cancellation cleanup, cumulative direct-change Jev classification, optional authoring, deterministic patch validation, and compare-and-swap commit. Stable decisions retain the full pending range while recording a separate decision-turn watermark so another check waits for `X` new completed turns. Append-only attempt audits retain classifier, author, usage, latency, threshold, force, outcome, and sanitized failure data. `off` remains the default, `observe` does not inject maps or alter compaction, and there is no post-author model judge or regeneration loop.

**Hypothesis:** Frequent cumulative decision checks can keep the map timely while avoiding most generative author calls, while deterministic author-output validation and observe-only operation preserve chat reliability.

**Build:** Add disabled-by-default `live_session_memory_mode` with `off` and `observe` initially, plus configurable `live_session_memory_author_model`, `live_session_memory_decision_model`, eligibility interval, direct-change thresholds, and maximum pending-turn/token ceiling. Validate generative text capability for the author and decision capability for the classifier with independent provider, credential, and resolved-model diagnostics. Add `core/memory/session_map/service.py`, compose one instance in `RuntimeContext`, and submit work through `ExecutionTaskRunner.start_background(...)` with an explicit session-memory task spec, inherited execution authority, timeout and lifecycle hooks, a keyed per-session execution gate, and the shared bounded-concurrency policy. The service freezes one cumulative source range and runs its Jev salience gate, optional author, deterministic validator, and compare-and-swap inside that tracked task. The successful-turn hook only durably advances pending counters and dispatches or coalesces work; it never calls a memory model inline and never uses a parallel background-task path. In `observe`, persist classifier decisions, validated map revisions, retry outcomes, and diagnostics without injecting the map or changing compaction output. Keep core lifecycle ownership outside generic scripts.

**Verify:** Exercise cumulative skip checks, repeated stable decisions, accumulation into a later trigger, newly introduced concepts absent from the old map, hard-ceiling authoring, successful deterministic application, rejected invalid author output, coalescing, background failures, timeout and cancellation hooks, retry/catch-up, process restart, concurrent turns, stale-task rejection, and missing or incompatible model fallback. Assert that skips advance only `decision_checked_through_sequence_index`; committed revisions alone advance map coverage and clear the covered pending range. Assert through task snapshots and validation events that classifier and author calls occur only inside the tracked session-memory execution task, concurrent work for one session queues behind its execution gate, global memory-model work respects its bounded-concurrency lane, task authority matches the originating session action, and no untracked coroutine or inline post-turn model call occurs. Compare shadow maps with forced audits and labelled samples while users continue to see existing behavior.

**Exit gate:** Shadow operation meets declared freshness, failure, cost, and audit-disagreement bounds over representative sessions. Turning the mode back to `off` stops work and leaves existing chat behavior unchanged.

### Slice 7: Opt-In Context Admission

**Hypothesis:** A bounded map plus the exact unmapped raw tail improves continuation on long and referential sessions enough to justify its prompt cost.

**Build:** Add a `context` mode that admits the latest completed map on ordinary turns while retaining every canonical message newer than its watermark exactly once. Expose bounded freshness metadata. Continue using the current recovery-card implementation for every compaction while gathering evidence for map-backed pruning.

**Verify:** Run paired continuation scenarios with and without map admission, including terse references, corrections, multiple active artifacts, stale maps, missing decision-model readiness, and disabling after a map exists. Measure task success, unsupported assumptions, prompt tokens, map/tail duplication, and latency. Add a tail-width ablation that separately scores map-only, retained-tail-only where practical, and combined effective history so compensation is visible. Assert that ordinary turns never wait for maintenance.

**Exit gate:** Context admission must improve predefined continuity measures without exceeding the map budget or increasing unsupported claims. Failure returns the mode to `observe` or `off`; compaction remains untouched.

### Slice 8: Map-Backed Pruning Experiment

**Hypothesis:** Given a fresh map, its bounded deterministic projection plus a retained canonical tail preserves continuation at least as well as the current recovery card while using less context and eliminating recursive summary drift.

**Build:** In the experimental harness, construct the current recovery-card history and the proposed map-projection-plus-tail history from identical checkpoints. Persist both as evaluation artifacts, but continue selecting the current recovery card for runtime history. Vary deterministic rendering budgets and retained-tail width without changing checkpoint or canonical transcript contracts.

**Verify:** Across at least three compactions, score active goal, focus, blocker, next action, newest lifecycle transition, volatile artifact and verification state, historical leakage, stale-state retention, unsupported claims, active-state share, and prompt size. Score the map projection by itself, retained tail by itself where practical, and their combined effective history. Inject a deliberately wrong prior recovery-card detail and confirm it cannot enter the map-backed candidate because previous cards are excluded as evidence.

**Exit gate:** Adopt map-backed pruning only if the combined projection and tail meets or beats the baseline continuation rubric, respects its context budget, and introduces no unsupported-claim regression. Otherwise retain current compaction and continue using the map, at most, for ordinary context.

### Slice 9: Opt-In Live-Memory Compaction and Hardening

**Build:** Add a `compaction` mode that prunes the covered effective-history prefix without generating a recovery card only when the feature is explicitly configured, the decision alias is compatible and ready, and a committed map revision covers the range about to leave effective history. Force or await bounded catch-up at that boundary. Render one bounded map projection and retain the configured canonical tail exactly once. On any readiness, freshness, timeout, authoring, rendering, or coverage failure, exit the live-memory path and run the existing recovery-card implementation. Preserve raw transcripts and checkpoint ownership.

**Verify:** Extend `validation/scenarios/integration/core/repeated_chat_history_compaction.py` or add a sibling scenario covering three or more compactions, correction and supersession, failure fallback, no-unmapped eviction, model readiness changes, disabling the feature, restart recovery, and contamination attempts from prior cards. Run focused checks during development and the required pre-merge profile once stable: `python validation/run_validation.py run integration/core`.

**Exit gate:** Only this slice allows map-backed pruning to replace recovery-card generation, and only in explicit `compaction` mode. Default `off` behavior and all fallback branches remain covered by the original deterministic scenario.

## Evidence and Promotion Rules

- Deterministic unit and integration checks are merge gates for every slice that changes code.
- Live TypeSafe and generative-model scenarios remain opt-in experiments; retain their structured inputs and outputs so results can be audited and thresholds recomputed.
- Define acceptance rubrics before running labelled experiments and keep calibration cases separate from final evaluation cases where the corpus size permits.
- Do not promote a shadow artifact into prompt context or a prompt artifact into compaction merely because it appears plausible in spot checks.
- Record resolved provider/model versions, prompt-contract versions, thresholds, usage, latency, and failure categories with every experimental artifact.

## Measurements

- Field-level precision and recall against hand-authored expected maps across representative sessions.
- Preservation of constraints, decisions, commitments, artifacts, and open work after repeated compactions.
- Accuracy of active-goal, active-work-item, update, temporal-validity, and unanswered-question state.
- Rate of unsupported source references and stale-writer retries.
- Map freshness lag in turns and wall-clock time.
- Prompt tokens added per ordinary turn and authoring tokens spent per completed turn.
- Authoring quality and total cost across small, medium, and natural compaction-scale source spans, including a single-pass full-prefix baseline.
- False-stable rate and consequential-change recall for cumulative per-field Jev checks at each candidate eligibility interval and trigger threshold.
- Combined classifier and author cost and freshness under cumulative Jev gating plus the hard ceiling.
- Growth of cumulative classifier input across repeated skips, including the turn at which accumulated micro-changes trigger reconciliation.
- Deterministic author-output rejection rate, retry outcomes, and semantic quality of committed shadow maps.
- Forced-audit disagreement rate with the incremental map.
- Continuation quality on terse or referential turns before and after map admission.
- Artifact-only fidelity compared with retained-tail-only and combined effective-history continuation.
- Active-state share and preservation of the newest lifecycle transition under bounded rendering.
- Proposal-to-adoption and created-to-verified promotion rates when no new supporting evidence exists.
- Recovery-card, map-projection, tail, and combined effective-history size across recursive rounds; growth is reported as a diagnostic rather than assumed to imply either accuracy or drift.

## Open Design Decisions

- Whether more than one goal may be active concurrently, and whether work items need parent-child relationships in the first slice.
- The maximum entry count and token budget for each field, including deterministic eviction or archival policy.
- Which assistant and tool statements qualify as evidence without user confirmation.
- The eligibility interval `X`, broad and per-field reconciliation thresholds, and cumulative hard turn/token ceiling.
- Whether observed author failures eventually justify a separate post-author judge experiment; this is not part of the initial live policy.
- Whether map context is injected as a system prompt part, agent instruction, or another provider-stable context layer.
- Whether map-backed pruning waits for a forced refresh inline or runs map refresh as an explicit prerequisite task.
- Whether revision history is retained indefinitely for debugging or pruned under a bounded policy.
- Whether to add a raw-content-specific session revision or use the existing broader `history_revision` and tolerate conservative stale-attempt retries.
- Which exact tagged Pydantic AI version to adopt if the post-`2.49.0` generic `DecisionModel` API ships before implementation starts.

## Research Sources

- [Towards Scalable Multi-domain Conversational Agents: The Schema-Guided Dialogue Dataset](https://arxiv.org/abs/1909.05855)
- [Cognitive Architectures for Language Agents](https://arxiv.org/abs/2309.02427)
- [MemGPT: Towards LLMs as Operating Systems](https://arxiv.org/abs/2310.08560)
- [Generative Agents: Interactive Simulacra of Human Behavior](https://arxiv.org/abs/2304.03442)
- [LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory](https://arxiv.org/abs/2410.10813)
- [Evaluating Very Long-Term Conversational Memory of LLM Agents](https://arxiv.org/abs/2402.17753)
- [Zep: A Temporal Knowledge Graph Architecture for Agent Memory](https://arxiv.org/abs/2501.13956)
- [Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory](https://arxiv.org/abs/2504.19413)

## Next Phase

Complete Slice 6 validation by running `observe` through normal application chat paths with the configured Jev and author models, then inspect the durable attempt audit for trigger precision, cumulative-input growth, author-call rate, latency, cost, failures, and map quality. Define the shadow exit bounds before tuning eligibility, thresholds, or hard ceilings, and add deterministic coverage for the remaining failure, timeout, cancellation, restart, hard-ceiling, and stale-writer contracts exposed by those runs. Do not begin context admission, add a post-author model judge or regeneration loop, or change compaction behavior until shadow operation meets the declared bounds.
