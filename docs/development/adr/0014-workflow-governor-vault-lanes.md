# 0014 - Govern Workflow Execution Through Vault Lanes

## Status

Accepted, backfilled. Superseded in part by
[0019 - Centralize Runtime Execution Task Running](0019-runtime-execution-task-runner.md).

## Context

Workflows can be triggered by scheduler jobs, API requests, and the
`workflow_run` tool. They may mutate vault files and can overlap if triggered
from more than one path. APScheduler owns timing, but it is not the right place
to encode AssistantMD-specific vault concurrency, task metadata, timeout, or
cancellation policy.

## Decision

Route workflow execution through `WorkflowGovernor`. The governor registers a
workflow execution task, serializes workflow execution by vault scope, applies
workflow timeout and optional global concurrency limits, and provides one policy
path for scheduler, API, system-template, and tool-triggered workflow runs.

ADR 0019 preserves the user-facing workflow invariants from this decision:
workflow execution remains serialized by vault, workflow timeout remains
enforced, and all workflow triggers use one policy path. It supersedes the
implementation ownership: generic spawning, queueing, lane, concurrency, and
timeout mechanics should move to the runtime execution task runner, leaving
workflow-specific result shaping and metadata in workflow code.

## Rationale

Vault-scoped lanes are conservative and user-centered: one vault's workflows run
sequentially, while workflows in different vaults may run concurrently when
global settings allow. This reduces file mutation races without making
APScheduler responsible for runtime policy. It also means lifecycle events,
task ids, cancellation, and failure metadata are consistent across trigger
sources.

## Consequences

- APScheduler persists and fires jobs; runtime execution policy owns generic
  task running mechanics, while workflow code owns workflow-specific execution
  behavior.
- Manual API runs and `workflow_run` use the same workflow path as scheduled
  runs.
- The per-vault lane is always active.
- Global workflow concurrency is a separate optional limit.
- Workflow job args stay lightweight and picklable for the scheduler store.

## Evidence

- Current system map: `docs/development/architecture.md`
- Recovered sources: PR #41 `EXECUTION_TASK_COORDINATOR_PLAN.md`,
  `EXECUTION_TASK_COORDINATOR_IMPLEMENTATION_PLAN.md`
