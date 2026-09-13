# 0004 - Track Long Running Work With Process Local Execution Tasks

## Status

Accepted, backfilled. Amended by
[0019 - Centralize Runtime Execution Task Running](0019-runtime-execution-task-runner.md).

## Context

Chat, workflows, ingestion, code execution, and history compaction can take long
enough that the UI and API need visibility into active work. Some operations
also need cancellation, timeout handling, lifecycle validation events, and task
identity for vault mutation provenance.

## Decision

Use a process-local `TaskCoordinator` owned by `RuntimeContext` to track active
and recently terminal execution tasks. Use `WorkflowGovernor` as the workflow
policy layer for vault execution lanes, workflow timeout, lifecycle metadata,
and workflow task registration.

ADR 0019 preserves the `TaskCoordinator` decision and supersedes the assumption
that task running policy should live in workflow-specific code. Generic task
running mechanics now belong in a runtime-owned execution task runner.

## Rationale

The first durable need was runtime visibility and cancellation, not a persistent
job history. A process-local coordinator gives active tasks a consistent status
model and cancellation handle without turning every operation into a durable job
queue. Keeping workflow policy in a governor avoids duplicating workflow
execution rules across scheduler, API, and tool paths.

## Consequences

- Task snapshots are runtime state, not a permanent system of record.
- Long-running entrypoints should register through the coordinator.
- Terminal task history is bounded.
- Vault mutation rows can persist task identity even though task snapshots
  themselves are process-local.
- Cancellation needs explicit handling so cancelled work is not reported as a
  generic failure.

## Evidence

- Current system map: `docs/development/architecture.md`
- Recovered sources: PR #41
  `EXECUTION_TASK_COORDINATOR_IMPLEMENTATION_PLAN.md`,
  `EXECUTION_TASK_COORDINATOR_PLAN.md`, `CHAT_HISTORY_COMPACTION_SPEC.md`
