# Testing and Validation

## Validation Scope
- Primary integration testing is scenario validation under `validation/scenarios/`.
- Prefer adding or adjusting scenario files by feature area (for example, `validation/scenarios/integration/`).
- Scenario names should be descriptive and behavior-oriented (for example, `context_manager_cache_modes.py`).
- Scenario coverage is for primary subsystem boundaries, durable contracts, and
  regressions we have already seen or have strong reason to expect. Do not add a
  scenario for every small UI detail, formatting tweak, or implementation helper.
  Use focused smoke checks, code review, or an existing scenario assertion for
  those narrower changes unless they protect an important cross-subsystem
  contract.

### Scenario taxonomy and pre-merge profile

Organize scenarios by what they exercise, not merely because they guard against
a regression:

- `integration/core`: deterministic integration, persistence, authorization,
  isolation, encryption, and network-boundary contracts that run without
  external services; this is the automatic CI and merge-gate profile;
- `experiments`: live-model, external-service, stress, and diagnostic probes
  that are not merge gates.

`regression` and `security` are not scenario categories. Every deterministic
contract required to protect a merge belongs in `integration/core`. Security
probes that depend on live models, external content, or diagnostic judgment
belong in `experiments` unless they can be made deterministic.

The regular pre-merge validation profile is `integration/core`. Run it with:

```bash
python validation/run_validation.py run integration/core
```

Experimental scenarios are opt-in and are not part of that profile. During active branch development, run the relevant individual scenarios for fast feedback. Once the branch is stable, run the complete `integration/core` profile during either the hardening pass or merge preparation. Do not duplicate the full run in both phases when it already passed against the same effective behavior.

## Integration Scenario Assertions
- Never assert on non-deterministic LLM prose in integration scenarios.
- Prefer deterministic artifacts: API responses, file contents, persisted state, validation events, tool calls, exact helper outputs, or stable contract fragments from static templates.
- If a scenario needs to prove that an LLM-visible instruction exists, assert only the smallest stable contract terms rather than a full sentence that may be edited for tone.
- Avoid asserting frontend CSS classes, markup structure, or copy unless that is
  the explicit product contract under test. Prefer the stable data contract that
  drives the UI, and leave visual styling details to manual review or a
  purpose-built frontend harness.

## Validation-First Workflow

Use the validation framework to shape feature design from day one for changes
that affect durable behavior or subsystem boundaries, not as a blanket rule for
every edit.

### 1) Write the feature spec in terms of artifacts
- final artifacts users will observe (files, API responses, state changes)
- internal artifacts required for confidence (validation events at key decision points)
- non-negotiable invariants (what must never regress)
- why the behavior warrants scenario coverage instead of a lighter smoke check:
  for example, it crosses subsystem ownership, preserves user data, guards a
  previously observed drift point, or defines a durable tool/API/runtime
  contract

### 2) Add scenario assertions before implementation
- create or extend a scenario with failing assertions for those artifacts
- include final artifact assertions (end-user behavior)
- include internal artifact assertions (decision correctness, skip reasons, routing, cache behavior)

This makes the scenario the executable contract for the feature.

### 3) Define event contracts in the implementation plan
- event name
- minimum payload keys that represent behavior
- when the event should fire

Only add events at decision boundaries; avoid noisy instrumentation.

### 4) Implement in small slices until scenario contract passes
- build the smallest increment that satisfies the next failing assertion
- keep assertions behavior-focused (avoid coupling to internals)
- prefer deterministic scenarios (`@model test`) unless real model behavior is explicitly required
- do not assert on free-form LLM wording; assert on validation events you control, exact API/file artifacts, and other deterministic outputs instead

### 5) Tighten and keep
- keep contract assertions as long-term regression guards
- remove temporary debug-only events/assertions
- preserve stable event names/payload keys as compatibility surface for scenarios

## What Matters Now
- Assert behavior, not implementation accidents.
- Prefer deterministic scenarios (`@model test`) unless real model behavior is essential.
- Use local smoke tests to probe exact edge cases quickly, especially for helper functions and failure paths.
- Keep development feedback fast with directly relevant scenarios, then run the complete deterministic pre-merge profile after the branch stabilizes.

## Definition of Done (Workflow Perspective)
- New behavior is represented by scenario assertions (new or updated).
- Decision-heavy branches have stable, behavior-oriented validation events when needed.
- Temporary debug instrumentation is removed.
- Relevant technical docs are updated (architecture, usage, or examples).
- The complete `integration/core` profile has passed against the stable branch during hardening or merge preparation, unless an external blocker is documented.

## Execution Rules
- During feature development, run individual scenarios directly to validate the functionality being built.
- During hardening or merge preparation, run the complete `integration/core` profile once the branch is stable.
- If behavior changes after that run, rerun affected scenarios immediately and rerun the complete profile when the change could affect broader contracts. Documentation-only changes do not require another full run.
- Do not run `experiments` or live-service scenarios unless the task requires them; they are not part of the deterministic merge gate.
- Static analysis is separate from scenario validation. Production Python changes still require the complete [Production Python Quality Gate](coding-standards.md#production-python-quality-gate) before commit or handoff.

## Agent Smoke Tests (Local, Fast)
- For new functions/modules, run quick ephemeral bash-based smoke tests before handoff.
- Pattern: `python - <<'PY' ... PY` using isolated temp roots under `/tmp` (for example, `/tmp/workflow-run-check`).
- For runtime-dependent smoke tests, call `set_bootstrap_roots(data_root, system_root)` before importing modules that read settings/runtime paths.
- Favor targeted failure probes that assert exact error text/pointers (for example, bad directive value, missing step name).

## Common Mistakes
- Relying on free-form model output instead of deterministic artifacts.
- Adding noisy events instead of decision-boundary events.
- Treating smoke tests as a substitute for scenario coverage.
- Running the complete pre-merge profile after every development edit instead of reserving it for a stable hardening or merge-preparation pass.
- Reaching merge preparation without a successful `integration/core` run against the branch's effective behavior.

## Phase Exit
Move to [Refactor and Hardening](refactor-and-hardening.md) once behavior is correct and covered.
