# 0031 - Separate Managed And Project-Local Workflows

## Status

Accepted.

## Context

Reusable and scheduled automations fit the managed `AssistantMD/Authoring/`
catalog. Research and other project workflows often belong beside the source
library, project instructions, manifests, and outputs they process. Requiring a
project workflow to live in the managed catalog separates its processing method
from the workspace it describes.

Expanding workflow discovery to the whole vault would make ordinary Markdown
an implicit executable candidate, increase scan cost and naming collisions, and
blur which workflows may be scheduled.

## Decision

Keep workflow discovery, scheduling, enable/disable lifecycle operations,
Dashboard management, and context-template selection confined to the managed
Authoring locations.

Allow `workflow_run` operations `run` and `start` to execute an explicitly
selected vault-relative Markdown workflow outside those locations. Explicit
targets must resolve inside the current vault, use a `.md` extension, declare
`run_type: workflow`, and satisfy the normal authoring parser contract. They
enter the existing `WorkflowGovernor` path and retain its vault lane, execution
authority, timeout, cancellation, rollback, activity, and durable-history
behavior.

## Consequences

- Project-local automation can travel with its project content.
- Only an explicit tool call makes a project-local workflow executable; it is
  not discovered or scheduled.
- Executable authoring files may exist outside one central directory, so task
  metadata, results, and durable identity retain the canonical vault-relative
  source path.
- The Monty sandbox and host capability boundary are unchanged.
- Managed workflow name execution remains backward compatible.

## Evidence

- Current system map: `docs/development/architecture.md`,
  `docs/tools/workflow_run.md`
- Validation:
  `validation/scenarios/integration/core/vault_relative_workflow_run.py`
