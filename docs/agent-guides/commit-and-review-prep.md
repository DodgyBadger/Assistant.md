# Commit and Review Prep

## What Matters Now
- Keep one logical change per commit.
- Make the diff easy to understand without rereading the full session.
- Confirm the commit subject describes the behavior or invariant that changed.
- Treat a validated logical milestone as a commit boundary, not only the end of
  the entire branch.

## Checklist
- Review the final diff for accidental scope growth.
- Separate refactor-only edits from behavior changes when practical.
- Confirm docs and validation changes match the implementation.
- For any production Python change, run and report the complete
  [Production Python Quality Gate](coding-standards.md#production-python-quality-gate).
  A commit is not ready while any of those commands reports a finding.
- Review new `noqa`, `type: ignore`, `Any`, casts, and checker-configuration
  changes; confirm each is narrow and justified at a real boundary.
- Write a focused commit message using [Git and Review Workflow](git-and-review.md).
- Commit completed work before beginning a distinct experiment or redesign that
  will touch the same files.
- Confirm the complete `integration/core` profile passed during hardening or run it during merge preparation. Note any unrun checks or external blockers in the handoff.

## Common Mistakes
- Mixing multiple logical changes into one commit.
- Writing commit subjects that describe files instead of behavior.
- Forgetting to mention unrun checks, external blockers, or remaining maintainer actions.
- Skipping a final duplication and cleanliness pass before committing.
- Relying only on checks scoped to changed files.
- Letting several accepted milestones accumulate in one dirty worktree, making
  selective rollback unnecessarily risky.

## Phase Exit
Move to [Cleanup Before Merge](cleanup-before-merge.md) when the commit shape is set and only merge-readiness checks remain.
