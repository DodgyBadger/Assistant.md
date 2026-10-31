# Validation migration merge plan

## Objective and current state

Merge the validation migration through a pull request with the required core-suite gate intact. This plan authorizes no push, pull request creation, repository-settings change or merge by itself. Next phase: branch hardening and CI preparation.

Inspected on 2026-10-31: `origin/main` is `086b219768fc666f3888e2ee14d2b1e2d3e7c417`; the working branch is `dev/test-cleanup` at `40b1a3091911fad643b25dbe0edf4aec5df513c8`. Main is an ancestor of the branch, with 147 branch commits and no main-only commits. The branch is four commits ahead of its remote, with a clean working tree. Refresh these facts before execution.

GitHub requires the `validation` check, an up-to-date branch, administrator enforcement and linear history. The required job depends on `security-audit`. There is no existing migration PR. Main still uses the old runner; this branch's pull-request workflow installs the commit-pinned plugin and runs the canonical pytest suite. Ordinary `pull_request` CI uses the proposed merge revision, so installing the new workflow, dependency and suite together does not require restoring the old runner or bypassing the check.

The migration also changes production code and browser behavior: 31 files under `core`, `api`, `static` and `scripts` differ from main. Review those changes separately from scenario translations. Historical targeted successes are useful diagnostic evidence, but there is no retained, passing full-profile result for the final branch. Earlier run artifacts were deleted at the user's request; do not reconstruct them or treat their historical paths as available evidence. The reported fixed failure still requires confirmation in a fresh complete run.

## 1. Resolve the governance bootstrap explicitly

The `validation-governance` environment already requires approval by DodgyBadger and allows protected branches. However, main does not contain the governance workflow or its trusted checker. The proposed workflow uses `pull_request_target` and checks out the trusted base; it cannot enforce its own initial installation. Branch protection currently requires only `validation`, whereas the new checker expects both `validation` and `validation-governance` once installed.

Recommended route: one migration PR, with explicit human review of the initial governance installation and migration diff. Keep `validation` required throughout. Do not add `validation-governance` as a required check before the trusted workflow exists on main, and do not manually manufacture a successful status. After the passing migration PR merges, add `validation-governance` to required checks while preserving strict checks and administrator enforcement. Verify enforcement with a disposable draft PR before allowing further merges. This is a one-time installation review; it is not evidence that automated governance protected the installation PR.

If automated manual-environment approval must protect the migration PR itself, use two PRs instead: first install the reviewed workflow, trusted checker, policy and required catalog on a small bootstrap branch from main, with main's existing core suite passing; then require the governance check, bring the migration branch up to date, and submit the migration PR under both gates. The bootstrap PR still needs initial human trust and adds an old-runner validation cycle. Do not split unrelated migration code into that PR just to make it pass. Choose between these routes before publishing.

## 2. Prepare a CI-ready candidate

- Review the production and UI diffs for justified fixes, fixture seams, persistent-state handling and external-only substitutions. Confirm no real secrets, populated `system/secrets.yaml` or persistent runtime data enter the diff. The test roots must remain isolated from repository data and system roots.
- Resolve the root Markdown allowlist before expecting CI to pass. Four tracked files currently violate it: `astra-validation-framework-design-plan.md`, `browser-validation-implementation-plan.md`, `pytest-scenario-library-refactor-plan.md` and `validation-gap-review.md`. This plan will be a fifth. Delete completed working notes or relocate still-useful material; retain the deferred gap list in an agreed maintained location. Do not weaken the allowlist merely to get this branch green. Any move into `docs/` requires the separately drafted user approval required by AGENTS.md. Remove or relocate this root plan last, after its decisions are settled, and check internal links.
- Confirm a clean installation from the committed lockfile using Python 3.13 and `uv sync --frozen --all-extras --no-install-project` in an isolated checkout/environment. `uv lock --check` currently passes. The pinned `scenario-harness` commit `941ede024673a0c8496dee908e1d6066cbaa0c05` is accessible in its public repository; verify the installed package comes from that pin and has no sibling-checkout dependency.
- Inspect workflow startup and security jobs before spending another complete-suite cycle. The prior remote run at `39365c9` failed without available job logs; its cause is not established. Confirm the candidate workflow is accepted, dependency audits work, inventory passes and the expected job names are emitted. Workflow failures are blockers even when local scenarios pass.
- Run the production quality gate (`uv run ruff check .`, `uv run black --check .`, `uv run mypy api core`), both routing checkers, the structural inventory and the existing repository policy checks. Confirm only canonical scenarios collect and deferred legacy references do not.
- Recheck the runner callback and vault-rescan cases directly before the full run, using their existing behavioral assertions. If either still fails, diagnose the actual evidence; do not skip, xfail, replace application services or relax the expected behavior solely to pass the gate.

