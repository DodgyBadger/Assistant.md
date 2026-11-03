---
name: plan-behavioral-change
description: Plan an application behavior change around executable acceptance conditions and meaningful red/green evidence. Use before changing functional behavior, not for presentation-only or documentation edits.
---

First identify the functional behavior that changes and a plausible incorrect outcome a test should reject. Copy, styling, layout, decorative markup and documentation/skill prose alone do not warrant tests or a red/green cycle. For those changes, use direct review or visual inspection and stop this testing workflow. Do not invent a behavioral requirement to justify a test. When a change mixes presentation and behavior, scope acceptance checks to the behavior.

Inspect repository instructions, existing scenarios and coverage before choosing a new scenario, an extension or a narrower check. Describe the requested behavior through production entry points, permitted inputs, state transitions and important negative cases. Record unresolved specification questions before implementing assumptions.

Choose the smallest observations that distinguish success from failure: actual responses, persisted state and semantic application events with selected required fields. Identify real startup, isolated storage, dependencies, service availability and timing costs. Keep in-app services real. Plan narrow external-service doubles when live dependencies are impractical, slow or require secrets; record the boundary and the resulting coverage limit. Simulated application outcomes are not full-stack evidence.

Do not plan assertions that freeze UI text, headings, CSS classes or snapshots of presentation. Exact values need an identified functional contract, such as machine-consumed output. A control's label may locate an action without making its wording an acceptance condition.

Write or update the project's implementation plan around an executable acceptance condition. Include the selected checks, expected meaningful red result, implementation steps, green rerun, evidence location, known limits and required project approvals. Follow the project's plan location and format.

For new behavior, implement the scenario first and run it against the real application. Confirm that its failure demonstrates the missing behavior; an import, setup or configuration failure is not useful red evidence. Implement the behavior, rerun the same acceptance condition and preserve the oracle through refactoring. Change an expectation only when the intended behavior itself has been revised and reviewed.

For already-working behavior, record a genuine green baseline. Clearly label wrong-expectation or other negative controls; do not present them as a real product defect or an implementation mutation. Plan broader checks according to project cadence. Respect authorization boundaries, including documentation approval rules.
