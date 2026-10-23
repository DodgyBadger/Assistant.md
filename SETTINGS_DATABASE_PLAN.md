# Settings Database Implementation Plan

Status: implementation authorized on `dev/settings-UI-upgrade`.

## Goal and recommendation

Move mutable installation settings, model entries and aliases, provider configuration, and tool configuration from runtime YAML to a settings-owned SQLite database. Preserve typed validation, managed catalog ownership, model resolution, and current UI behavior. Secrets already use an encrypted database and remain behind their existing service boundary. Environment-based deployment bootstrap remains environment-based.

Implement on the existing settings branch as explicitly requested. Preserve all existing commits and persistent local runtime state.

## Inspected boundaries

- `core/settings/store.py` owns typed configuration, cached loading, whole-file persistence, and automatic catalog upgrades.
- `core/settings/config_editor.py`, `core/settings/model_registry.py`, and `core/settings/model_catalog.py` own editing, alias validation, and catalog reconciliation.
- `api/services/configuration.py` includes direct YAML read/write and template repair paths that also need redesign.
- `core/database.py`, `core/database_migrations.py`, and `core/system_migrations.py` provide existing SQLite ownership and migration infrastructure.
- `core/settings/secrets_store.py` delegates to encrypted principal-owned secret storage; secret values must not enter settings tables or exports.
- ADRs 0007, 0015, 0034, and 0052 establish settings-backed resolution, subsystem database ownership, separate encrypted secrets, and durable model aliases.

## Implementation contract

- Remove GET/PUT raw-YAML settings endpoints and their request/response models. Validation uses dedicated configuration helpers. Retain the UI repair operation with a structured result and a SQLite backup. No new import/export product surface is requested.
- Store settings, models, providers, and tools as individually addressable typed records in separate tables, with structured JSON for record fields. Preserve managed IDs and canonical/alternate namespace validation. Legacy invalid aliases remain visible as actionable System diagnostics.
- Use SQLite transactions for configuration snapshots and catalog changes, a persisted revision for stale-write detection, and defensive copies for edits. Publish caches after successful commit. Enforce existing editor validation without losing legacy diagnostics.
- Import existing YAML once after structural validation and backup. Commit initialization and imported records atomically; failures remain retryable and never seed defaults over existing state. Leave legacy YAML untouched as a recovery artifact, ignored after initialization. New installations seed defaults directly into SQLite.
- Separate packaged defaults and managed catalog definitions from mutable runtime state; shipping defaults as source files does not require storing live configuration in YAML.

## Testable slices

1. Add the settings-owned schema and repository using existing database helpers. Validate typed values, alias uniqueness, transactional rollback, and isolated runtime roots without switching production persistence.
2. Add idempotent legacy import and new-install seeding. Validate preservation of custom entries, alternate aliases, invalid legacy configuration diagnostics, failed import rollback, and restart recovery using temporary roots.
3. Switch configuration reads, edits, and catalog reconciliation to the repository. Preserve API behavior and shared model resolution; validate successful cache invalidation and failure without partial state publication.
4. Replace direct YAML file editing and repair with the agreed management operations. Validate whole-configuration changes atomically, error feedback, secret exclusion, and theme-aware UI behavior.
5. Run focused persistence, catalog, alias, and configuration scenarios, then the full deterministic integration/core profile and required production checks. Verify actual migration and configuration lifecycle events without exposing secret values.
6. Review documentation: identify affected architecture, ADR, user guide, and release-note files; present proposed text or diffs to the user, obtain explicit approval, then apply approved changes.

## Next phase

Feature development, starting with executable migration/revision contracts. Validation uses temporary roots. Documentation drafts for architecture, ADR 0053, and the v0.9.2 release note were explicitly approved and applied.

## Lifecycle events

- `settings_database_initialized`: status completed, source (legacy_yaml or defaults), database path, revision, after commit.
- `settings_database_initialization_failed`: status failed, database path, exception type; no configuration values.
- `settings_repair_completed`: status completed, database path, backup path, after commit.
- Stale writes raise a reload-and-retry error and leave persisted state and caches unchanged.

## Progress and evidence

- Implemented slices 1–4; separate record tables use JSON-validated per-record fields, and unchanged records are not rewritten.
- Added deterministic integration coverage in `settings_database_migration` and focused rollback/import/revision tests. Updated validation fixtures and optional live probes to use database-backed configuration helpers.
- Focused tests: 44 passed. Initial six-scenario run passed five; the provider-metadata repair failure was fixed and its rerun passed. Migration, repair/seed refresh, scheduler settings, and system database migration scenarios pass. Full deterministic integration/core run is in progress. The final migration/model-alias rerun passed both scenarios (`20261023_030339_514102`).
- Approved documentation applied in `docs/development/architecture.md`, ADR 0053, and `RELEASE_NOTES.md`. Installation documentation does not describe runtime YAML configuration and needs no change for this effort.

- Final review reproduced a concurrent YAML edit causing backup bytes to differ from imported bytes. Import now reads one byte snapshot and backs up those exact bytes; the new regression passes. An injected interruption after record writes also proves both configuration records and initialization metadata roll back before a retry succeeds.
- Python quality gates pass: complete Ruff, Black, and MyPy checks. All 14 Node editor checks and the vault mutation routing check pass.

- Initial full deterministic run `20261023_025814_769397`: 130/138 passed. Eight compaction scenarios exposed fixture seeding through a previous active runtime root. Validation now seeds the explicit scenario database directly; a root-isolation regression passes, and all ten scenarios in the affected-sequence rerun passed (`20261023_030837_568561`). A fresh full deterministic run is in progress against this correction.
