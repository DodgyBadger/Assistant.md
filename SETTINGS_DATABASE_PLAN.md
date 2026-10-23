# Settings Database Implementation Plan

Status: implementation completed and validated on `dev/settings-UI-upgrade`.

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

Implementation and hardening are complete. Validation used temporary roots; the configured local runtime state was not used as a test fixture. Documentation drafts for architecture, ADR 0053, and the v0.9.2 release note were explicitly approved and applied.

## Lifecycle events

- `settings_database_initialized`: status completed, source (legacy_yaml or defaults), database path, revision, after commit.
- `settings_database_initialization_failed`: status failed, database path, exception type; no configuration values.
- `settings_repair_completed`: status completed, database path, backup path, after commit.
- Stale writes raise a reload-and-retry error and leave persisted state and caches unchanged.

## Progress and evidence

- Implemented slices 1–4; separate record tables use JSON-validated per-record fields, and unchanged records are not rewritten.
- Added deterministic integration coverage in `settings_database_migration` and focused rollback/import/revision tests. Updated validation fixtures and optional live probes to use database-backed configuration helpers.
- Focused tests: 44 passed. Initial six-scenario run passed five; the provider-metadata repair failure was fixed and its rerun passed. Migration, repair/seed refresh, scheduler settings, and system database migration scenarios pass. Full deterministic integration/core run passed all 138 scenarios (`20261023_030852_963378`). The final migration/model-alias rerun passed both scenarios (`20261023_030339_514102`).
- Approved documentation applied in `docs/development/architecture.md`, ADR 0053, and `RELEASE_NOTES.md`. Installation documentation does not describe runtime YAML configuration and needs no change for this effort.

- Final review reproduced a concurrent YAML edit causing backup bytes to differ from imported bytes. Import now reads one byte snapshot and backs up those exact bytes; the new regression passes. An injected interruption after record writes also proves both configuration records and initialization metadata roll back before a retry succeeds.
- Python quality gates pass: complete Ruff, Black, and MyPy checks. All 14 Node editor checks and the vault mutation routing check pass.

- Initial full deterministic run `20261023_025814_769397`: 130/138 passed. Eight compaction scenarios exposed fixture seeding through a previous active runtime root. Validation now seeds the explicit scenario database directly; a root-isolation regression passes, and all ten scenarios in the affected-sequence rerun passed (`20261023_030837_568561`). The fresh full deterministic run passed all 138 scenarios against this correction (`20261023_030852_963378`).

## Final validation

- Full deterministic `integration/core`: 138/138 passed; report `validation/runs/reports/20261023_030852_963378.md`.
- Focused settings, catalog, alias, configuration, and chat-selection checks: 44 passed.
- Node editor checks: 14 passed; configuration-status JavaScript syntax check passed.
- Complete Ruff, Black, and MyPy gates passed; MyPy checked 292 production source files.
- Vault mutation routing and `git diff --check` passed.
- No experimental or live-provider scenarios were executed.
- Approved architecture, ADR 0053, and release-note changes are complete. Work remains on `dev/settings-UI-upgrade`; commits are local and have not been pushed.

## Peer hardening round

Status: complete; review scope is `cb47996..HEAD`, retaining the earlier settings/model work as the established contract.

### Stage 1: scope and contracts

- Storage: configuration records and initialization marker commit atomically; existing YAML is imported once with an exact-snapshot backup; subsequent reads use SQLite.
- Editing: shared typed editors, alias validation and managed ownership apply through API services; snapshot revisions reject stale writes; failed writes leave state and caches intact.
- Catalog and bootstrap: managed reconciliation preserves custom bindings and alias diagnostics; declared database migrations and backup ownership must remain consistent with runtime initialization.
- API/UI: raw-YAML endpoints remain absent; repair returns a structured outcome only after durable completion; errors and all status controls remain theme-aware.
- Validation and observability: explicit scenario roots protect persistent runtime state; actual activity events must identify migration/repair outcomes without private configuration values.

