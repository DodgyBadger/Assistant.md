# Independent AssistantMD behavioral skill trial

## Purpose and boundary

Evaluate whether the draft develop-behavioral-scenarios skill helps an independent agent derive a useful real-application scenario from production implementation. This is a skill evaluation, not a scenario migration, parity claim or coverage admission.

The held-out reference is validation/scenarios/integration/core/file_write_directory_delete.py. Select it by filename only; do not disclose its contents, assertions or migration analysis to the authoring agent. Give the author the production directory-deletion topic and core/tools/file_write.py as the starting point.

## Isolation and inputs

- Use a fresh Sol agent without inherited conversation history.
- Provide /tmp/assistantmd-skill-blind-834lnj8h/application, a snapshot of tracked production core/api/static files, the real application fixture and its support dependencies, repository instructions and a bounded pytest configuration.
- Exclude existing tests/scenarios, migration maps/plans, prior evidence, Git history and persistent data/system state. The input manifest records the source revision and copied files; the snapshot contains no existing test source.
- Extract the draft skill into this temporary evaluation workspace. Do not install it in the user's home or publish it.
- Restrict the author to that snapshot and the installed plugin runtime API. This is a controlled-input experiment with explicit agent instructions, not an operating-system-enforced security boundary. Record inspected files and accidental exposure if any.

## Authoring and verification

1. Ask the agent to inspect production modules and write its own implementation plan and behavioral scenario without proposed assertions from the evaluator.
2. Reuse the actual application fixture, isolated runtime roots and installed plugin wheel. Keep production and fixture source unchanged. Use only the new focused case(s), including real outputs and semantic observations.
3. Preserve run artifacts, actual pytest verdicts, source provenance and a frozen scenario digest. For existing behavior, an initial green baseline is valid. Label any incorrect-expectation negative control rather than claiming a product regression.
4. Freeze the authored scenario before the evaluator reads the held-out reference. Compare the independently discovered contracts, production boundaries and observations afterward; do not feed omissions back as instructions to reproduce the reference.
5. Report usefulness, coupling, execution/isolation evidence, missing behaviors and demonstrated skill deficiencies. Keep the output as evaluation evidence, without registering it as replacement coverage or deleting the original scenario.

## Completion record

Completed independent authoring and evaluator comparison. Sol authored one real-app scenario using the public FileWrite tool, actual filesystem output and semantic sink assertions. Its focused protected run passed in 11.97 seconds, with fixture evidence showing an inactive runtime after shutdown. Ruff and Black passed. The frozen source digest is 0617f1ee766c967afb0c3371def6c29eb5ff2fb8f4d561884279c7c6b76d191c; every copied production/fixture file remained unchanged. The author reported no prohibited source access. The evaluator read the held-out reference only after checking that digest.

The authored case independently covers complete and partial directory cleanup, file-byte/sibling preservation, confirmation rejection, repeated deletion and empty-path rejection. The held-out reference additionally covers hidden/non-markdown blockers, listing cleanup candidates, truncation and later cleanup after blockers are removed. These differences are evaluation findings, not a migration parity verdict; the original remains unchanged and no new coverage was admitted.

The trial exposed an important judgment weakness. An initial expectation that "." is rejected as the vault root failed, with actual root-scoped cleanup deleting two empty AssistantMD directories in the disposable vault. The author switched the input to "" and described the original expectation as mistaken based on the implementation. Evaluator review challenged that conclusion because both forms resolve to the same root and production states a root-protection intent. The author agreed that the dot-path behavior remains an unresolved contract discrepancy and possible guard bypass. The green empty-path case does not resolve it. Preserve the initial failed case and do not claim a historical regression or an accepted root-cleanup contract.

Proposed narrow skill improvement: when execution contradicts an intended invariant, implementation inspection alone must not justify weakening the oracle. Preserve the failing example, disclose any narrowing and keep the discrepancy visible pending contract review. This is recorded as a proposal; the draft skill has not been changed or published by this trial.

The authored source, plan, input manifest, initial failed source, both run directories, author report and separate self-review are preserved under validation/runs/skill_evaluations/directory_delete_blind_20261028_834lnj8h. Start with evaluator-assessment.md for the comparison and limitations. The snapshot-path assertion in the authored test is trial provenance instrumentation and should not become a permanent product contract.

Protected documentation, full-suite execution, application behavior changes and remote publication were outside this trial. No existing scenario or registry admission was changed.

## Revised methodology skill rerun

Rerun requested after the core draft was renamed test-application-behavior and revised to lead with the testing philosophy, preservation of behavioral expectations, developer-chosen internal observations and real application-produced evidence. Use skill revision 31c0b5c48ab5263babe0e736d6ba0b625fd8d67d from scenario-harness.

Use a new Sol agent without inherited history and /tmp/assistantmd-skill-rerun-hyol076k/application. Reconstruct exactly the same 484 production/fixture/instruction files from the original source revision 91d1837220b115f4d3f6e92f83b69f3f07bd3e83, with the same bounded pytest configuration and installed wheel environment. The new input manifest records the revised skill digest. Supply the same directory-deletion topic and starting module, without the original scenario, earlier authored cases, failure details, evaluator conclusions or prior reports.

Keep the authoring instructions equivalent to the first run apart from the skill name and workspace. Evaluate the resulting contract, real actions, observations and handling of any failure before providing feedback. Preserve source and evidence, and verify production/fixture source bytes remain unchanged. A single unconstrained authoring trial may choose different cases; a passing result without a comparable discrepancy does not establish that test weakening has been prevented.

