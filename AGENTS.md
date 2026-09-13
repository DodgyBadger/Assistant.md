# Agent Operating Guide

AssistantMD is a single-user, markdown-first agent system with a Python backend, FastAPI API/UI surface, and scenario-based validation harness.

## Durability Principle

This codebase will outlive any single contributor. Every shortcut becomes future maintenance cost. Fight entropy and leave the codebase better than you found it. Fail fast so we can see and then fix bugs.

## Always True

- Python version: `3.13`.
- Non-standard build command: `npm run build:css` compiles `static/input.css` to `static/output.css`.
- Validation ownership: maintainers run full validation (`python validation/run_validation.py ...`); agents should request results instead of running the suite.
- Validation-first delivery: follow [Testing and Validation](docs/agent-guides/testing-and-validation.md#validation-first-workflow).
- Planning output is required: planning work must end with a root-level markdown implementation plan, either by creating a new file or updating the existing one for that effort.
- Documentation should describe the current contract only. Do not document behavior as “instead of the old way” or teach migrations inside product docs unless the task explicitly calls for migration guidance.
- Markdown prose must not use arbitrary source line breaks. Keep each paragraph and list item on one source line and let the viewer wrap it; retain line breaks only when Markdown structure or intentional rendering requires them.
- Never commit real API keys or populated `system/secrets.yaml`.
- Treat the configured data and system roots as persistent runtime state during local testing. `scripts/dev run` defaults them to repository `data/` and `system/`; production containers use `/app/data` and `/app/system`.
- Commit message expectations live in [Git and Review Workflow](docs/agent-guides/git-and-review.md).

## Choose the Current Phase

Start with the phase that matches the current request. Open only that guide first. Pull in reference docs only when the phase guide points to them or the task needs deeper detail.

- [Planning](docs/agent-guides/planning.md) Use when the request is ambiguous, spans multiple subsystems, changes contracts, or needs an implementation strategy before coding.
- [Feature Development](docs/agent-guides/feature-development.md) Use when the next step is to implement or refactor code to deliver behavior.
- [Testing and Validation](docs/agent-guides/testing-and-validation.md) Use when defining validation scenarios, adding assertions, or verifying behavior with targeted local checks.
- [Refactor and Hardening](docs/agent-guides/refactor-and-hardening.md) Use after behavior is working and validated to reduce drift risk, improve structure, tighten errors, and ensure adequate logging.
- [Commit and Review Prep](docs/agent-guides/commit-and-review-prep.md) Use when shaping the final diff, checking commit boundaries, and preparing a clean commit for review.
- [Cleanup Before Merge](docs/agent-guides/cleanup-before-merge.md) Use for final polish: docs alignment, temporary-code removal, invariant checks, and merge-readiness review.

## Reference Docs

- [Architecture Overview](docs/development/architecture.md)
- [Project Structure](docs/agent-guides/project-structure.md)
- [Coding Standards](docs/agent-guides/coding-standards.md)
- [Activity Logging](docs/agent-guides/activity-logging.md)
- [Logfire](docs/agent-guides/logfire.md)
- [Git and Review Workflow](docs/agent-guides/git-and-review.md)
