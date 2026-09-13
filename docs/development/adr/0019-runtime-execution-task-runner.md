# 0019 - Centralize Runtime Execution Task Running

## Status

Accepted.

Supersedes the workflow-specific execution-policy portions of
[0014 - Govern Workflow Execution Through Vault Lanes](0014-workflow-governor-vault-lanes.md).

Amends [0004 - Track Long Running Work With Process Local Execution Tasks](0004-process-local-execution-tasks.md)
by separating task state coordination from task running policy.

## Decision

AssistantMD uses a runtime-owned `ExecutionTaskRunner` as the generic execution
policy layer for execution tasks.

`TaskCoordinator` owns process-local task state, status transitions,
cancellation handles, lifecycle validation events, and current-task context for
mutation provenance.

`ExecutionTaskRunner` owns generic task running mechanics: detached background
spawning, queued task attachment, background handle registration, keyed gates,
timeout enforcement, and inline task contexts for awaited work.

Task terminal notification is part of the runtime execution contract. When
shutdown or cancellation requests affect a live worker, terminal observers run
after the owned worker has unwound, not merely after cancellation has been
requested.

Domain adapters own domain-specific behavior. They supply task identity,
metadata, gate keys, timeout settings, lifecycle hooks, and domain results such
as workflow metadata, chat events, ingestion job statuses, compaction summaries,
and vault-state refresh logs.

`WorkflowGovernor` remains the workflow adapter while workflow-specific result
shaping, failure metadata, global workflow concurrency policy, and workflow
lifecycle logging live there. Generic workflow background spawning, vault lane
locking, and timeout enforcement belong to `ExecutionTaskRunner`.

## Rationale

Task type differences are real, but spawning, cancellation, timeout, queueing,
lane locks, background handle tracking, and shutdown behavior are shared runtime
mechanics. Centralizing them reduces drift between chat, workflows, ingestion,
compaction, and future external chat surfaces.

Keeping domain adapters thin prevents runtime infrastructure from learning
workflow result schemas, chat SSE payloads, ingestion storage rules, or
compaction summary contracts.

Ordering terminal observers after worker unwind keeps follow-up policies, such
as vault mutation rollback, from racing active task cleanup or late file
mutations.
