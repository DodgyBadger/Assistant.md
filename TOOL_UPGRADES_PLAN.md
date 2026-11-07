# Tool Upgrades Branch Issues

Branch: `dev/tool-upgrades`. Source: `/home/codeman/docs/AssistantMD/! To Do.md`, reviewed 2026-11-07. Session tool, compaction continuity, and vault-root protection are implemented and validated; binary/exchange and multi-delete work remain planned. This file tracks branch issues and status; linked root-level plans own their detailed contracts, acceptance conditions, steps, and evidence.

## Selected issues

| Issue | Status | Implementation plan |
| --- | --- | --- |
| Session map discovery and selective retrieval with references and oldest-first ordering | Complete; 497 core cases and quality gate pass, approved docs applied | [Session Map Discovery](SESSION_MAP_DISCOVERY_PLAN.md) |
| Continuity after compaction of consequential assistant responses | Complete; context-only guidance, 13 focused cases and quality gate pass | [Compaction Continuity](COMPACTION_CONTINUITY_PLAN.md) |
| Byte-preserving PDF copying and supported exchange access | Planned; path and reading semantics to settle | [Binary Files Exchange](BINARY_FILES_EXCHANGE_PLAN.md) |
| Native multi-delete with standard normal/inline-edit approval rules | Planned; scripts retain current behavior | [Multi Delete](MULTI_DELETE_PLAN.md) |
| Vault-root deletion rejection, including normalized aliases | Complete; shared validation, 31 focused cases and quality gate pass, approved docs applied | [Vault Root Delete Protection](VAULT_ROOT_DELETE_PROTECTION_PLAN.md) |

## Existing capabilities verified

Explorer multi-selection, recursive deletion, confirmation, and rollback are implemented. File-tool empty-directory cleanup is implemented. Agents can delete multiple files through Monty loops over single-target calls. Native multi-delete will preserve scripting and existing approval rules; the external TODO's always-required approval proposal is superseded by the user's decision.

On 2026-11-07, the existing explorer bulk-deletion, split file-tool, and inline-edit policy scenarios passed: 8 tests, report `validation/runs/pytest_20261107_001234_e6ec4fab60/report.md`. The dedicated empty-directory cleanup scenario also passed: 1 test, report `validation/runs/pytest_20261107_001246_dfc55e5af4/report.md`. These checks establish existing behavior; no production or scenario code was changed during this scope audit. Browser interaction was inspected in source, not exercised in a browser.

## Sequence and branch validation

Start with session map discovery, then compaction continuity and binary/exchange work. Complete single-target root protection before extending deletion to multiple targets; add mixed-selection protection with multi-delete. Preserve completed capabilities as regression baselines.

Follow validation-first development and the appropriate behavioral testing skills. Use isolated runtime roots, meaningful red/green evidence, targeted deterministic checks, and the complete Production Python Quality Gate for production Python changes. Run `uv run python -m pytest validation/scenarios/integration/core` once the branch is stable during hardening or merge preparation. Experimental and live-service checks remain opt-in.

## Deferred issues

- Mobile attachment upload failures: requires reproduction of the mobile upload path and UI error handling.
- Explorer move/copy button: adjacent to file copying, but a separate UI interaction; tool copying is selected here.
- Startup `Imports`/`Imported` folders and setting retirement: runtime provisioning and possible persisted-data cleanup need their own investigation.
- Sending messages to other sessions: introduces delivery, execution, and authorization semantics beyond retrieval.
- JSON URL ingestion, limited multiuser support, editable prompt administration, and recurring dependency upgrades: separate subsystem or maintenance efforts.

## Documentation review

For each implemented issue, identify relevant documentation, present exact proposed text or diffs in chat, obtain explicit approval, and apply only the approved updates. Documentation changes remain pending. No edits under `docs/`, `README.md`, or `RELEASE_NOTES.md` are authorized by this split. The external library TODO is unchanged.
