# Project Structure

- `core/`: backend runtime, tools, workflow parsing, ingestion, scheduling, settings.
  `core/access_store/` owns the shared SQLite connection and transaction boundary
  for encrypted secrets and connection metadata; domain policy remains in its
  owning packages.
- `api/`: FastAPI API models, endpoints, and service wrappers.
- `validation/`: scenario framework, runners, templates, and run artifacts (`validation/runs/`).
- `static/`: frontend assets (`index.html`, `app.js`, Tailwind input/output CSS).
- `docs/`: setup and usage guides plus contributor architecture, ADR, and
  development references under `docs/development/`.
- `docker/`: production container definition.
- `pyproject.toml` and `uv.lock`: Python dependencies and shared tool
  configuration.

## Module Placement Rules
- Keep side-effect-heavy logic in `core/` services.
- Keep API layer thin (`api/` should orchestrate, not own business logic).
- For new Python modules, place the primary class/function near the top; helpers follow below or live in utility modules.
