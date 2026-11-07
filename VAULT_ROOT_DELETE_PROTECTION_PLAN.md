# Vault Root Deletion Protection Plan

Branch: `dev/tool-upgrades`. Status: planned; implementation has not started. Branch issues: [TOOL_UPGRADES_PLAN.md](TOOL_UPGRADES_PLAN.md). Source: `/home/codeman/docs/AssistantMD/! To Do.md`, reviewed 2026-11-07; conversation decisions take precedence.

## Scope

**Vault-root deletion protection:** Reject deletion of the vault root through ordinary file tools, including normalized aliases such as `.` and `./`. Rejection must preserve all root contents. Include this guard before extending deletion capabilities.

## Current implementation and affected surfaces

- `core/vault_state/path_deletion.py` already implements explorer bulk deletion with root protection. The file-tool deletion path is separate and requires `confirm_path` before file or empty-directory removal.
- Root protection differs between paths: explorer deletion compares resolved targets against the vault root, while empty-directory cleanup rejects only an empty normalized string. `normalize_vault_relative_path` leaves `.` intact, so the ordinary file-tool root-alias concern remains in scope pending a focused behavioral reproduction.
- Inspect `core/vault_state/file_operations.py`, `core/vault_state/file_mutations.py`, and `core/vault_state/pathing.py`.

## Contract and constraints

- Reject deletion of the resolved vault root, including `.` and `./`, before any removal.
- Reject a root alias mixed into a deletion selection before removing any target.
- Preserve every existing file and directory, including empty-directory topology. Deliberate empty-vault operations are outside scope.

## Acceptance and validation

**Root protection:** Through ordinary file-tool deletion, test normalized root aliases with populated and empty vaults and with a root alias mixed into a batch. Assert rejection and preservation of every existing file and directory. If already protected, record a genuine green baseline rather than claiming a defect.

Existing deletion baseline evidence: On 2026-11-07, the existing explorer bulk-deletion, split file-tool, and inline-edit policy scenarios passed: 8 tests, report `validation/runs/pytest_20261107_001234_e6ec4fab60/report.md`. The dedicated empty-directory cleanup scenario also passed: 1 test, report `validation/runs/pytest_20261107_001246_dfc55e5af4/report.md`. These checks establish existing behavior; no production or scenario code was changed during this scope audit. Browser interaction was inspected in source, not exercised in a browser.

## Execution and evidence

Use real application startup with isolated scenario vaults and system roots. Never use repository `data/` or `system/` as scratch storage. Keep internal services real; use narrow model-provider doubles for deterministic journeys when needed. Record meaningful red/green results, report paths, retained artifacts, and limitations here during implementation. Setup failures do not count as red evidence; record genuine green baselines for working behavior.

Run narrow deterministic scenarios during development. Production Python changes require the complete Production Python Quality Gate. Run `uv run python -m pytest validation/scenarios/integration/core` once the branch is stable during hardening or merge preparation. Experimental and live-service scenarios remain opt-in.

## Open decisions and dependencies

- Reproduce through the ordinary tool in isolated storage before declaring a confirmed defect; current string normalization appears weaker than explorer resolved-root protection.
- No settings or schema migration is currently planned. This is a persistent-data boundary: reject before mutation.
- Complete single-target protection before [MULTI_DELETE_PLAN.md](MULTI_DELETE_PLAN.md); add the mixed-selection regression with that feature. Read ADR 0025 before changing shared mutation behavior.

## Implementation steps

1. Add focused production-tool scenarios with populated vaults, empty directories, and normalized root aliases; record meaningful red evidence or genuine green baseline.
2. If a gap is demonstrated, enforce resolved-root rejection at the appropriate shared mutation boundary.
3. Rerun the same scenarios and existing cleanup/explorer preflight checks to green, verifying complete content and topology preservation.
4. Coordinate the mixed-selection root-alias regression with native multi-delete before enabling it.
5. Run relevant hardening and quality checks and record evidence and limitations.
6. Review relevant documentation (file-tool deletion, path-boundary documentation, and mutation decisions as applicable), identify affected files, present exact proposed text or diffs in chat, obtain explicit user approval, and then apply approved updates. Documentation edits remain pending until approval.

Next phase: Feature Development, beginning with the remaining contract decisions and acceptance scenarios.
