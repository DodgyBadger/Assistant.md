---
name: test-application-behavior
description: Apply scenario-based testing to real application behavior using observable outcomes and stable behavioral contracts. Use when designing, writing or changing behavioral scenarios; exclude presentation-only and documentation edits.
---

## Testing philosophy

A scenario is an executable claim about what the application must do under specified conditions. Define the inputs, actions, expected outcomes and invariants that make the behavior correct. The aim is trustworthy evidence that those claims hold. A passing result has value only while the scenario still rejects the incorrect behavior it was meant to detect.

Keep those claims stable when implementation interfaces, collaborators or strategies change. Test through an appropriate production entry point and judge the resulting behavior. Assertions about private object layout, collaborator calls or incidental formatting create maintenance work without necessarily protecting the application outcome.

Before adding or extending a test, name the plausible incorrect behavior it would catch and its consequence. If there is no meaningful behavioral consequence, do not add a test. Copy, headings, punctuation, styling, layout, decorative markup and documentation/skill wording alone need direct review or visual inspection, not automated assertions, snapshots or a red/green cycle.

For UI scenarios, assert what the user can do and what the application does in response: state changes, persisted results, rejected operations, permissions and functional accessibility. A label can locate a control without making its exact wording a contract. Assert error semantics and accessible exposure when relevant, rather than exact message prose. Assert exact output only when the value itself determines functional correctness, such as machine-consumed output or a required exported value, and identify that contract. Screenshots and traces may support diagnosis without being snapshot oracles.

Start a real, isolated application instance. Observe actual responses, files, persisted state and other application outputs. Where external output cannot establish an important decision, use meaningful internal observations exposed through a mechanism chosen by the application developer, such as structured logs. Internal observations may validate messages, state transitions and other behavior that matters to the scenario. Choose observation points that keep tests maintainable as implementation details change, and let the real application produce the evidence (e.g. via logs) rather than replacing in-app services with the expected evidence.

Keep in-app services real: do not mock application collaborators, strategies, persistence, internal messages or evidence producers to obtain the expected result. Fixtures must not manufacture application outcomes or publish the events being asserted.

External services and environmental dependencies may be mocked when live calls are impractical, slow, variable, costly or require secrets. Prefer a controlled local server where it adds useful protocol evidence; use a narrow external-boundary double when that is more practical. DNS answers, provider HTTP responses and external tool responses are examples. Keep application admission, orchestration, processing and writes real. Document beside each substitution what is replaced, why, what remains real and what the scenario does not prove. Simulated provider responses can establish application behavior given those responses; they cannot establish the live provider's correctness or deployment compatibility. Do not introduce a test-only production security bypass when controlling external inputs will suffice.

Derive expected correctness from requirements, agreed invariants, documented contracts and reviewed decisions. Implementation inspection helps find entry points and explain observations; existing implementation is evidence of current behavior, not the sole authority for what is correct. When only current behavior is known, label the result as characterization and identify unresolved expectations.

## Preserve the contract when a test fails

Treat an unexpected failure as evidence to investigate. Keep the original inputs, actions, assertions and observed outputs available while distinguishing application defects, fixture faults, environmental problems and genuine mistakes in the expectation. Conflicting requirements or invariants remain open contract questions until resolved.

An assertion that merely freezes incidental presentation is not a behavioral contract. Update or remove it when presentation changes, retaining any meaningful behavioral coverage. Do not apply contract-preservation or red/green requirements to wording, styling or documentation assertions with no behavioral consequence.

Changing an input, removing a negative case, broadening accepted results, skipping a case or weakening a state assertion can remove the demonstrated failure without fixing its cause. Assess these as changes to what the scenario proves. Do not make them solely to obtain a passing run or because the implementation currently behaves that way.

Correct a demonstrably mistaken expectation using evidence independent of the failing implementation. Explain the reason, preserve the original failure and identify any reduced scope. Revise intended behavior through the project's contract-review process. If the intended outcome remains unclear, report the discrepancy for a decision and continue independent work without presenting a narrower passing case as resolution.

## Keep the behavior sequence readable

A scenario is maintained code and can be refactored while preserving what it proves. Write it as a clear sequence: establish the relevant state, perform an action, then show the expected outcome. Keep each outcome close to the action that produces it and make significant inputs and expectations visible.

Move setup plumbing, artifact serialization, provenance checks, repetitive diagnostics and routine lifecycle management into fixtures or focused helpers. Use clear behavioral names so the scenario remains understandable without following helper internals. Keep lifecycle or diagnostic actions in the sequence when those actions are themselves the behavior being tested. Extraction should remove distractions without hiding meaningful actions or expectations.

## Apply the methodology

Read the behavior plan, repository instructions, permitted existing coverage and application fixture. Choose or extend the smallest scenario that protects a meaningful outcome or subsystem boundary. Record the contract, production actions, real fixture/dependency needs, observations and known limits before writing assertions.

For new behavior, write the acceptance case first and confirm that its failure demonstrates the missing behavior. Implement the production change and rerun the same case. Preserve the acceptance source or digest with red and green evidence. Refactor while keeping the behavioral contract intact. For existing behavior, establish an honest baseline; investigate unexpected failures under the same rules. Clearly label deliberate wrong-expectation probes and other negative controls.

Use ordinary pytest fixtures and assertions with the project's real app fixture. Retain actual outputs with scenario_run.save_json or write_text. Query application-emitted observations through scenario_run.observations, using checkpoints and correlation fields to associate them with the action under test. Tolerate unrelated metadata and extra fields. Register known secrets before retaining plugin-owned evidence; raw application files require their own policy.

Verify application cleanup through lifecycle or resource observations where it matters. Completed pytest finalizers alone do not establish that every application resource was released. Run relevant focused checks during development and broader profiles at the project's designated hardening point. Report the actual verdicts, retained evidence, expectation changes, unresolved discrepancies and coverage limits. The testing framework and this skill support that judgment; neither certifies correctness automatically.
