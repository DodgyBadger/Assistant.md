# Testing and Validation

## Skills

Use the appropriate scenario-harness skill:

- Planning: [plan-behavioral-change](https://github.com/DodgyBadger/scenario-harness/blob/93bfc8dab87c8436775af05876fb32a2610230fd/skills/plan-behavioral-change/SKILL.md).
- Writing scenarios: [test-application-behavior](https://github.com/DodgyBadger/scenario-harness/blob/93bfc8dab87c8436775af05876fb32a2610230fd/skills/test-application-behavior/SKILL.md).
- Review: [review-behavioral-scenarios](https://github.com/DodgyBadger/scenario-harness/blob/93bfc8dab87c8436775af05876fb32a2610230fd/skills/review-behavioral-scenarios/SKILL.md).
- Diagnosis: [diagnose-scenario-failures](https://github.com/DodgyBadger/scenario-harness/blob/93bfc8dab87c8436775af05876fb32a2610230fd/skills/diagnose-scenario-failures/SKILL.md).
- Fixture integration: [integrate-scenario-testing](https://github.com/DodgyBadger/scenario-harness/blob/93bfc8dab87c8436775af05876fb32a2610230fd/skills/integrate-scenario-testing/SKILL.md).

Install skills from the harness revision pinned in `pyproject.toml`; Python dependency installation does not install agent skills. Read the linked `SKILL.md` when the skill is unavailable in your agent.

## Setup and commands

Run from the repository root.

Install dependencies:

    uv sync --frozen --all-extras

Run one backend scenario; append `::test_name` to select a case:

    scripts/dev scenario validation/scenarios/integration/core/test_gmail_selected_account_tokens.py

Run the complete backend profile:

    uv run python -m pytest validation/scenarios/integration/core

Set up and run browser scenarios:

    scripts/dev setup --browser
    uv run python -m pytest -c validation/scenarios/browser/pytest.ini

Backend scenarios default to four pytest workers locally and in CI. Override the worker count with `-n`, for example `uv run python -m pytest validation/scenarios/integration/core -n 2`. Browser scenarios run with one worker.

Core scenarios must not depend on live models, public services or real credentials. Browser scenarios use synthetic data and controlled local dependencies. Experimental and live-service cases remain opt-in. Validation cadence is defined in [AGENTS.md](../../AGENTS.md).

## Application integration

Backend scenarios use `pytestmark = pytest.mark.scenario` and the `app` fixture from `validation/support/application.py`. Browser scenarios use `browser_app` and `ui`.

- `validation/scenarios/integration/core/`: backend scenarios.
- `validation/scenarios/browser/`: browser scenarios and configuration.
- `validation/support/`: application fixtures and helpers, grouped by subsystem.
- `validation/fixtures/`: sample documents, images and workspace files.
- `validation/registry/`: source admission and coverage accounting.
- `validation/runs/`: ignored reports and artifacts.
- `validation/legacy/pending/`: deferred references excluded from collection.

## Admission and CI

Canonical scenario and support sources require matching hashes and test identities in `validation/registry/pytest-admission.json`. Update the admission records when those sources change.

    uv run python -m validation.support.inventory
    uv run python -m pytest -c .github/pytest.ini .github/tests/test_validation_policy.py .github/tests/test_pytest_collection_policy.py

Human PR review assesses testing policy and changed expectations. The required `validation` CI check runs dependency/security checks and both scenario profiles.

Python checks follow the [Production Python Quality Gate](coding-standards.md#production-python-quality-gate).
