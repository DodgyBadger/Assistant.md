# 0010 - Validate Behavior With Scenario Artifacts

## Status

Accepted, backfilled.

## Context

AssistantMD behavior crosses runtime bootstrap, API handlers, scheduler jobs,
authoring execution, tools, file outputs, and model-facing prompt composition.
Narrow unit tests alone do not prove those contracts, and direct introspection
of internals makes validation brittle during refactors.

## Decision

Use scenario-based validation as the primary behavioral proof surface. Scenarios
run against sandboxed runtime roots and assert on end-user artifacts plus
explicit validation events emitted at important decision boundaries.

## Rationale

Scenario artifacts show what the system actually did: vault outputs, API
responses, logs, validation events, and run-local state. Validation events let
tests assert internal decisions such as routing, fallback, cache behavior, and
task lifecycle without coupling the scenario to private classes or helper
functions.

## Consequences

- Product code should emit validation events at stable decision boundaries when
  behavior needs deterministic proof.
- Event names and behavior keys are compatibility surfaces.
- Scenarios should prefer high-level helpers and artifacts over mocks.
- Full validation remains maintainer-owned; agents should use targeted local
  checks and request full-suite results when needed.

## Evidence

- Current system map: `docs/development/architecture.md`,
  `docs/agent-guides/testing-and-validation.md`
- Recovered sources: PR #22 `validation_logger_artifacts_plan.md`, PR #40
  `live-suite-triage-plan.md`, PR #38 `validation_suite_refactor_plan.md`
