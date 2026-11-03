---
name: review-behavioral-scenarios
description: Review scenario changes for meaningful behavioral coverage, stable semantic assertions and trustworthy execution evidence.
---

For each new assertion, identify a plausible incorrect behavior it rejects and the consequence. Flag tests added solely for UI wording, headings, punctuation, styling, layout, decorative markup or documentation/skill prose as unnecessary clutter. Presentation-only changes need no new tests or red/green evidence. Recommend updating or removing existing presentation-only assertions while retaining behavioral coverage. Exact output checks need a functional reason, such as a machine-consumed contract; a UI label used as a locator does not make its wording a requirement. Check functional accessibility through usable actions and accessible state/error exposure rather than exact prose. Diagnostic screenshots do not need snapshot assertions.

Compare the requested contract, implementation and scenario diff. Determine whether real production entry points produce the asserted outputs, persisted state or observations. Trace internal observations to the application's chosen instrumentation; test-emitted expected records are not proof. Distinguish controlled external inputs from substituted application outcomes. External-service doubles are permitted when justified by practicality, speed, determinism or secrets; in-app service mocks are not. Check that each substitution documents its boundary, reason and limits in code, and leaves the application producing its own outputs and observations.

Read the scenario as a behavior sequence: can the reader follow the relevant state, actions and expected outcomes without working through setup or diagnostic plumbing? Suggest refactoring distracting mechanics into fixtures or helpers while keeping meaningful inputs and expectations visible.

Check whether the oracle rejects a plausible incorrect behavior. Reject weakened expectations that hide a failure without an explicitly revised behavior contract, even when tests become green. Prefer selected semantic fields over snapshots of metadata, internal classes or collaborator call order. Check correlations, timing assumptions, negative cases and isolation between reused workers.

Inspect actual pytest phase verdicts and artifacts, including setup and teardown failures. Verify that a claimed red result demonstrates missing behavior and that migration negative controls are labelled. Passing output alone cannot establish cleanup after a dead worker. Assess admission, live-service limits and broader-profile evidence against the consuming project's policy.

Report actionable findings with affected locations, the behavioral consequence and supporting evidence. Separate observed defects from coverage limits. Skills guide judgment; they neither grant approval nor mechanically certify test quality.
