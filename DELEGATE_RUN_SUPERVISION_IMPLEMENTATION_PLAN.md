# Delegate Run Supervision Implementation Plan

## Status

Implemented on `dev/delegate-run-supervision`. Focused delegate, job, execution-task runner, and settings-upgrade scenarios pass; the maintainer-owned full validation profile remains pending.

## Purpose

Replace the delegate child tool-call ceiling with observable, cancellable execution tasks so healthy long-running delegates can continue regardless of how many useful tools they call. Give parent agents one compact `job` tool for listing, inspecting, waiting on, and cancelling managed runtime jobs while preserving the existing blocking delegate contract for authored scripts and focused calls.

## Decisions

- Remove `delegate_tool_calls_limit` from the product contract and runtime rather than changing its default, because persisted values would otherwise keep enforcing the old ceiling after an upgrade.
- Register every delegate child run with `ExecutionTaskRunner`, including blocking delegates, so lifecycle attachment, lineage, cancellation, progress, and terminal results have one implementation.
- Keep `delegate(...)` blocking by default and add `mode="managed"` as an explicit opt-in that returns a job handle backed by an execution task immediately.
- Retain `parent_task_id` as lineage, not lifetime ownership: managed delegates detach from every launching-task terminal transition, while blocking delegates remain lifecycle-attached.
- Add one model-facing `job` tool with `list`, `status`, `wait`, and `cancel` operations; do not add separate lifecycle tools for delegates, workflows, or ingestion.
- Use `job` and `job_id` only at the model-facing boundary to distinguish concrete asynchronous runs from goals and conceptual tasks; retain the established execution-task names inside the runtime and API implementation.
- Treat `wait` as an optional dependency-barrier tool, not an automatic next action after delegation.
- Make waits event-driven and return when any selected task becomes terminal or requests attention; return current snapshots normally when the wait times out.
- Allow `job(operation="wait")` without job IDs as a general timer that suspends the active agent task without ending its turn.
- Return a completed task's bounded result from both `status` and `wait` so the parent does not need a second collection call and no mailbox subsystem is required.
- Preserve process-local task semantics for this effort. Managed delegate tasks do not survive runtime restart and must not be represented as durable workflows.
- Retain `delegate_model_requests_limit`, `delegate_repeated_failure_limit`, and `delegate_timeout_seconds` as independent safeguards in this effort; `0` continues to disable the request or timeout ceiling.
- Add a delegate concurrency limit as the primary resource-expansion control after removing the cumulative tool-call limit.
- Do not add mid-turn user-message wakeups or a parent/child message queue.

## User-Visible Contract

### Delegate launch

Existing blocking calls continue to return the completed child output:

```python
result = await delegate(
    prompt="Research the selected sources.",
    tools=["web_search", "web_extract", "file_write"],
)
```

Managed calls return promptly with a process-local job handle:

```python
run = await delegate(
    prompt="Research the selected sources.",
    tools=["web_search", "web_extract", "file_write"],
    mode="managed",
)
```

The managed return value must identify the job ID, job kind, initial status, and process-local retention semantics. The public job ID is an opaque alias for the underlying coordinator task ID. The return value must not claim that the delegate result is already available.

### Job tool

The tool has one operation-discriminated schema:

```python
await job(operation="list", kind="delegate", include_terminal=False)
await job(operation="status", job_ids=["job_a", "job_b"])
await job(operation="wait", job_ids=["job_a", "job_b"], timeout_seconds=300)
await job(operation="wait", timeout_seconds=60)
await job(operation="cancel", job_ids=["job_b"])
```

- `list` returns compact accessible task summaries and omits terminal result bodies.
- `status` returns current accessible job snapshots and includes bounded terminal results when present.
- `wait` snapshots and subscribes atomically, returns immediately for already terminal or attention-required tasks, otherwise returns on the first relevant selected-task change or timeout, and includes bounded terminal results when present.
- `wait` with no job IDs acts as a timer and returns a normal timed-out/timer-completed response after the requested duration.
- `cancel` requests cancellation for each accessible selected task and returns an outcome for every supplied ID.
- Unknown and inaccessible job IDs are indistinguishable to the caller.
- Wait timeout is an observation outcome with `timed_out: true`, not a failed tool result.