Completed: baseline and final focused runs each passed one real-app scenario, with the final run taking 11.44 seconds. Ruff and Black passed. The author explicitly labelled implementation-described behavior as characterization, recorded meaningful actual outputs/internal events and observed runtime context release. The frozen source digest is 4e391843cd823865afeea4d29f36b6c146d5c6f6cb222f7ab35b700ac11da255. All 484 production/fixture/instruction files were byte-compared with the starting revision and remained unchanged; the exact supplied skill digest also matches the manifest. The author reported no prohibited access or accidental exposure.

No behavioral failure occurred. The author reported no changed behavioral expectations, and the only reported source correction was import ordering. The root rejection input was empty string throughout; equivalent root spellings were not exercised. This demonstrates useful independent authoring and more explicit reporting of correctness limits, but does not establish that the revised skill prevents weakening a failing contract. A fixed independently justified failing-contract trial would be needed to assess that separately. Keep the original dot-path discrepancy unresolved.

The final tree snapshot helper excludes the application's AssistantMD metadata subtree, limiting any broad unchanged-vault claim. Module import-path assertions are evaluation provenance checks rather than durable application behavior. The scenario was not admitted as replacement coverage.

Evidence is preserved under validation/runs/skill_evaluations/directory_delete_revised_skill_20261028_hyol076k, including the new source, author plan, input manifest, exact skill, baseline/final reports and evaluator-assessment.md. No migration admission, product fix, full-suite execution, skill publication or remote push occurred.

## Final trial with independent requirements and readability guidance

The user requested a final fresh-agent trial with a requirements document, assessing both behavioral quality and clean, readable code. Use a new Sol agent, the same 484 production/fixture/instruction files at source revision 91d1837220b115f4d3f6e92f83b69f3f07bd3e83, the existing installed wheel environment and the core skill at revision 7c9ec19a7209a6f2068046d1c0f32cef7a12f53e. The fresh workspace is /tmp/assistantmd-skill-requirements-6qjyjja0/application.

Provide requirements.md as an evaluator-authored acceptance contract, explicitly distinguished from a historically approved product specification. It covers exact confirmation, empty-tree removal, partial cleanup preserving hidden/non-Markdown files and other bytes, safe repeated requests, rejection of all relative root spellings including empty path, dot and dot-slash, and truthful operation evidence. It prescribes no helper names, logging API, private class structure or result-field schema. Its input SHA256 is 5de307e47705e28fac22c691a132ad44ba4eda03bd3799ef739c4a9467677b5b. This requirement set establishes an independent basis for assertions during the exercise; it does not approve a product behavior change.

The author receives no original scenarios, previous authored cases, earlier failure details, evaluator feedback, reports or Git history. Production, existing fixtures, requirements and the supplied skill remain immutable. New local fixtures/helpers are permitted. Preserve the authored sources at first execution, all subsequent revisions, actual verdicts and the final source digests.

Assess the output against these criteria before giving feedback:

- Trace the actual assertions and evidence to requirements R1–R6; distinguish absent coverage from observed failures.
- Use real app startup, production operations and actual outputs/state. Internal observations, where useful, must originate from the application's chosen instrumentation.
- Read the scenario as a state/action/expected-outcome journey with significant inputs and expectations visible. Evaluate useful extraction rather than minimizing line counts.
- Move routine setup, serialization, provenance checks and lifecycle plumbing into fixtures or focused helpers; avoid hiding the behavior inside opaque helpers or building an unnecessary framework.
- Preserve any failing acceptance conditions and report requirements/implementation discrepancies honestly. Record rationale for changes rather than changing inputs or weakening assertions to obtain green.

Completed: final focused execution produced 4 passed and 2 failed in 19.74 seconds, with Ruff and Black passing. R1–R4 and supporting operation-evidence assertions pass. R5 passes for empty string and fails for dot/dot-slash: both remove empty user and application-managed directories instead of rejecting root cleanup. The author preserved both failing parameter cases and did not revise the requirement. All six cases emitted inactive-runtime teardown evidence.

The author retained three source/run revisions while correcting setup: startup was moved after vault creation, and a real FileWrite mkdir initialized lazy mutation identity before preservation snapshots. Structural comparison confirms all 34 original acceptance assertions remain in the final source. These corrections establish behavior for initialized vaults; first-operation bookkeeping on an uninitialized vault remains a separate limit, not a resolved green claim. The original production/fixture/instruction bytes, requirements and skill digest were independently rechecked and remained unchanged.

The authored module contains clearer separate state/action/outcome cases, including a multi-step cleanup/retry/explicit-file-deletion journey. Significant paths and expectations remain visible; serialization, provenance and repeated evidence checks are extracted, and shutdown is fixture-owned. Helpers and the provenance fixture still occupy much of the same module; moving them into support/conftest files would further improve presentation. An unused app argument in the evidence assertion helper is a minor refactoring opportunity. Keep this frozen output intact as trial evidence.

Frozen source SHA256: 8b0327579f25103ea15b1f9bcb4e5b2e9a94ee0829f522cf53edc3703a49db3c. Requirements, skill, authored source, initial/final revisions, all run artifacts and evaluator-assessment.md are preserved under validation/runs/skill_evaluations/directory_cleanup_requirements_20261028_6qjyjja0. The combined requirements and revised skill produced stronger coverage, more readable test bodies and preserved real failures in this instance; their individual causal contributions cannot be separated by this trial. No product fix, migration admission, protected documentation change, full-suite run or remote publication occurred.
