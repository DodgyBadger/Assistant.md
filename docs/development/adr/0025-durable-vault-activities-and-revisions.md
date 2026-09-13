# 0025 - Generalize Vault Mutations Into Durable Activities And Revisions

## Status

Accepted.

Amends
[0005 - Route Vault Mutations Through Audit And Snapshot Infrastructure](0005-vault-mutation-audit-and-snapshots.md).

## Context

Vault mutation audit initially centered on execution tasks and automatic recovery
after a failed, cancelled, or timed-out task. The unified Vault Explorer adds
direct user mutations that do not belong to a long-running task. Users also need
to understand related operations as one activity, inspect earlier versions of a
file, restore one retained version, and reverse a completed multi-operation
activity without losing subsequent recovery options.

Task-specific mutation ownership cannot represent all of those sources cleanly.
Separate restore implementations for task rollback, revision restore, and
activity rollback would also drift in conflict detection, locking, compensation,
and manifest refresh behavior.

## Decision

Use a source-neutral durable vault activity ledger.

- `vault_activities` records attributed units of work from chat, workflows,
  ingestion, code execution, and direct Vault Explorer commands.
- `vault_mutations` records path-level before and after states, logical operation
  identity, related paths, and retained snapshot references under an activity.
- Execution task, goal, step, session, and source metadata are provenance on the
  owning activity rather than the activity's identity model.
- Directory operations are represented as one logical directory mutation rather
  than synthetic child-file mutations.

Retain pre-mutation file states as snapshot records owned by either an execution
task or explicit activity. Those snapshots provide exact-path revision history
for AssistantMD-routed writes. Revision history does not follow a file across a
move or rename, and externally performed edits are observable through manifest
refresh but do not have a guaranteed before-state snapshot.

Revision restore, explicit activity rollback, and automatic task rollback share
one policy-neutral exact-file restoration primitive in
`core.vault_state.file_mutations`. The primitive:

- resolves every requested vault path and rejects duplicate targets;
- validates expected current existence and content hashes while holding the
  process-local vault hierarchy and exact-path mutation locks;
- validates retained restore content before mutation;
- captures displaced states for compensation;
- applies and verifies the complete requested state transition;
- refreshes vault state before reporting success;
- compensates already-applied filesystem changes if execution or required
  durable finalization fails.

Explicit activity rollback restores each path to its earliest before-state in
the source activity and validates against that activity's latest recorded
after-state. It is all-or-nothing. A successful rollback is recorded as a new
linked Explorer activity whose displaced states are retained, so that rollback
can itself be rolled back while expected current states still match.

Automatic rollback remains a terminal execution-task policy, but it uses the
same restoration primitive and expected-state checks. Directory mutations are
durably auditable but are not eligible for automatic task rollback, exact-path
revision restore, or explicit file-state activity rollback.

## Rationale

Activity is the stable user-facing unit of intent; an execution task is only one
possible source. A generalized ledger lets the Dashboard, session summaries,
goals, Explorer revisions, and rollback use one provenance model without
inventing parallel records for direct user edits.

Before-state snapshots are non-rebuildable evidence and must be captured at the
mutation boundary. The current manifest remains rebuildable from canonical vault
files. Sharing one atomic restoration engine prevents a recovery path from
silently overwriting later work or offering weaker guarantees than another
recovery path.

## Consequences

- Every supported interactive or task-owned AssistantMD mutation should be
  attributed to a task-derived or explicit activity and route through the shared
  mutation service.
- Direct Explorer edits receive the same durable activity and retained revision
  treatment as tool-driven edits.
- One stale path, missing restore payload, unsupported directory mutation, or
  vault mismatch rejects an explicit multi-path rollback before any final state
  is accepted.
- Snapshot and mutation retention settings bound how long revision and rollback
  data remains available; the ledger is not a full version-control system.
- Rollback creates forward audit history rather than rewriting or deleting the
  source activity.
- Process-local locks coordinate AssistantMD writes in one process. Expected
  hashes remain the boundary for external editors and other processes.

## Evidence

- Current system map: `docs/development/architecture.md`
- Implementation: `core/vault_state/activity.py`,
  `core/vault_state/file_mutations.py`, `core/vault_state/snapshots.py`,
  `core/vault_state/activity_rollback.py`, `core/vault_state/rollback.py`
- Validation: `validation/scenarios/integration/core/vault_activity_rollback.py`,
  `validation/scenarios/integration/core/vault_file_reference_api.py`,
  `validation/scenarios/integration/core/vault_state_mutation_recorder.py`
- Implementation plan: `vault-activity-redesign-plan.md`