Commit these preparation changes at logical boundaries. Re-fetch main, resolve any new base changes and freeze the resulting candidate before the expensive runs. There is no need to rebase the current branch solely for freshness while main remains its ancestor.

## 3. Establish fresh full-profile evidence

Run the complete deterministic core profile from the frozen candidate, with a new run root: `uv run python -m pytest validation/scenarios/integration/core --scenario-root validation/runs/premerge-<candidate-sha>`. Use the committed execution policy, including its one-worker default. Collect the expected case count from that candidate rather than assuming a historical count is still authoritative.

A passing run must have normal collection, every expected case accounted for, no newly introduced skips/xfails, completed cleanup and no unexplained worker loss or incomplete evidence. Record the tested Git SHA, command, environment, result and run report. Retain these new artifacts through merge review; this creates new evidence and does not restore or archive the deleted runs.

The current `validation` CI job also runs the deterministic browser profile after core and builds its assets. Browser failures therefore block this branch under the existing proposed workflow even though the merge discussion focuses on core. Run that profile with its own configuration and run root, using built CSS and the installed Playwright Chromium. Keep it as an additional requirement; separating its job or changing required checks would be a distinct policy decision.

If any case fails, pause merge preparation, classify the failure and fix it without manufacturing green output. Re-run the affected cases to confirm the diagnosis, then re-run the complete applicable profile after the correction. Report a failed full run honestly; do not cite its successful majority as gate completion. Changes limited to prose may not need another local full run, but the final PR still needs a green CI run on its actual proposed merge revision.

## 4. Publish and use the actual merge gate

After the bootstrap route and preparatory changes are agreed and complete, push the candidate and open the PR against main. Describe the framework replacement, production/UI fixes, deferred coverage and fresh verification without claiming total code coverage. The local full run reduces avoidable CI iterations; GitHub's required `validation` check remains the merge authority.

Wait for `security-audit` and `validation` on the PR's proposed merge revision. Do not treat a workflow-dispatch run on a branch head, a run from an earlier SHA, or a focused test run as a substitute for required PR checks. Review and retain the uploaded artifacts. If main moves, update the candidate and obtain passing checks on the resulting combination before merging. Any scenario or governance changes after review require review of that new revision.

Use squash merge unless a different linear-history strategy is agreed. Do not use an administrative bypass, disable protection or push directly to main. Confirm the PR's current head and base match the reviewed candidate and that GitHub reports the required check successful immediately before merging.

## 5. Activate and verify governance

For the recommended single-PR installation route, immediately after merge configure branch protection to require both `validation` and `validation-governance`, preserving the existing strict, administrator and linear-history settings. Repository settings changes require explicit authorization when execution reaches this step; this plan does not make them yet.

Use a disposable draft PR containing a benign protected validation change to verify that the trusted-base audit runs, requires the manual environment approval, records approval for the actual head/base and does not accept stale approval after a new push. Also exercise the unchanged/non-sensitive path. Do not merge the canary or claim governance is operational from the existence of the environment alone. Diagnose any configuration mismatch before another merge.

Verify the resulting main checkout can install the locked dependency and collect the canonical suite, and link the passing PR check and artifacts. Run another complete profile only if the merge result or subsequent changes introduce behavior not covered by the tested merge revision.

## Documentation review and final decisions

Before implementation, settle the bootstrap route and the destination or deletion of working plans and the deferred list. Relevant maintained docs are AGENTS.md, `docs/agent-guides/testing-and-validation.md`, `docs/agent-guides/project-structure.md`, `docs/development/dev-setup.md` and ADR 0010; these were recently aligned and should change only if the agreed merge procedure exposes a current-contract gap. Present exact proposed text or diffs for any protected documentation or release-note changes and obtain explicit user approval before applying them. Root-plan creation here is planning output, not approval to edit those protected files.
