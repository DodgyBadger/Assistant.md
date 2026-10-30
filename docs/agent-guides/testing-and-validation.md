# Testing and Validation

AssistantMD uses ordinary pytest scenarios with the commit-pinned `scenario-harness` development dependency. The package owns evidence recording and protected pytest execution. AssistantMD owns its application fixtures, scenario library, external inputs and admission policy.

## Skills

Use the installed skill that matches the task; other agents can read the linked `SKILL.md` directly. Install these generic skills in Codex from the same harness revision pinned in `pyproject.toml`; Python dependency installation does not install agent skills.

- Behavioral planning: [plan-behavioral-change](https://github.com/DodgyBadger/scenario-harness/blob/941ede024673a0c8496dee908e1d6066cbaa0c05/skills/plan-behavioral-change/SKILL.md).
- Writing or changing scenarios: [test-application-behavior](https://github.com/DodgyBadger/scenario-harness/blob/941ede024673a0c8496dee908e1d6066cbaa0c05/skills/test-application-behavior/SKILL.md).
- Reviewing scenario changes: [review-behavioral-scenarios](https://github.com/DodgyBadger/scenario-harness/blob/941ede024673a0c8496dee908e1d6066cbaa0c05/skills/review-behavioral-scenarios/SKILL.md).
- Investigating failures: [diagnose-scenario-failures](https://github.com/DodgyBadger/scenario-harness/blob/941ede024673a0c8496dee908e1d6066cbaa0c05/skills/diagnose-scenario-failures/SKILL.md).
- Changing app-fixture integration or execution policy: [integrate-scenario-testing](https://github.com/DodgyBadger/scenario-harness/blob/941ede024673a0c8496dee908e1d6066cbaa0c05/skills/integrate-scenario-testing/SKILL.md).

The skills own the reusable testing methodology. This guide defines AssistantMD's setup, locations, commands and project policy.

## Profiles and commands

Install the locked test environment with `uv sync --frozen --all-extras`. Run commands from the repository root.

Run one scenario file, or append `::test_name` to select a case:

```bash
scripts/dev scenario validation/scenarios/integration/core/test_gmail_selected_account_tokens.py
```

Run the complete deterministic backend profile once the branch is stable:

```bash
uv run python -m pytest validation/scenarios/integration/core
```

Run browser scenarios separately against the actual UI/backend with the app-owned Playwright fixtures:

```bash
scripts/dev setup --browser
uv run python -m pytest -c validation/scenarios/browser/pytest.ini
```

Core scenarios must not depend on live models, public services or real credentials. Browser scenarios use synthetic data and controlled local dependencies. Experimental and live-service cases remain opt-in. Keep focused feedback during development; run complete applicable profiles during hardening or merge preparation, and repeat only when subsequent changes affect their proof.

## Application-owned structure

- `validation/scenarios/integration/core/`: deterministic backend behavior scenarios.
- `validation/scenarios/browser/`: browser scenarios and their separate pytest configuration.
- `validation/scenarios/application.py`: the real application fixture, actions and observation bindings.
- `validation/scenarios/support/` and `validation/support/`: app-specific setup, lifecycle, external inputs and policy helpers.
- `validation/registry/`: source admission, evidence mappings and reviewed coverage gaps.
- `validation/runs/`: generated reports and per-case evidence; keep run artifacts out of commits.
- `validation/legacy/`: retained historical sources, excluded from canonical collection.

## Validation-first workflow

Backend scenario modules use `pytestmark = pytest.mark.scenario` and the `app` fixture. Prepare required vault inputs with `app.vault` and `app.write`, start the real runtime with `await app.start()`, then exercise actual API, tool or service actions. Browser scenarios use the `browser_app` and `ui` fixtures in their profile to own the HTTP listener and Playwright diagnostics.

1. Define meaningful acceptance conditions from requirements and agreed invariants. Update the root implementation plan with selected scenarios, real inputs, expected outcomes and evidence limits.
2. Start an isolated real app using the `app` fixture. Keep in-app services, persistence, strategies and evidence producers real. External HTTP, DNS or executable inputs may be controlled when live dependencies are impractical; document the boundary, reason and limits beside the fixture.
3. Write the behavior sequence as actions followed by nearby expectations. Move setup and diagnostic mechanics into helpers. For new behavior, demonstrate a meaningful failing acceptance case before implementation, then rerun the same contract after the change.
4. Assert actual responses, files, persisted state and application-produced semantic observations. Internal messages and decisions may be tested when they matter; use correlations and selected fields that survive implementation refactoring. Do not manufacture expected observations or freeze editable prompt prose, incidental metadata or frontend markup.
5. Investigate failures without weakening expectations to obtain green output. Preserve the original failure and distinguish app defects, fixture faults, environmental failures and independently demonstrated expectation mistakes. Report unresolved contracts explicitly.

## Evidence and execution

Each invocation creates a fresh directory under `validation/runs/`, containing `report.json`, `report.md` and worker/case artifacts. To choose a location, pass `--scenario-root PATH`; the selected directory must be empty. Read setup, call and teardown results together with original app outputs and recorded cleanup status.

Protected execution uses a local Linux worker, controller deadlines and worker-crash evidence. The app fixture owns startup and shutdown. Completed pytest finalizers do not by themselves prove every application resource was released. Process cleanup covers verified inherited worker sessions; deliberately detached sessions are outside that guarantee. Core Python-network and subprocess admission guards are not an OS sandbox.

Register known secrets before retaining plugin-owned evidence. Producer logs are not rewritten by the plugin. Browser screenshots and traces are binary artifacts and are not redacted; use synthetic credentials and content.

## Admission and CI

Canonical collection requires exact file hashes and case identities in `validation/registry/pytest-admission.json`, including admitted support sources. Review and update admission records intentionally when those sources change. A changed manifest authorizes neither an altered behavioral expectation nor CI governance approval.

```bash
uv run python -m validation.support.inventory
uv run python -m pytest -c .github/pytest.ini .github/tests/test_validation_policy.py .github/tests/test_pytest_collection_policy.py .github/tests/test_validation_governance_ci.py
```

GitHub's `validation-governance` workflow audits protected validation changes and binds manual environment approval to the candidate commit and base. Scenario results and governance approval are separate requirements. Current deferred coverage is recorded in the root `validation-gap-review.md` and `validation/registry/pytest-gaps.json`; deferral is not completed coverage.

Python quality checks follow the [Production Python Quality Gate](coding-standards.md#production-python-quality-gate). The independent npm audit uses `node --test .github/tests/test_npm_audit_policy.cjs` and `node scripts/audit-npm.cjs` after dependency installation.
