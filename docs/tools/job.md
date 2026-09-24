# `job`

## Purpose

List, inspect, wait for, and cancel accessible process-local asynchronous jobs through one supervision tool. A wait without job IDs acts as a general timer that keeps the current agent turn active.

## When To Use

- a managed delegate or another asynchronous operation returned a job ID
- the next step depends on one or more job results
- a running job needs a health or progress check
- the user asks to stop an asynchronous job
- the agent needs a bounded delay before checking external state again

Start independent work early and continue useful non-overlapping work while jobs run. `wait` is a dependency barrier, not an automatic follow-up to every launch. When the current request depends on job results, wait on all relevant job IDs and integrate their terminal results before answering. Prefer one useful longer wait over repeated short polling; a wait timeout is not completion, so wait again while required work remains healthy and continues making progress. Leave required jobs active across turns only when the user requested background work or completion requires new user input. In either case, provide the job IDs and current status, and never present unfinished work as complete. Cancel work that is no longer needed.

## Arguments

- `operation`: required; one of `list`, `status`, `wait`, or `cancel`
- `job_ids`: job IDs for `status`, `wait`, or `cancel`; omit only for `list` or a timer-only `wait`
- `kind`: optional job-kind filter for `list`, such as `delegate`
- `include_terminal`: whether `list` includes recently retained terminal jobs; defaults to `true`
- `timeout_seconds`: duration for `wait`, from 10 through 3,600 seconds; defaults to 30 seconds

## Examples

```python
await job(operation="list", kind="delegate", include_terminal=False)
```

```python
await job(operation="status", job_ids=["task_abc"])
```

```python
await job(operation="wait", job_ids=["task_abc", "task_def"], timeout_seconds=300)
```

```python
await job(operation="wait", timeout_seconds=60)
```

```python
await job(operation="cancel", job_ids=["task_def"])
```

## Output Shape

The tool returns JSON text. Job snapshots contain bounded JSON-safe fields including identity, kind, status, parent job, whether the job is detached from its parent's lifecycle, timestamps, cancellation state, terminal reason, revision, health, queue state, active tools, recent activity, and aggregate tool-call counts.

`list` returns at most the 50 most recently created matching summaries, reports the total and whether the response was truncated, and omits detailed activity and result bodies. `status` includes bounded activity and a bounded result when available. `wait` returns when a selected job becomes terminal or requests attention, or when the wait duration expires; it includes current snapshots and sets `timed_out` for a normal observation timeout. Timer-only waits return `timer_completed: true` after the requested duration.

`status`, `wait`, and `cancel` accept at most 20 job IDs per call. `cancel` reports the current snapshot and whether the request was effective for each supplied ID. Unknown and inaccessible IDs both return `outcome: "not_found"` so identifiers cannot bypass task authority.

## Notes

- jobs and their results are process-local coordination state, not durable workflow or ingestion history
- a runtime restart discards managed delegate job state
- managed delegates are detached runner tasks: completion, failure, cancellation, timeout, or skip of the launching task does not stop them
- explicit job or scope cancellation and runtime shutdown still stop active managed delegates
- `last_progress_at`, active tool names, and recent tool outcomes help infer forward progress but are not semantic health proofs
- waiting is event-driven and does not consume a model request while suspended
- use `status` for an immediate check; the minimum `wait` duration prevents tight polling
- cancellation is cooperative and cannot undo external effects completed before the request
