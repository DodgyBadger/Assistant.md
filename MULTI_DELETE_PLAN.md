# Native Multi-Delete Plan

Branch: `dev/tool-upgrades`. Status: planned; implementation has not started. Branch issues: [TOOL_UPGRADES_PLAN.md](TOOL_UPGRADES_PLAN.md). Source: `/home/codeman/docs/AssistantMD/! To Do.md`, reviewed 2026-11-07; conversation decisions take precedence.

## Scope

**Native multi-delete with standard approval:** Add multiple deletion targets to `file_write`. Direct tool calls follow the existing approval rules: one selection card in inline-edit mode, immediate execution in normal mode. Preserve scripted tool invocation, including Monty loops over single-file deletes, without adding approval pauses. Explorer bulk deletion and file-tool empty-directory cleanup already exist; preserve those behaviors and reuse their shared mutation, auditing, snapshot, and review services where their contracts fit. This user decision supersedes the external TODO's always-required approval proposal.

## Current implementation and affected surfaces

- `core/vault_state/path_deletion.py` already implements explorer bulk deletion with root protection. The file-tool deletion path is separate and requires `confirm_path` before file or empty-directory removal.
- Recheck on 2026-11-07: the explorer UI presents a selection confirmation form and calls `/paths/delete-batch`. `test_explorer_bulk_delete.py` covers recursive multi-target deletion, binary preservation on rollback, empty-directory topology restoration, full-target preflight, and rejection of `.` mixed into a selection. `test_directory_cleanup.py` covers removal of nested empty directories while retaining ordinary, hidden, and binary files. These are completed capabilities, not missing features.
- `core/chat/executor.py:approval_tools_for_chat_mode` requires approval for `file_write` in inline-edit mode and returns no approval tools in normal mode. Preserve this policy when extending the current single-target input to support multiple targets.
- Monty follow-up: `docs/tools/code_execution.md` already demonstrates looping over `file_write(operation="delete", path=path, confirm_path=path)`. `core/authoring/runtime/monty_runner.py` independently binds tools without chat approval names, and `core/authoring/helpers/runtime_common.py` invokes their functions directly. Preserve this scripted execution contract. This source review confirms the capability, not which method was used in any particular historical chat.
- `core/chat/deferred_reviews.py` and chat tool binding own interactive review. Extend selection snapshots and target-change validation for native multi-delete under the existing inline-edit policy. ADR 0023's per-operation review description may need to describe one reviewed deletion selection.

## Contract and constraints

- Preserve single-target calls and compatibility with their path confirmation.
- Direct multi-delete produces one selection card in inline-edit mode and executes immediately in normal mode.
- Preserve scripted invocation and Monty single-delete loops without new approval pauses. Approval-policy redesign and Monty suspend/resume are out of scope.
- Preserve full-selection validation, target-change checks, auditing, snapshots, and recovery. Explorer deletion and empty-folder cleanup are existing capabilities.

## Acceptance and validation

**Deletion:** Preserve green baselines from `test_explorer_bulk_delete.py` and `test_directory_cleanup.py`; do not rebuild completed explorer deletion or empty-folder cleanup. Extend `test_file_tools.py` and `test_file_ops_inline_edit_policy.py` for multiple deletion targets, overlapping targets, invalid targets, and actual resulting state. Through chat, prove a direct multi-delete remains pending with targets intact until approval in inline-edit mode; denial preserves targets and changed targets invalidate approval. Prove the same operation executes without a card in normal mode. Preserve Monty scripting and single-target compatibility. Define partial-failure semantics before writing those assertions. Expected red: unsupported multi-target request or incomplete selection review, not the absence of approval in normal mode or scripts.

Existing deletion baseline evidence: On 2026-11-07, the existing explorer bulk-deletion, split file-tool, and inline-edit policy scenarios passed: 8 tests, report `validation/runs/pytest_20261107_001234_e6ec4fab60/report.md`. The dedicated empty-directory cleanup scenario also passed: 1 test, report `validation/runs/pytest_20261107_001246_dfc55e5af4/report.md`. These checks establish existing behavior; no production or scenario code was changed during this scope audit. Browser interaction was inspected in source, not exercised in a browser.

## Execution and evidence

Use real application startup with isolated scenario vaults and system roots. Never use repository `data/` or `system/` as scratch storage. Keep internal services real; use narrow model-provider doubles for deterministic journeys when needed. Record meaningful red/green results, report paths, retained artifacts, and limitations here during implementation. Setup failures do not count as red evidence; record genuine green baselines for working behavior.

Run narrow deterministic scenarios during development. Production Python changes require the complete Production Python Quality Gate. Run `uv run python -m pytest validation/scenarios/integration/core` once the branch is stable during hardening or merge preparation. Experimental and live-service scenarios remain opt-in.

## Open decisions and dependencies

- Define input/confirmation shape, overlapping-target handling, preflight, partial-failure reporting, and recursive-folder semantics.
- Identify persisted review-context or payload changes before implementation.
- Dependency: complete single-target protection in [VAULT_ROOT_DELETE_PROTECTION_PLAN.md](VAULT_ROOT_DELETE_PROTECTION_PLAN.md) before enabling native multi-delete.
- Reviewed ADR 0023; read ADR 0025 for mutation and recovery contracts.

## Implementation steps

1. Complete single-target root protection, then settle multi-target inputs and semantics against the shared deletion service.
2. Add production-tool and chat review scenarios; confirm meaningful failure for missing multi-target support or incomplete selection review.
3. Implement multi-delete through shared deletion/mutation services and extend inline-edit selection snapshots and target validation.
4. Rerun to green, including denial, changed targets, immediate normal-mode execution, scripting, single-target compatibility, and mixed-selection root rejection.
5. Preserve existing deletion baselines and run relevant hardening and quality checks.
6. Review relevant documentation (`docs/tools/file_write.md`, review guidance, and ADR 0023 as applicable), identify affected files, present exact proposed text or diffs in chat, obtain explicit user approval, and then apply approved updates. Documentation edits remain pending until approval.

Next phase: Feature Development, beginning with the remaining contract decisions and acceptance scenarios.