### Job snapshot

The model-facing snapshot contains only bounded, JSON-safe fields:

```text
job_id
kind
label
status
parent_job_id
detached_from_parent_lifecycle
created_at
started_at
finished_at
cancel_requested
terminal_reason
revision
last_progress_at
health_status
active_tools
recent_activity
aggregate usage and tool-call counters
terminal result when requested and available
```

Argument hints in live activity must use the existing delegate audit sanitization and truncation rules. Full tool arguments, full tool results, provider-native messages, credentials, and arbitrary dependency objects must not enter model-facing job snapshots.

## Runtime Invariants

- A managed delegate never retains the parent Pydantic `RunContext` after the launch tool returns.
- A delegate launch snapshots only immutable or independently owned inputs needed by the child run.
- Every delegate emits exactly one terminal execution-task state and one existing delegate terminal lifecycle event.
- Managed delegates are first-class detached runner tasks. Launching-task completion, failure, cancellation, timeout, or skip does not stop them.
- Blocking delegates remain attached to their parent lifecycle, and parent cancellation waits for their cleanup.
- Explicit job or scope cancellation and runtime shutdown stop active managed delegates.
- Background reservation, parent transition, and shutdown admission races cannot leave an execution task permanently queued or running.
- Cancelling one managed delegate does not cancel its parent or sibling tasks.
- Blocking delegate cancellation propagates from the parent to its separately owned child task and waits for child cleanup.
- A wait never busy-polls and never consumes a model request until its event condition or timeout returns control to the model.
- Task results remain bounded and process-local and expire with existing terminal-task retention.
- Delegate tool-call count remains observable but is never an enforcement input.
- Delegate children cannot call `delegate`, `code_execution`, or `job`.
- Authority checks apply to list, status, wait, cancellation, and terminal result access.

## Current Architecture to Reuse

- `core/runtime/execution_tasks.py` already owns process-local task identity, lifecycle states, authority, metadata, heartbeats, terminal retention, and cancellation handles.
- `core/runtime/task_runner.py` already starts isolated background tasks, attaches queued records, applies timeouts, and runs domain hooks.
- `core/runtime/background.py` already creates runtime-owned tasks with a fresh `contextvars.Context`, which is the correct isolation boundary for managed delegates.
- `core/runtime/task_access.py` already centralizes authority-filtered task reads and cancellation.
- `core/chat/task_events.py` provides the existing race-safe pattern for buffered state, change signals, and async subscribers.
- `core/llm/agents.py` already collects streamed Pydantic agent output and preserves the latest partial messages in `finally` cleanup.
- `core/tools/delegate.py` already resolves child models and tools, applies child instructions, classifies failures, records bounded audits, and logs delegate lifecycle events.

## Detailed Implementation

### 1. Define observable execution-task state

- Add `ExecutionTaskKind.DELEGATE` and stable helpers for delegate parent scope and labels.
- Add first-class `parent_task_id`, monotonically increasing `revision`, bounded terminal `result`, and task-change signaling to coordinator records and snapshots.
- Increment `revision` for lifecycle changes, cancellation requests, metadata changes, progress updates, attention changes, and terminal result publication.
- Add a race-safe coordinator wait method that validates the initial state and captures the relevant change signals under the coordinator lock before awaiting.
- Support waiting on multiple task IDs and return when any selected task is terminal or has `health_status="attention_required"`.
- Add coordinator methods for recording a bounded terminal result and listing active children by `parent_task_id`.
- Give child tasks an explicit lifecycle-attachment policy. Close attached-child admission atomically when a parent becomes terminal, cancel only attached descendants, and do not await child cancellation while holding the coordinator lock.
- Keep result retention aligned with the existing bounded terminal-task history and add an explicit result-size bound with a `truncated` marker and preserved artifact references.

