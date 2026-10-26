# Versioned Settings Peer Hardening

## Scope and contract

Review settings database schema/checkpoints, sparse inherited defaults versus explicit Custom values, managed reset and per-setting Undo, backup/failure/concurrency guarantees, API/editor validation, configuration status, and UI request/refresh lifecycle. Preserve user runtime state and custom models. The final baseline uses session_map; existing checkpoint strategies remain pinned. Review current uncommitted defaults work and commits beginning with a77986a, consulting earlier database foundations where necessary.

## Review slices

1. Persistence peer: migrations, checkpoint history, backup atomicity, concurrent writes, cache invalidation, corruption and failure diagnostics; read-only review with isolated probes.
2. API/UI peer: shared validation, source state, reset confirmation, draft preservation, loading/refresh races, actionable status, keyboard and theme contracts; read-only review with focused probes.
3. Validation peer: investigate five failures from full run 20261023_221659_205811, align intentional scenario fixtures with approved defaults, investigate cancellation-hook failure, and run only the affected individual scenarios.
4. Root: review runtime consumer and operation event boundaries, adjudicate concrete findings, implement smallest defensible corrections, run affected checks and complete production Python quality gates, then create coherent local commits on the existing branch.

## Validation boundary

The completed full deterministic run passed 134/139. Four failures appear tied to previous default assumptions; the cancellation-hook failure requires investigation. The user explicitly requested no additional full validation until instructed. Use individual deterministic scenarios, focused Python/Node/browser tests, and static gates for corrections. Record that no full run has certified later hardening changes; do not relabel the earlier run as a clean pass.

## Observability and documentation review

Inspect settings_upgrade_completed, migration/load failures, managed_configuration_defaults_started/completed/failed, and setting-save/reset diagnostics for stable identities, truthful durable completion, concise safe errors, and absence of sensitive configuration values. Existing docs edits for setup, ADR 0049 and release notes are approved; present exact drafts and obtain approval for any additional protected documentation change. End with findings, fixes, focused test evidence and any remaining validation boundary.

## Findings resolved

- Medium, partial mutation/rollback contract: reset loaded its CAS snapshot after the backup, so a concurrent edit could be deleted without appearing in that backup. Backup now returns the revision from the exact SQLite read snapshot copied, reset requires that revision and rejects newer state, and ordinary save CAS guards later writes. Reset reserves a unique backup file to prevent concurrent operations from sharing a destination. The API exposes a stale-reset retry message. Unit tests cover intervening edits, successful reset, API rejection without reload, and distinct retained backup revisions.
- Medium, stale editor state: successful global reset retained model/provider drafts despite promising to discard unsaved edits. It now clears those drafts only after durable success and before card refresh. Cancellation and failure preserve them.
- Medium, async lifecycle: a pre-reset model/provider GET suppressed the post-reset refresh, then left old cards visible. Loaders coalesce pending reads and support a trailing fresh read after reset; delayed-read tests cover both registries and concurrent fresh callers.

## Exit evidence

All three peers completed; no further actionable persistence/API/UI findings remain. The five prior failures pass targeted run 20261023_222733_607544, and reset/database/repair checks pass 20261023_222851_978452 (3/3). Focused checks pass 34 Python tests, 25 Node tests and two browser availability tests. Full-repository Ruff and Black plus MyPy for api/core pass with zero findings. Inspected actual isolated settings lifecycle activity.log records: per-setting restore identifies setting_key; global reset start/completed/failed pairs share operation_id; failure reports phase, error_type and durable_update_completed without setting values. Existing migration/load event review remains valid; no new operation family or extra telemetry was introduced.

No new documentation changes are required for these fixes; approved setup, default strategy and release-note edits are applied. No additional full validation was started after the user requested a hold. The latest full run remains 134/139 before the targeted corrections; a fresh full pass is deferred until the user authorizes it.
