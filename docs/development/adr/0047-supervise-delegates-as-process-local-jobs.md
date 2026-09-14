# 0047 - Supervise Delegates As Process-Local Jobs

## Status

Accepted.

Supersedes the delegate tool-call limit and non-observable progress portions of [0033 - Bound Delegate Child Execution And Preserve Failure Handoffs](0033-bound-delegate-child-execution.md).

Extends [0019 - Centralize Runtime Execution Task Running](0019-runtime-execution-task-runner.md) by applying the shared runner to delegate children and exposing authority-mediated job supervision.

## Context

A fixed cumulative child tool-call ceiling treats useful research activity and runaway repetition alike. Long but healthy delegates can cross that ceiling after substantial model and tool work, forcing the parent to retry or repartition completed work and increasing latency and cost. Raising a persisted default does not change upgraded installations that retain the prior value.

The runtime already has process-local execution task identity, authority, cancellation, background isolation, retention, and lifecycle state. Delegate children previously ran outside that common task runner, so the parent could neither detach from a long run nor observe concrete progress before completion.

Waiting is also useful beyond delegation. A parent may need to suspend until a queued or external operation changes while retaining the option to do independent work first. Mid-turn user-message wakeups would require a distinct mailbox and continuation contract and are not needed for task supervision.

## Decision

Run every delegate child as a parent-owned process-local execution task through `ExecutionTaskRunner`. Preserve blocking delegation by awaiting the child task internally, and add explicit managed mode that returns a job handle immediately. A frozen launch specification captures only owned values needed by the child and never retains the parent Pydantic `RunContext`.

Remove `delegate_tool_calls_limit` from settings and runtime enforcement. Continue observing tool-call counts and retain the independent model-request, repeated-failure, and cooperative timeout guardrails. Bound aggregate resource expansion with `max_concurrent_delegates`; excess children remain queued execution tasks rather than failing.

Extend execution-task snapshots with parent identity, revision, last progress, health, bounded results, and bounded progress metadata. Observe Pydantic AI function-call and result events by call ID so concurrent tools are represented correctly. Active tool names, recent sanitised activity, and aggregate counts provide evidence of progress without storing full arguments, results, provider messages, agents, or stream objects. Lack of an event alone does not make a running tool unhealthy or trigger cancellation.

Expose one model-facing `job` tool for list, status, event-driven wait, and cancellation. A wait returns on terminal or attention-required state and returns current snapshots normally on timeout. A wait without IDs is a general timer. Result delivery uses the task record, not a mailbox, and mid-turn user messages do not wake waits.

Task access remains authority-mediated, and unknown and inaccessible job IDs are indistinguishable. Parent terminal transitions request cancellation of active descendants, while cancelling one child does not affect its parent or siblings. Managed delegate records and results follow existing process-local retention and do not claim restart durability.

## Consequences

- Healthy research delegates can make as many useful child tool calls as their task requires without failing at an arbitrary cumulative threshold.
- Parents can launch independent work, continue other work, inspect concrete tool progress, wait only at dependency barriers, and cancel unhealthy runs.
- Delegate concurrency, model requests, repeated failures, and timeouts remain separate controls with different operational purposes.
- Progress signals support a reasonable inference that work is moving but cannot prove the semantic quality of the child result.
- Process restart ends managed delegate jobs and clears their retained results; durable or resumable delegation would require a separate domain contract.
- Cooperative cancellation cannot provide a hard wall against blocking or cancellation-suppressing third-party code or reverse effects already committed by tools.

## Evidence

- Current system map: `docs/development/architecture.md`; tool contracts: `docs/tools/delegate.md` and `docs/tools/job.md`
- Runtime implementation: `core/runtime/execution_tasks.py`, `core/runtime/task_access.py`, and `core/runtime/task_runner.py`
- Delegate integration: `core/tools/delegate.py`, `core/tools/job.py`, and `core/llm/agents.py`
- Validation: `validation/scenarios/integration/core/delegate_tool.py`, `validation/scenarios/integration/core/execution_task_runner.py`, `validation/scenarios/integration/core/job_tool.py`, and `validation/scenarios/integration/core/system_template_seed_refresh.py`