### 2. Extend authority-mediated task access

- Add `wait_for_tasks` and result-aware snapshot projection to `ExecutionTaskAccessService`.
- Resolve and authorize all requested task IDs before subscribing while concealing inaccessible tasks as not found.
- Recheck authorization and state after wakeup before returning results.
- Keep bulk cancellation best-effort per task and return deterministic per-ID outcomes.

### 3. Add the `job` tool

- Implement a built-in `job` tool whose strict operation schema covers `list`, `status`, `wait`, and `cancel` without exposing unrelated task-runner internals.
- Use a default wait of 30 seconds, a minimum positive task wait of 10 seconds to discourage busy polling, and a maximum wait of 3,600 seconds; keep these as runtime constants for the first version rather than adding more general settings.
- Permit a zero-second status check only through `operation="status"`; reject attempts to turn `wait` into tight polling.
- On job-wait timeout, return `timed_out: true` plus the latest compact snapshots for the selected jobs.
- On timer-only wait completion, return the elapsed duration and no fabricated task state.
- Register the tool for primary chat and task-owned authored execution, but exclude it from delegate child bindings.
- Add concise model instructions: start independent work early, continue useful non-overlapping work, call wait only at a dependency barrier, prefer longer waits, return active managed job IDs before ending a turn, and cancel work that is no longer needed.

### 4. Create an immutable delegate launch boundary

- Add a frozen `DelegateLaunchSpec` containing normalized prompt, instructions, model choice, tool names, thinking choice, vault path, week-start day, session ID, execution authority, parent task ID, and effective remaining guardrails.
- Construct the launch spec while the parent `RunContext` is valid and do not retain `ctx`, `ctx.deps`, a parent agent, or a parent model connection.
- Require a current execution-task identity for `mode="managed"`; preserve blocking behavior or return a clear structured failure on surfaces that cannot establish parent lineage.
- Create and bind the child Pydantic agent inside the background delegate coroutine.
- Run both blocking and managed modes as separate execution tasks; blocking mode awaits the child task internally and adapts its terminal result back to the existing `ToolReturn` contract.

### 5. Capture Pydantic AI progress

- Continue using streaming transport because the ChatGPT/Codex OAuth backend requires it.
- Add a lightweight Pydantic `event_stream_handler` to the child run and observe model stream activity, `FunctionToolCallEvent`, and `FunctionToolResultEvent` without changing child tool semantics.
- Track active tool calls by tool-call ID because Pydantic may execute multiple child calls concurrently.
- Record aggregate started, completed, failed, invalid, and unsettled call counts.
- Maintain a bounded recent-activity ring with tool name, sanitized argument hint, start time, finish time, duration, and outcome.
- Update `last_progress_at` on model activity, tool start, and tool completion.
- Publish `attention_required` only for concrete runtime signals such as the repeated-failure guard opening or a classified child failure requiring intervention. Expose each active tool's start time so the parent can judge its duration without a speculative health transition.
- Do not automatically classify or cancel a delegate solely because no Pydantic event arrived while a tool remains active.
- Preserve the existing final audit and partial-output handoff on failures and cancellation.

### 6. Store and deliver delegate results

- Normalize completed, failed, timed-out, and cancelled child outcomes into one JSON-safe delegate task result envelope.
- Include the existing return text, metadata, audit, usage, partial output, failure classification, and artifact references where applicable.
- Record the result before marking the execution task terminal so a waiter cannot observe terminal state without its result.
- Make result recording and terminal transition atomic from a waiter's perspective.
- Return the same normalized envelope from blocking delegate mode, managed `job(status)`, and managed `job(wait)`.
- Do not persist Pydantic `AgentRunResult`, `RunContext`, agent instances, provider-native message history, or live stream objects in coordinator state.

### 7. Implement cancellation and lifecycle attachment

