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
