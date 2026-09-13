# 0032 - Task-Scoped Active-Run Recovery

## Status

Accepted.

## Context

Long primary-chat runs can cross several model and tool cycles before a
provider stream disconnects. Replaying the whole turn wastes completed work and
can repeat side effects. Canonical chat history is portable and committed at
logical-turn boundaries, so it is not an exact live continuation checkpoint.

Pydantic AI Harness provides native message snapshots and an unresolved
tool-effect ledger. Its stores do not yet provide the whole-run deletion,
retention, claim, and externalized-media lifecycle needed for AssistantMD to
own durable restart recovery safely. Container loss during an active run is not
a demonstrated operational failure mode for this installation.

## Decision

Use Harness `StepPersistence` with a task-scoped `InMemoryStepStore` for primary
chat. Bound retained snapshots per run with Harness's native store option. Keep
canonical session history as the durable source of truth.

Continue from settled snapshots when no tool effect is unresolved. Replay only
tools that declare replay-safe recovery. Route unresolved vault mutations
through terminal task rollback before creating one replacement task. Fail
closed for unresolved unknown, manual, or external effects.

Do not add a recovery database, startup reconciliation, or process-restart
continuation. Reconsider durable recovery only when observed failures justify
it and the persistence design has whole-run lifecycle, bounded retention,
single-consumer claim, compatibility, and media-cleanup contracts.

Compaction, code mode, and other Harness capabilities remain subject to
separate parity review.

## Consequences

- Provider disconnections can recover during a live process without repeating
  completed tool calls.
- Recovery memory is bounded and disappears when the task or process ends.
- A process or container restart still ends the active turn; the accepted user
  request and existing manual retry path remain available.
- No schema migration or new durable provider-bound history is introduced.
- Tool recovery policy remains explicit and owned by each tool.

## Evidence

- Validation:
  `validation/scenarios/integration/core/chat_stream_auto_retry.py`,
  `validation/scenarios/integration/core/tool_recovery_policy.py`