- Use the runner's separate `asyncio.Task` handle as the cancellation mechanism supported by pinned Pydantic AI `2.19.0`; do not require the newer `CancellationToken` API.
- On cancellation, await child unwinding, capture the latest `AgentRunProgress`, record a bounded cancelled result, emit `delegate_cancelled`, and re-raise cancellation to the task runner.
- For blocking mode, catch parent cancellation around the internal child wait, cancel the child task, await its cleanup, and then re-raise parent cancellation.
- Mark managed delegates as detached background tasks while retaining parent lineage for provenance and grouping; skip them on every parent terminal transition.
- Make background reservation interruption-safe, close attached-child admission atomically with parent transitions, reject detachment for inline tasks, and close all task admission during runtime shutdown.
- Preserve direct cancellation of an individual child through `job(operation="cancel")` without cascading upward or sideways.

### 8. Replace the tool-call ceiling with concurrency control

- Remove `delegate_tool_calls_limit` from `core/settings/settings.template.yaml` and all settings/UI projections.
- Remove `get_delegate_tool_calls_limit()`, `DELEGATE_DEFAULT_MAX_TOOL_CALLS`, tool-call `UsageLimits`, tool-limit flight-card text, tool-limit failure classification, and `max_tool_calls` result/log metadata.
- Continue counting child tool calls only for progress, auditing, and observability.
- Preserve explicit `request_limit=None` when `delegate_model_requests_limit` is disabled so Pydantic AI's constructor default does not silently introduce a request ceiling.
- Add `max_concurrent_delegates` as a general setting with a conservative default of `3` and `0` meaning unlimited.
- Enforce delegate concurrency in a shared delegate execution service or runner policy rather than `Agent(max_concurrency=...)`, because AssistantMD creates a distinct Pydantic agent per delegate run.
- Represent concurrency-delayed delegate tasks as queued and publish queue position or an equivalent compact reason.
- Apply the same concurrency lane to blocking and managed delegates.
- Treat stale `delegate_tool_calls_limit` entries in existing `system/settings.yaml` as ignored immediately after upgrade; the existing settings repair path removes the physical key because it is absent from the template.

### 9. Preserve API and UI task visibility

- Extend `ExecutionTaskInfo` and API projection with `parent_task_id`, `detached_from_parent_lifecycle`, `revision`, `last_heartbeat_at`, and `heartbeat_status` so existing task inspection can represent managed delegates.
- Keep full terminal result bodies out of task list responses; expose them through the model-facing authority service first and add an API result endpoint only if the UI needs it during implementation.
- Ensure active chat-task lookup cannot confuse child delegate tasks with their parent by giving delegates a distinct scope and kind.
- Add UI treatment only where current generic task status surfaces already render tasks; do not build a separate delegate dashboard in this effort.

### 10. Keep the execution abstraction extensible

- Make `job` operate on generic coordinator task IDs and kinds through public job handles rather than delegate-specific handles.
- Existing running workflow and ingestion execution tasks become inspectable, waitable, and cancellable through the same authority path when the tool caller can access their public job IDs.
- Do not claim support for a durable ingestion job that exists only as a queued database row and has not yet been registered with `TaskCoordinator`.
- Record a follow-up design task for creating ingestion execution-task identity at enqueue time or adapting durable ingestion job IDs into the execution observation contract; that work requires restart reconciliation and is outside this delegate-focused implementation.

## Settings and Persistent Runtime State

- This effort changes the settings contract by removing `delegate_tool_calls_limit` and adding `max_concurrent_delegates`.
- Existing configured data and system roots must be treated as persistent during all local checks.
- The implementation must never overwrite populated `system/secrets.yaml` or reset user settings.
- Existing active settings files may retain an ignored stale tool-limit entry until settings repair; runtime behavior must not consult it.
- The new concurrency setting must use its template fallback before settings repair so upgraded installations receive the intended default immediately.
- No delegate task or result durability is added to repository `data/`, repository `system/`, `/app/data`, or `/app/system`.

## Documentation and Architecture Records

