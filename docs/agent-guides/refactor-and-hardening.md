# Refactor and Hardening

## What Matters Now
- Reduce entropy after correctness is established.
- Centralize logic that would otherwise drift.
- Tighten error paths and observability.
- Keep contracts stable while improving internals.

## Contract-First Smell Framework

Use conventional code-smell vocabulary as a diagnostic aid, not as a mechanical
scorecard. Long methods and large modules matter when they hide ownership,
invalid states, or behavior that can drift. Start with system contracts and
consequences, then inspect local structure.

Review through these lenses, in order:

### 1. Behavioral Contracts

- Identify the authoritative contract for each operation and the paths that
  invoke it.
- Compare behavior across API, UI, tools, workflows, scripts, schedulers, and
  other automation surfaces.
- Look for contract drift, bypass paths, inconsistent results, and policy or
  validation applied only at one adapter.
- Confirm that safeguards such as mutation recording, snapshots, concurrency
  checks, approval handling, and persistence cannot be skipped accidentally.

### 2. Responsibility And Duplication

- Look for duplicated code, divergent change, shotgun surgery, feature envy,
  middlemen, and parallel service layers.
- Centralize validation, normalization, mutation execution, payload assembly,
  and error translation when copies could drift.
- Prefer one authoritative operation helper with thin adapters over adapter
  chains that merely forward arguments.
- Remove an abstraction when it does not own policy, stabilize a contract, or
  eliminate meaningful complexity.

### 3. State And Lifecycle

- Look for temporal coupling, boolean blindness, mutable shared state, stale
  caches, incomplete cleanup, and states the system can represent but cannot
  handle.
- Trace task, session, deferred action, artifact, modal, editor, and persistence
  lifecycles through success, denial, retry, reload, cancellation, and failure.
- Write down the state transitions for decision-heavy flows even when a formal
  state-machine implementation would be excessive.
- Verify that repeated, delayed, and out-of-order actions are idempotent or fail
  explicitly.

### 4. API And Data Boundaries

- Look for primitive obsession, data clumps, leaky abstractions, inconsistent
  errors, unstable payloads, and speculative generality.
- Ensure structured application data crosses boundaries instead of prompts,
  pre-rendered UI, or trusted instructions assembled by an untrusted client.
- Keep core helpers independent from transport, chat artifact, and UI concerns.
- Preserve canonical, portable records when another model, summarizer, or
  process must understand the outcome later.

### 5. Failure Behavior And Observability

- Look for swallowed exceptions, broad catches, partial mutation, misleading
  success, lossy error translation, and cleanup that runs only on the happy
  path.
- Trace stale data, malformed inputs, external changes, process restart,
  unavailable dependencies, and multi-step partial failure.
- Require useful decision and failure events without logging sensitive file
  contents or producing noisy per-item telemetry.

### 6. Frontend Structure And Usability

- Look for large controllers, callback spaghetti, hidden DOM coupling,
  duplicated rendering, async races, and view state represented by unrelated
  flags.
- Check keyboard, pointer, and mobile flows; focus restoration; dark-mode and
  responsive behavior; loading and empty states; and unsaved-change handling.
- Treat multiple views of one feature as one navigation and state system, even
  when implementation is split across modules.
- Reuse shared renderers, controls, and API clients so equivalent entry points
  behave consistently.

AssistantMD-specific smells that deserve explicit attention are **contract
drift**, **bypass paths**, **invalid lifecycle states**, **adapter leakage**, and
**partial mutation**.

## Finding Severity

Classify findings by consequence, not by the size of the code change:

- **Critical:** data loss, vault-boundary escape, approval or authorization
  bypass, secret exposure, or corrupted canonical history.
- **High:** incorrect mutation, unrecoverable or stuck workflow, broken
  continuation, or major contract divergence.
- **Medium:** duplication likely to drift, bounded race conditions, weak error
  semantics, or recoverable inconsistent state.
- **Low:** readability, naming, unnecessary indirection, local complexity, or
  visual inconsistency without behavioral impact.

Each finding should name the smell, affected contract, consequence, concrete
evidence, and the smallest defensible correction. Findings lead the review and
are ordered by severity; summaries and cleanup suggestions come afterward.

## Review Workflow

1. Establish the branch diff and map every touched subsystem and public
   contract.
2. Write down intended invariants before judging implementation details.
3. Trace representative operations end to end across every relevant adapter.
4. Exercise failure, reload, cancellation, concurrency, and automation paths,
   not only the successful interactive path.
5. Search structurally for duplicated validation, obsolete temporary paths,
   broad exception handling, and multiple sources of truth.
6. Review frontend lifecycle and responsive behavior separately from backend
   correctness.
7. Report findings first, ordered by severity and grounded in file/line
   references.
8. Convert accepted findings into small hardening stages. Keep behavioral fixes
   distinct from structural refactors when practical.

## Checklist
- Preserve the zero-finding
  [Production Python Quality Gate](coding-standards.md#production-python-quality-gate);
  structural work is not complete while new or discovered findings remain.
- Remove duplicated logic that can drift:
  parameter schemas, validation paths, payload construction, routing decisions.
- Extract mixed-responsibility functions into focused helpers.
- Centralize cross-cutting utilities when drift risk is high.
- Improve error quality:
  fail fast - avoid broad catches, preserve diagnostics, and keep user-facing failures specific.
- Verify logging coverage for changed paths:
  start, decision, success, and failure milestones with structured context.
- Confirm docs and validation still describe the post-refactor behavior.
  This includes `docs/development/architecture.md` when subsystem ownership,
  trust boundaries, or major execution flows change.
- If the refactor reveals a bug, fix it explicitly and keep the scope clear.
- Ask before building compatibility shims or adapters.
- When a dev branch is approaching finalization, consider dependency freshness as part of the hardening pass:
  check whether Python or Node dependencies are stale, whether security audit failures are likely, and whether newer dependency versions unlock simpler or safer implementation patterns. Only propose updates to versions that have been available for more than one week, unless a security fix requires faster action. Do not update dependencies during unrelated refactors by default; propose a scoped dependency refresh when it would reduce risk, fix CVEs, or simplify the code.

## Guardrails
- Refactor in small, reviewable chunks.
- Do not mix adjacent feature work into the refactor pass.
- Preserve validation and event contracts unless the change explicitly updates them.
- If a refactor reveals a real bug, fix it, call it out, and keep the diff scoped.

## Observability Standard
- Any new feature or fix should leave behind useful activity logging for the changed path.
- At minimum, cover:
  operation start, meaningful decisions, successful completion, and failures.
- Use stable tags and structured fields so logs remain queryable over time.
- Avoid noisy per-loop logging; prefer lifecycle milestones and decision boundaries.
- Never swallow exceptions without preserving actionable diagnostics.

## Common Mistakes
- Expanding the refactor into adjacent feature work.
- Changing public contracts accidentally while cleaning internals.
- Leaving split-brain validation or policy logic in multiple helpers.
- Calling work “done” once scenarios pass without addressing obvious drift risks.
- Hiding type uncertainty behind broad `Any`, casts, or checker suppressions
  instead of typing the owning boundary.

## Reference Docs
- [Coding Standards](coding-standards.md)
- [Git and Review Workflow](git-and-review.md)

## Phase Exit
Move to [Commit and Review Prep](commit-and-review-prep.md) once the remaining changes are packaging and review-readiness work.
