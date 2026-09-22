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

## Required Hardening Stages

Address every stage in order. A stage may be marked not applicable only with a concrete reason in the handoff; do not silently skip it because the implementation already passes its focused scenarios. Record findings by severity with file and line evidence, then make accepted corrections in small reviewable changes.

### Stage 1: Scope And Contract Map

- Establish the branch diff and map each changed subsystem, user-visible behavior, durable record, API/tool boundary, and automation entry point.
- Write down the authoritative contract and invariants for each changed operation before reviewing implementation details.
- Compare equivalent paths across API, UI, tools, workflows, scripts, schedulers, and background execution for drift or bypasses.
- Exit evidence: a concise list of affected contracts and the paths that exercise them.

### Stage 2: Structure And Ownership

- Search for duplicated validation, normalization, mutation, payload construction, routing, and error translation that could drift.
- Extract mixed-responsibility functions and remove abstractions that own no policy or stable contract.
- Check new `Any`, casts, suppressions, compatibility shims, and parallel service layers; each must be narrow and justified at the owning boundary.
- Exit evidence: structural findings are resolved or explicitly retained with rationale, and the production quality gate has no findings.

### Stage 3: State, Lifecycle, And Failure Paths

- Trace success, denial, retry, reload, cancellation, timeout, concurrency, process shutdown, and partial-failure behavior for each affected lifecycle.
- Verify that safeguards such as authorization, mutation recording, snapshots, cleanup, and persistence cannot be bypassed by another adapter or an out-of-order action.
- Fail fast on invalid states, preserve actionable error details, and confirm repeated or delayed actions are idempotent or rejected explicitly.
- Exit evidence: focused scenarios or smoke checks cover the relevant non-happy paths and no task, lock, temporary artifact, or partial mutation is left stranded.

### Stage 4: Activity Logging And Observability

- Follow [Activity Logging](activity-logging.md) and inspect the actual emitted records, not only the presence of logger calls.
- For every changed user-visible or background operation, verify a compact lifecycle: start, meaningful decision or queued/retry state when applicable, and completion, skip, cancellation, timeout, or failure.
- Require stable `event` and `status` fields, searchable user-known and correlation identities, concise `error_type` and `error` fields on failures, and enough context to identify the next inspection step.
- Check warning deduplication explicitly: repeated failures for different operations must carry an appropriate stable `issue` identity, while genuinely repeated noise may collapse within one boot.
- Keep high-frequency progress, loop, token, and helper-detail events out of System Activity; use validation-only logs for those signals and exclude prompts, outputs, secrets, document contents, and bulky arguments.
- Add or update deterministic assertions for the logging contract, including at least one System Activity assertion when introducing a new operation family.
- Exit evidence: list the lifecycle events inspected, the identities they can be searched by, and any intentionally validation-only events.

### Stage 5: Frontend And Operator Experience

- Review loading, empty, error, retry, cancellation, reload, reconnect, focus restoration, keyboard, pointer, mobile, and unsaved-change behavior for affected interfaces.
- Check that equivalent views share state and rendering contracts rather than depending on hidden DOM coupling or unrelated flags.
- Confirm operator-facing status and error text agrees with backend state and does not imply success before durable completion.
- Exit evidence: relevant browser or syntax checks pass, or the handoff identifies the exact manual interaction still required.

### Stage 6: Documentation, Validation, And Release Readiness

- Confirm current-contract docs, architecture records, examples, validation scenarios, and release notes describe the hardened behavior consistently.
- Run affected individual deterministic scenarios after each correction and preserve the zero-finding [Production Python Quality Gate](coding-standards.md#production-python-quality-gate).
- Once behavior is stable, run the complete `integration/core` validation profile here or record that it must run during merge preparation. Do not rerun it solely because the phase changed when it already passed against the same effective behavior.
- Consider dependency freshness when the branch is approaching finalization. Propose a scoped refresh when it reduces risk, fixes CVEs, or simplifies the implementation; do not update unrelated dependencies by default, and normally avoid versions released less than one week ago unless a security fix requires them.
- Exit evidence: report focused checks, the production quality gate, the deterministic pre-merge profile, documentation changes, and any external or manual validation blocker.

## Guardrails
- Refactor in small, reviewable chunks.
- Do not mix adjacent feature work into the refactor pass.
- Preserve validation and event contracts unless the change explicitly updates them.
- If a refactor reveals a real bug, fix it, call it out, and keep the diff scoped.

## Common Mistakes
- Expanding the refactor into adjacent feature work.
- Changing public contracts accidentally while cleaning internals.
- Leaving split-brain validation or policy logic in multiple helpers.
- Calling work “done” once scenarios pass without addressing obvious drift risks.
- Treating logger calls as sufficient without inspecting System Activity payloads and failure records.
- Skipping a hardening stage without recording why it does not apply.
- Hiding type uncertainty behind broad `Any`, casts, or checker suppressions
  instead of typing the owning boundary.

## Reference Docs
- [Coding Standards](coding-standards.md)
- [Git and Review Workflow](git-and-review.md)

## Phase Exit
Move to [Commit and Review Prep](commit-and-review-prep.md) once the remaining changes are packaging and review-readiness work.