- Add a new ADR that supersedes the tool-call-limit portion of ADR 0033 while retaining its repeated-failure, audit, and partial-handoff decisions.
- Update `docs/tools/delegate.md` to describe blocking and managed modes, removal of the tool-call ceiling, execution-task lineage and lifecycle attachment, result retention, and remaining request/timeout guardrails as current behavior only.
- Add `docs/tools/job.md` for the four operations, timer behavior, wait semantics, process-local retention, authority, and examples.
- Update the architecture map and tool documentation index where execution-task lineage, lifecycle attachment, and tool routing are listed.
- Update authoring guidance to preserve blocking delegate examples and explain when managed mode is appropriate.
- Keep product documentation free of migration narrative; put historical rationale in the ADR and release/review material.

## Validation-First Workflow

### Scenario targets

- Extend `validation/scenarios/integration/core/execution_task_runner.py` with failing assertions for revision changes, race-free wait, timeout snapshots, bounded results, attached-child cancellation, detached-child survival, interruption-safe reservation, shutdown admission closure, authority isolation, and result-before-terminal ordering.
- Extend `validation/scenarios/integration/core/delegate_tool.py` with failing assertions for managed launch handles, cross-turn survival, blocking compatibility, execution-task registration, progress projection, cancellation cleanup, terminal result delivery, and successful completion after more than 32 deterministic child tool calls.
- Add `validation/scenarios/integration/core/job_tool.py` for list, status, wait-any, already-terminal return, timer-only wait, timeout-as-success, bulk cancellation, inaccessible-job concealment, and delegate-child exclusion.
- Extend the settings/template integration scenario to assert that `delegate_tool_calls_limit` is absent, stale values are ignored and pruned by repair, and `max_concurrent_delegates` is available from the template fallback on upgraded settings.
- Use deterministic test models and synthetic tool events; do not assert on free-form model prose or call external services.

### Stable event contracts

- Preserve `delegate_started`, `delegate_completed`, `delegate_failed`, and `delegate_cancelled`; add `task_id`, `parent_task_id`, and `mode` to their minimum payload where applicable.
- Add `delegate_attention_required` only when the health state first crosses into attention-required, with minimum payload `task_id`, `parent_task_id`, `reason`, `active_tool_names`, and aggregate call counts.
- Add `execution_task_result_recorded` at the decision boundary where a bounded result becomes observable, with minimum payload `task_id`, `kind`, `status`, `result_chars`, `truncated`, and `artifact_reference_count`.
- Add `delegate_concurrency_queued` when a delegate waits for the shared concurrency lane, with minimum payload `task_id`, `parent_task_id`, and `queue_position` when available.
- Avoid per-token and per-tool-call validation log events; live progress belongs in task state, while validation logs cover lifecycle and decision boundaries.

### Agent-owned verification

- Run quick isolated smoke checks against temporary data and system roots for task wait races, timer behavior, cancellation during an active tool, and result truncation.
- Run the affected individual deterministic scenarios directly as implementation slices land.
- Run the production Python quality gate required by the coding standards before review handoff.
- Do not run the full validation suite; request the maintainer result for `python validation/run_validation.py run integration/core` before merge.

## Testable Delivery Slices

Each slice starts with a deterministic failing scenario, adds only the production behavior needed for that scenario, and ends with its affected scenarios passing. A slice must not rely on an untested behavior deferred to a later slice. The model-facing `job` tool remains unavailable until its registration slice is complete, and the delegate tool-call ceiling remains enforced until supervision, cancellation, and result collection are working end to end.

### Slice 1: Observable execution-task substrate

**Outcome:** Existing execution tasks can publish revisioned progress and bounded terminal results, and callers can wait for a state change without polling.

**Assertions first:** Extend `validation/scenarios/integration/core/execution_task_runner.py` for revision increments, result-before-terminal visibility, result truncation, artifact-reference preservation, already-terminal waits, change-driven waits, timeout snapshots, and the subscribe-versus-complete race.

