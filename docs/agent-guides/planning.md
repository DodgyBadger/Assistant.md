# Planning

## What Matters Now
- Define the behavior change in terms of user-visible artifacts and invariants.
- Identify the smallest affected surfaces before editing.
- Prefer understanding current structure over inventing a fresh design.
- Call out assumptions that materially affect implementation shape.

## Checklist
- Inspect the current implementation and nearby modules before proposing changes.
- Create or update a root-level markdown implementation plan for the effort before leaving this phase.
- Identify which workflow phase should come next after planning.
- Name the validation target early:
  scenario to extend, artifact to assert, or smoke test to run.
- When the planned work touches an existing subsystem, check
  `docs/development/adr/` with `ls` or `rg` for relevant architecture decisions that
  explain why the current shape exists.
- Note any contract-sensitive areas:
  directives, API payloads, validation events, routing, settings, persistence.
- Pull in references only as needed:
  [Project Structure](project-structure.md),
  the current system map in `docs/development/architecture.md`.
- If the plan touches settings, secrets, or persisted runtime data, call that out explicitly in the plan.

## Common Mistakes
- Starting implementation before locating the real source of behavior.
- Treating a refactor as a feature or a feature as a refactor.
- Planning only around code changes and not around validation coverage.
- Ignoring runtime-state or persistence implications.

## Phase Exit
Move to [Feature Development](feature-development.md) only after the current effort has a written root-level markdown implementation plan.

That plan should:
- live in the repository root
- be created if none exists for the effort
- be updated if the effort already has a plan file
- capture scope, affected areas, validation target, and the next concrete implementation steps

This is required so work can continue cleanly across chat sessions and longer-running sprints.
