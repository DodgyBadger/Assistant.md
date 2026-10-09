# Nightly Workflow Failure Hardening

## Scope and evidence

The October 9 nightly runs in Ashley_Personal and Ashley_NCC failed during embedding preflight after a successful HTTP response. Earlier Logfire records preserve `TypeError: process() takes no keyword arguments`; an offline compressed-response probe reproduces it with HTTPX2 2.12.0 and Brotli 1.1.0. Brotli 1.2.0 adds the output-buffer argument required by HTTPX2 ([upstream release](https://github.com/google/brotli/releases/tag/v1.2.0)). Separately, Michelle and Reilly workflows missed their scheduled start by approximately 1.4 seconds and exceeded APScheduler's default one-second grace period.

The same incompatible HTTPX2/Brotli versions are locked on main, so the evidence does not establish that this branch introduced the dependency mismatch. SDK upgrades may have exposed it, while this branch's generic failure reporting obscured the cause. The repair targets the reproduced incompatibility rather than assuming a session-memory regression.

## Testable slices

1. Add a deterministic Brotli-compressed OpenAI embedding response scenario through the real SDK, Pydantic AI, and vector service with a local mock transport. Require Brotli >=1.2.0,<2 and update only that dependency in the lockfile; do not change model configuration, suppress compression, or spend live API quota.
2. Restore activity-visible embedding-preflight failure diagnostics and add bounded frame-location traces to session_ops failures. Recognize the known decoder incompatibility with an actionable safe message, but never put arbitrary exception values, source text, locals, private session identities, tool arguments, or transcript contents into diagnostics. Tool-facing failures remain sanitized. Protect the existing transcript-retrieval privacy scenarios.
3. Give scheduled workflows a 60-second misfire grace period on creation, replacement, and synchronization of persisted jobs. Leave system jobs and coalescing behavior unchanged. Verify newly scheduled and existing jobs carry the policy and a short delay actually executes rather than misfires.

## Event contracts and validation

- `session_ops_failed`: preserve operation/context/run/tool identity, error_type, issue, and failed status; add a safe bounded traceback and an actionable known compatibility diagnostic without exposing tool inputs.
- `session_summary_embedding_preflight_failed`: activity-visible failed status, model_alias, error_type, safe error, and bounded traceback. Successful preflights remain validation-only.
- Validate compressed embedding decode, failure activity and sanitized tool returns, scheduler admission and persisted-policy refresh, existing session_ops privacy and summary-index contracts, then the full deterministic integration/core profile and complete Ruff/Black/MyPy gate.

## Runtime-state boundary

Do not rerun production workflows or mutate live summaries, chats, settings, or credentials. A rebuild installs the compatible dependency; normal workflow synchronization applies the grace policy to existing jobs. Local validated commits are in scope; pushing requires the user's request.

## Completion

All three slices are implemented. The compressed-response scenario reproduced the production TypeError with Brotli 1.1.0 (`20261009_181240_031370`) and passed with Brotli 1.2.0 (`20261009_181311_082504`). The real chat-tool scenario verifies failed embedding preflights are visible in durable activity, carry safe stack locations, preserve the prior summary, and do not expose private exception payloads in logs or persisted tool results. Workflow lifecycle coverage verifies creation, persisted-policy refresh without changing the next run, replacement, and actual APScheduler admission after a two-second delay.

The full deterministic `integration/core` profile passed 134/134 scenarios (`20261009_181512_241337`). `uv run ruff check .`, `uv run black --check .`, `uv run mypy api core`, and `git diff --check` passed. No live service calls or production workflow reruns were used to verify the repair.
