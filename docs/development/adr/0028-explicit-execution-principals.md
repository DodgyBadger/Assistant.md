# 0028 - Make Execution Principals Explicit

## Status

Accepted.

## Context

AssistantMD currently presents a single-user product, but future principal-owned
resources require a stable identity and authorization boundary.
Task source values such as `api`, `tool`, and `scheduler` describe how work
began; they do not identify the actor authorized to use a resource.

## Decision

Use stable `local-user` and `system` principals. Interactive API requests resolve
to `local-user`, while installation maintenance uses `system`. Chat sessions,
workflow definitions, schedules, and workflow-run history persist an immutable
owner. Scheduled jobs carry the stored workflow owner rather than inferring
authority from the scheduler execution source or defaulting to `system`.

Every execution task captures explicit authority when created and installs it in
context-local state while its worker runs. API-, tool-, nested-, and
scheduler-triggered work retains that captured authority through execution and
durable history. Missing ownership or authority fails closed rather than
selecting an interactive principal.

The API router installs interactive authority once for each request.
Runtime-owned session, task, and workflow access services mediate
principal-owned resource access through the context-local authority; the
process-global runtime context does not store mutable current-principal state.
Authorization policy lives behind shared identity-domain services rather than
task metadata, transport-provided identifiers, or repeated endpoint checks.

Principal IDs are persistence contracts. Public API payloads remain unchanged;
the single-user resolver and ownership fields are internal foundations.

## Consequences

- Existing sessions migrate deterministically to `local-user`.
- Existing user-authored workflows, schedules, and run records migrate
  deterministically to `local-user`; system maintenance remains explicitly
  `system`-owned.
- Background and nested work retain the authority captured at creation.
- Scheduled workflows resolve the stored owner's models, secrets, connections,
  and external capabilities rather than scheduler-global credentials.
- Tooling can resolve future principal-owned resources without FastAPI state.
- New API endpoints inherit request authority automatically, while resource
  services remain responsible for owner-scoped access decisions.
- Multi-user authentication, vault grants, connection storage, and user
  administration remain separate features.
- Task source and execution authority remain distinct concepts.

## Evidence

- Current system map: `docs/development/architecture.md`
- Principal-owned workflow implementation: `core/authoring/workflow_execution.py`,
  `core/runtime/workflow_governor.py`, `core/workflow_runs/`, and
  `core/scheduling/`
