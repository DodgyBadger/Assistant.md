# 0001 - Use Runtime Context As The Service Composition Root

## Status

Accepted, backfilled.

## Context

AssistantMD has several services that must agree on roots, settings, scheduler
state, authoring registry, ingestion, execution tasks, and vault state. Many of
those services are used from API handlers, scheduled jobs, chat execution, and
tools. Path-sensitive modules also need a reliable startup story before the full
runtime exists.

## Decision

Use `RuntimeContext` as the process-wide service composition root. Bootstrap
creates the runtime services, stores them in the runtime context, and registers
that context globally after startup is ready. Path helpers resolve from active
runtime context first and from bootstrap roots only during early startup.

## Rationale

This gives every entrypoint one runtime contract instead of independent service
construction. It keeps scheduler, authoring, ingestion, execution task, vault
state, and reload behavior aligned across API, chat, tool, and background paths.
It also lets path resolution fail fast when neither bootstrap roots nor runtime
context have been established.

## Consequences

- Runtime bootstrap is the place to wire shared services.
- Custom scripts that import path-sensitive modules need bootstrap roots or a
  runtime context.
- Runtime reload can update shared caches and metadata in one place.
- Components should use runtime accessors rather than reconstructing service
  paths or settings ad hoc.

## Evidence

- Current system map: `docs/development/architecture.md`
- Recovered sources: PR #41
  `EXECUTION_TASK_COORDINATOR_IMPLEMENTATION_PLAN.md`, PR #42
  `vault_state.md`, PR #20 `runtime-hardening-plan.md`
