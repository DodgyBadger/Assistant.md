# 0026 - Persist Workflow Outcomes As Domain History

## Status

Accepted.

Complements
[0004 - Track Long Running Work With Process Local Execution Tasks](0004-process-local-execution-tasks.md)
and [0014 - Govern Workflow Execution Through Vault Lanes](0014-workflow-governor-vault-lanes.md).

## Context

Execution tasks provide live status and cancellation, but their terminal history
is bounded and process-local. APScheduler persists future jobs, not authoritative
workflow results. Vault Activity persists attributed file mutations, but a
workflow can complete, skip, time out, or fail without changing a file.

Users need durable answers about whether a workflow ran and what happened,
including after application restart. Scheduler-level completion is insufficient
because an executed Python job may return a failed workflow domain result
without raising an exception.

## Decision

Persist workflow attempts and terminal outcomes in a subsystem-owned
`workflow_runs.db` ledger.

`WorkflowGovernor` creates a queued run before waiting for execution lanes,
marks it running when execution begins, and records the authoritative terminal
status from the workflow result or failure path. API, scheduler, tool, and
system-triggered workflows share this path.

APScheduler missed events and dispatch errors that do not reach the governor
are recorded as scheduler-sourced terminal runs. Stable scheduler event keys
make those records idempotent.

Detailed history is bounded by age and per-workflow count. The latest terminal
outcome for each workflow is retained even when it is older than the detail
window. The runtime context owns one `WorkflowRunStore`, and status surfaces
read workflow last-run fields from that durable store.

## Rationale

Workflow history is domain state, not generic task infrastructure or diagnostic
logging. Giving it a dedicated persistence boundary keeps cancellation
mechanics process-local, keeps mutation history focused on vault changes, and
allows workflow status to survive restarts without treating logs as a database.

The governor is the correct write boundary because every normal workflow trigger
already passes through it and it owns workflow result normalization, timeouts,
and failure classification.

## Consequences

- Workflow status uses the returned domain status rather than APScheduler's
  executed/error distinction.
- Every governor run must reach one idempotent durable terminal transition.
- A persistence failure prevents untracked workflow execution from proceeding.
- Scheduler misses are visible even though they have no execution task.
- Generic execution tasks remain process-local and are not a historical query
  API.
- Vault Activity remains the source for file mutations, revisions, and rollback,
  not workflow execution health.

## Evidence

- Current system map: `docs/development/architecture.md`
- Implementation: `core/workflow_runs`,
  `core/runtime/workflow_governor.py`, `core/scheduling/job_history.py`
- Validation:
  `validation/scenarios/integration/core/workflow_run_history.py`,
  `validation/scenarios/integration/core/workflow_governor_timeout.py`
- Implementation plan: `activity-log-and-workflow-health-plan.md`