**Production boundary:** Extend coordinator records and snapshots with `revision`, `result`, progress timestamps, health state, and change signaling; add atomic result-plus-terminal publication and race-safe multi-task wait primitives; project the new non-result fields through the existing API model.

**Complete when:** The focused execution-task runner scenario passes, a temporary-root smoke check proves a waiter cannot miss a concurrent completion, and existing task list/get/cancel behavior remains unchanged.

### Slice 2: Generic `job` observation and control

**Outcome:** A primary agent can list, inspect, wait for, and cancel authorized existing runtime jobs, or use `wait` as a plain timer.

**Assertions first:** Add `validation/scenarios/integration/core/job_tool.py` for schema validation, list, status with terminal result, wait-any, already-terminal return, timer-only wait, timeout-as-success, cancellation outcomes, inaccessible-job concealment, bounded responses, and delegate-child exclusion.

**Production boundary:** Extend `ExecutionTaskAccessService`, implement the operation-discriminated `job` tool, map public `job_id` values to coordinator task IDs, register the tool only on eligible parent surfaces, and add the concise wait-use instructions.

**Complete when:** The job-tool scenario passes using synthetic execution tasks, timer wait consumes no model request while suspended, cancellation remains authority checked, and no delegate code depends on the new tool yet.

### Slice 3: Delegate execution-task parity in blocking mode

**Outcome:** An ordinary blocking `delegate(...)` call runs its child through `ExecutionTaskRunner` while preserving its current caller-visible return and failure behavior.

**Assertions first:** Extend `validation/scenarios/integration/core/delegate_tool.py` to prove blocking return parity, delegate task registration and terminal state, exactly one lifecycle event, timeout classification, partial audit preservation, and exclusion of `delegate`, `code_execution`, and `job` from child bindings.

**Production boundary:** Add `ExecutionTaskKind.DELEGATE`, introduce the frozen `DelegateLaunchSpec`, construct the child agent only inside its isolated runner coroutine, normalize terminal result envelopes, and adapt the blocking call to await that child task.

**Complete when:** Existing blocking delegate cases and the new parity assertions pass, no background object retains the parent `RunContext`, and task state never exposes provider-native or dependency objects.

### Slice 4: Managed delegate end-to-end

**Outcome:** `delegate(..., mode="managed")` returns immediately with a job handle whose completion and bounded result can be observed through `job(status)` and `job(wait)`.

**Assertions first:** Add deterministic cases for immediate managed return, valid public job fields, queued/running/terminal transitions, successful result collection through both status and wait, wait timeout followed by later completion, blocking and managed envelope equivalence, and rejection when no launching execution-task identity exists.

**Production boundary:** Add the delegate mode schema and launch path, attach parent lineage and authority to child records, expose delegate terminal results through the job projection, and retain blocking mode as the default.

**Complete when:** A parent can launch a delayed synthetic delegate, end its turn while the child remains active, and receive the result in a later turn without polling or a mailbox.

### Slice 5: Live delegate progress and health visibility

**Outcome:** Job snapshots show enough bounded Pydantic activity to distinguish useful progress from a run that may need attention.

**Assertions first:** Add synthetic Pydantic event cases for model activity, concurrent tool calls keyed by call ID, tool completion and failure, active-tool projection, bounded recent-activity eviction, sanitized argument hints, aggregate counters, and one-shot transition to `attention_required`.

**Production boundary:** Add the child `event_stream_handler`, progress accumulator, bounded recent-activity ring, coordinator progress publication, and attention event; do not alter child tool execution semantics or auto-cancel solely because an active tool is old.

**Complete when:** A deterministic web-search/read/extract-style sequence is visible in order through `job(status)`, concurrent calls settle correctly, sensitive or oversized arguments are absent, and the focused scenarios pass.

### Slice 6: Cancellation and lifecycle closure

**Outcome:** Managed delegates remain independent across launching-task terminal transitions, attached blocking children still clean up with their parent, and explicit cancellation remains reliable.

