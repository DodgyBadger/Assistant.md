# Cleanup Before Merge

## What Matters Now
- Remove temporary scaffolding and session leftovers.
- Align docs, validation, logging, and behavior.
- Look for small inconsistencies that create future maintenance cost.

## Checklist
- Remove temporary debug code, comments, probes, and dead helpers.
- Confirm no secrets or runtime-state artifacts are being committed:
  never commit real API keys, populated `system/secrets.yaml`, or unintended changes under persistent runtime state.
- Make sure docs match the final behavior: update user-facing docs and examples;
  update `docs/development/architecture.md` only when subsystem ownership,
  trust boundaries, or major execution flows change.
- Recommend a new ADR when the final implementation establishes or changes a
  durable architecture decision:
  cross-subsystem boundaries, persistence ownership, user-data ownership,
  execution policy, security posture, or model/tool/runtime contracts.
  Do not create ADRs for temporary implementation choices or decisions that are
  still likely to change; call out the recommendation in the handoff when the
  maintainer should decide.
- Draft the next `RELEASE_NOTES.md` entry for the change.
  Keep the entry user-oriented:
  describe what changed for users, operators, or template authors, not internal architecture churn unless it changes how they use or upgrade the system.
  Source it in this order:
  1. the current effort's root-level implementation plan
  2. the linked GitHub issue, if one exists
  3. commit messages for smaller follow-on additions or cleanup
- Re-read changed error messages and logging for clarity.
- Confirm the final handoff explains:
  what changed, what was verified, and what still needs maintainer action.

## Common Mistakes
- Leaving behind session-only helpers or debug instrumentation.
- Updating implementation without updating docs or examples.
- Treating every plan change as an ADR candidate; ADRs should capture durable
  decisions that survived the work.
- Accidentally committing secrets or local runtime-state artifacts.
- Writing release notes from memory instead of the implementation plan and issue history.
- Forgetting to mention unrun full validation or unresolved risks.

## Phase Exit
The change is ready for maintainer review or merge preparation.