Three independent read-only peers are reviewing storage, API/runtime boundaries, and validation/lifecycle coverage. The primary review covers cross-cutting ownership, observable failure behavior, and operator contracts. Subsequent stages and accepted findings will be recorded here after evidence is established.

### Findings and corrections

- Medium, contract drift / legacy ownership provenance: initial `SettingsFile.model_dump()` synthesized `managed_id: null` before catalog reconciliation (`core/settings/store.py`, original import lines 188–189). This prevented the exact pre-managed Jev entry from retiring and retained its TypeSafe dependency. Source fields now remain raw through catalog reconciliation, and only the final typed snapshot is written. A migration-through-store regression failed before the change and passes afterward; both storage and boundary peers independently verified the correction, including cold reload and atomic rollback.
- Medium, failure lifecycle gap: connection/schema setup ran outside the guarded load boundary (`core/settings/store.py`, original line 161). Corrupt databases raised without the settings failure event. Acquisition is now guarded and cleanup conditional; controlled error guidance identifies the database/backup inspection step. A corrupt-database regression failed for the absent event before the change and passes afterward. The validation peer independently verified exactly one real Activity failure record and unchanged source bytes.

### Stages 2–5: ownership, lifecycle, logging, and operator review

- Shared typed editing, model registry validation, catalog ownership and revision-checked commits remain authoritative. No transport-specific persistence or duplicate runtime configuration source was introduced. Raw legacy dictionaries are retained only until provenance-sensitive reconciliation; final records still use JSON-mode typed serialization.
- Initial records, initialization marker, reconciled catalog and revision remain inside one SQLite transaction. Focused tests cover stale writes, failed-save rollback, interrupted initialization, exact source backups, retry, root isolation and unchanged corrupt database bytes.
- Actual Activity records inspected from `20261023_033059_settingsdatabasemigration`: `settings_database_initialized` (legacy_yaml/defaults), `settings_database_initialization_failed`, `settings_database_load_failed`, `managed_models_updated` (removed Jev), and `retired_builtin_providers_updated` (TypeSafe). All have stable event/status and applicable source/path/exception identities; failure guidance is controlled text, without stored values. The repair integration scenario verifies `settings_repair_completed` and its persisted SQLite backup. Initialization publishes a terminal atomic outcome rather than helper-level progress events during early logging bootstrap; system migration orchestration retains its start/terminal operation IDs.
- No frontend mutation was required by these corrections. The shared structured repair response, stale-write 409 error, theme-aware feedback and unchanged editor flows were reviewed. Configuration-status syntax checks pass; existing editor checks are rerun as focused operator evidence.
- Current schema migration v1 creates tables non-destructively. Peers noted automatic schema setup can precede startup orchestration; no current destructive migration bypass was demonstrated, so speculative future migration redesign is outside this correction. Pre-existing generic malformed-configuration exception rendering was also distinguished from new behavior and left unchanged.

### Stage 6: validation and release readiness

- All 46 focused Python checks pass; complete Ruff, Black and MyPy gates pass.
- Affected integration rerun: 3/3 passed (`20261023_033058_938256`): settings_database_migration, model_alias_resolution, system_template_seed_refresh.
- The full deterministic integration/core profile passed all 138 scenarios against the corrected behavior (`20261023_033109_006730`); report: `validation/runs/reports/20261023_033109_006730.md`.
- Architecture, ADR 0053 and release-note contracts remain accurate; no documentation draft changes or approvals are pending for this round. No dependency changes or live-provider tests are required.

### Peer hardening outcome

All six stages are complete. Both Medium findings are resolved and independently re-reviewed; no remaining actionable findings were identified. Final validation: 138/138 deterministic scenarios, 46 focused Python checks, 14 Node editor checks, JavaScript syntax, complete Ruff/Black/MyPy gates and diff hygiene. No new documentation changes or manual checks are pending. Corrections and evidence are committed locally on `dev/settings-UI-upgrade`; nothing from this round has been pushed.
