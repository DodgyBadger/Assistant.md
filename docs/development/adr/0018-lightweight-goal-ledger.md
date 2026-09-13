# 0018 - Use A Lightweight Goal Ledger

## Status

Accepted.

## Context

AssistantMD needs better support for long-running knowledge work such as
research synthesis, report drafting, project/client folder processing, and
bounded vault maintenance. These tasks need durable recovery points and audit
state, but they should not turn every chat into a managed autonomous run.

AssistantMD is markdown-first. Users should continue to own their workspace
files, notes, drafts, playbooks, and workflows. SQLite is appropriate for exact
runtime invariants that markdown handles poorly, such as stable ids,
provenance, status, timestamps, and recovery checkpoints.

The system already has chat sessions, workflows, execution tasks, vault mutation
records, and file tools. A goal layer should compose with those systems instead
of creating a parallel scheduler, artifact registry, or project-management
model.

## Decision

Add a small SQLite-backed goal ledger exposed through `goal_ops`.

Goals record operational state only:

- stable goal ids;
- vault and optional workspace hints;
- system-inferred source provenance;
- title, objective, status, success criteria, and a lightweight plan snapshot;
- compact recovery checkpoints;
- meaningful goal events;
- related activity derived from existing vault mutation rows.

Do not model public first-class step rows, dependency graphs, artifact
attachments, workflow triggers, or session-owned active-goal pointers in this
phase.

Markdown remains the user-facing work surface. Files such as `goal.md`,
`progress.md`, `sources.md`, drafts, `README.md`, and `playbook.md` are useful
composition conventions, not authoritative goal databases. If a user wants
markdown-driven goal state, a workflow or context script can deliberately read
markdown and call `goal_ops`.

File mutation provenance remains owned by the vault mutation system.
`goal_ops(list_activity)` derives related files from existing mutation records
rather than maintaining a separate artifact table.

## Rationale

This gives agents a durable recovery primitive without creating a new autonomous
mode. It preserves AssistantMD's composable model: chat, workflows,
`code_execution`, file tools, and user-authored scripts continue doing work,
while `goal_ops` records enough state to recover from compaction, request
limits, timeouts, model failures, and user interruption.

A lightweight plan snapshot is intentionally less powerful than a project
manager. It avoids durable step ordering, stale pointers, rework semantics, and
session/goal authority conflicts until a concrete workflow requires them.

Source provenance belongs to the runtime layer because a model should not be
able to declare where a goal came from. This keeps chat, workflow, and context
origins queryable without adding a session-level active goal field.

## Consequences

- Long-running work can checkpoint progress without relying only on chat
  memory.
- Goals can be queried by id, status, source, or workspace hint from chat,
  workflows, or context scripts.
- Workspace markdown remains user-owned and flexible.
- Goal-related files are visible through existing mutation activity, avoiding a
  second artifact pathway.
- More advanced orchestration remains future work: durable subtasks, dependency
  graphs, approval states, and automatic context injection should be added only
  when a real workflow proves they are needed.

## Evidence

- Current system map: `docs/development/architecture.md`; tool contract:
  `docs/tools/goal_ops.md`
- Current implementation: `core/goals/`, `core/tools/goal_ops.py`
- Validation: `validation/scenarios/integration/core/goal_ops.py`