**Assertions first:** Add cases for direct job cancellation during an active child tool, blocking-parent cancellation, managed-parent completion/failure/cancellation/timeout/skip, sibling isolation, interrupted background reservation, late child admission, runtime shutdown admission closure, latest partial-result capture, and cancellation requested during terminal transition.

**Production boundary:** Add explicit lifecycle attachment, cancel only attached descendants on parent terminal paths, keep managed descendants detached, make reservation and admission race-safe, await Pydantic unwinding for blocking parent cancellation through the runner-owned `asyncio.Task`, publish the cancelled result before child terminal visibility, and avoid awaiting cancellation while holding coordinator locks.

**Complete when:** All cancellation cases deterministically converge with no stranded reservations, managed jobs remain observable across turns, runtime shutdown cancels all active jobs, exactly one child terminal event is emitted, and cancellation never propagates upward or sideways.

### Slice 7: Remove the delegate tool-call ceiling

**Outcome:** Healthy delegates may make any number of useful tool calls, while request, repeated-failure, and timeout guardrails remain effective.

**Assertions first:** Add a deterministic delegate that completes after more than 32 successful child tool calls; add settings cases proving the removed key is absent from the template, ignored when stale in active settings, and pruned by settings repair; retain coverage for request-limit and repeated-failure enforcement.

**Production boundary:** Remove `delegate_tool_calls_limit`, its getter and fallback constant, Pydantic tool-call enforcement, flight-card text, failure classification, and result/log limit metadata; retain tool-call counts only as progress and audit data; explicitly pass `request_limit=None` when that independent limit is disabled.

**Complete when:** The greater-than-32-call delegate completes, a stale configured value cannot affect runtime behavior after restart, settings repair removes it, and the remaining guardrail assertions pass.

### Slice 8: Delegate concurrency control

**Outcome:** Resource expansion is controlled by concurrent delegate count rather than cumulative useful work.

**Assertions first:** Add settings and delegate cases for default concurrency `3`, `0` as unlimited, FIFO or otherwise documented deterministic admission, queued job visibility, queue cancellation, slot release after every terminal outcome, and equal treatment of blocking and managed delegates.

**Production boundary:** Add `max_concurrent_delegates`, enforce it in the shared delegate execution path, project a compact queued reason and queue position when available, and emit `delegate_concurrency_queued` only at the queueing decision boundary.

**Complete when:** A deterministic burst never exceeds the configured concurrency, cancelled or failed children release slots, unlimited mode admits the full burst, and upgraded settings use the template fallback without repair.

### Slice 9: Contract alignment and review handoff

**Outcome:** API projections, current-contract documentation, architecture records, and validation evidence describe the shipped behavior consistently.

**Assertions first:** Add or finish API projection assertions for delegate kind, parent identity, revision, heartbeat/health state, omission of terminal result bodies from list responses, and protection against child tasks being mistaken for active chat tasks.

**Production boundary:** Complete only the necessary existing UI/API task-status integration, add the superseding ADR, update delegate and job tool documentation plus the architecture/tool indexes, and remove temporary compatibility code or duplicated paths found during the preceding slices.

**Complete when:** All affected individual scenarios pass, temporary-root smoke checks pass, the production Python quality gate and `git diff --check` pass, and the maintainer is given the exact request to run `python validation/run_validation.py run integration/core` without the agent running the full suite.

## Out of Scope

- Durable delegate resumption after process restart.
- Injecting new user messages into an active waiting parent turn.
- Parent/child or sibling message queues.
- Recursive delegation by delegate children.
- Automatic replay of cancelled, timed-out, or failed child tool calls.
- Provider-native async function-call integration that would behave differently across model providers.
- A dedicated delegate monitoring dashboard.
- Promotion of queued durable ingestion database jobs into execution tasks before the ingestion worker claims them.

## Review Handoff

The implementation is ready for maintainer review. Before merge, maintainers should run `python validation/run_validation.py run integration/core`; agents have intentionally limited local validation to the affected individual deterministic scenarios.
