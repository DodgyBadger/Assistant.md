# 0010 - Validate Behavior With Scenario Artifacts

## Status

Accepted, backfilled.

## Context

AssistantMD behavior crosses runtime bootstrap, API handlers, scheduler jobs,
authoring execution, tools, file outputs, and model-facing prompt composition.
Narrow unit tests alone do not prove those contracts, and direct introspection
of internals makes validation brittle during refactors.

## Decision

Use scenario-based validation as the primary behavioral proof surface. Ordinary pytest scenarios start the real application with isolated runtime roots and assert actual outputs, persisted state and meaningful application-produced observations. The external scenario-harness plugin owns retained evidence and protected execution; AssistantMD owns application lifecycle, scenarios and project policy.

## Rationale

Scenario artifacts show what the system actually did: vault outputs, API responses, logs and run-local state. Where external output cannot establish an important decision, the application developer chooses an observation mechanism, such as structured logs. Semantic observations can establish routing, fallback, cache behavior and task lifecycle without fixing private class layout or collaborator calls as test contracts.

## Consequences

- Keep in-app services and evidence producers real. Justified external-service or environmental doubles must document their boundary and limits.
- Treat selected semantic observations as behavioral contracts when they provide necessary proof; do not require a harness-owned validation sink or instrument every implementation detail.
- Preserve readable action/expectation sequences and trustworthy failure evidence across refactoring.
- Use focused checks during development and complete applicable profiles during hardening or merge preparation, following the project validation policy.

## Evidence

- Current system map: `docs/development/architecture.md`,
  `docs/agent-guides/testing-and-validation.md`
- Recovered sources: PR #22 `validation_logger_artifacts_plan.md`, PR #40
  `live-suite-triage-plan.md`, PR #38 `validation_suite_refactor_plan.md`
