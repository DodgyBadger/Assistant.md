# 0053 - Store Installation Settings in SQLite

## Status

Accepted. Complements [ADR 0007](0007-settings-backed-model-and-tool-binding.md), [ADR 0015](0015-subsystem-owned-system-databases.md), and [ADR 0052](0052-managed-model-families-and-durable-aliases.md).

## Context

System settings combine editable values, provider configuration, model aliases, and application-managed catalog updates. These records need atomic persistence and protection against stale writes while retaining shared typed validation.

## Decision

The settings subsystem owns `settings.db` under the configured system root. General settings, models, providers, and tools occupy separate record tables. A persisted revision protects configuration commits from stale snapshots. Model ownership, alias validation, and catalog reconciliation remain shared domain rules.

Packaged defaults seed new installations. Existing runtime YAML is imported once after validation and backup; initialization and imported records commit together. After initialization, the database is authoritative.

Secret values remain in the encrypted credentials store. Deployment bootstrap remains environment-based. The System UI uses typed configuration operations; raw-YAML read and replacement endpoints are removed. Settings repair creates a consistent database backup before committing changes.

Existing stored values are retained as explicit overrides when default ownership is introduced. Saving a setting pins its value, including when it equals the current default; Use default removes the override. Effective values and product-owned metadata are resolved centrally.

Ordered settings migrations cover structural and semantic changes, record successful steps transactionally, and reject unsupported newer versions.

Global Use defaults restores the shipped managed configuration and clears general-setting overrides after backup; custom models, independently named providers and tools, alternate model aliases, and encrypted secrets are preserved. Conflicts that would overwrite a custom model block the reset.

## Consequences

Configuration changes commit atomically, stale writes require reload and retry, and failed imports remain retryable. Backups and recovery must include the settings database. Runtime configuration no longer depends on editing YAML files.

## Evidence

- `core/settings/store.py`
- `core/settings/repository.py`
- `core/settings/schema.py`
- `validation/scenarios/integration/core/settings_database_migration.py`
