# Vault Root Deletion Protection Plan

Branch: `dev/tool-upgrades`. Status: complete; implementation, focused validation, and approved documentation update finished. Branch issues: [TOOL_UPGRADES_PLAN.md](TOOL_UPGRADES_PLAN.md). Source: `/home/codeman/docs/AssistantMD/! To Do.md`, reviewed 2026-11-07; conversation decisions take precedence.

## Scope

**Vault-root deletion protection:** Reject deletion of the vault root through ordinary file tools, including normalized aliases such as `.` and `./`. Rejection must preserve all root contents. Include this guard before extending deletion capabilities.

## Implementation baseline and affected surfaces

- `core/vault_state/path_deletion.py` already implements explorer bulk deletion with root protection. The file-tool deletion path is separate and requires `confirm_path` before file or empty-directory removal.
- Root protection differs between paths: explorer deletion compares resolved targets against the vault root, while empty-directory cleanup rejects only an empty normalized string. `normalize_vault_relative_path` leaves `.` intact, so the ordinary file-tool root-alias concern remains in scope pending a focused behavioral reproduction.
- Inspect `core/vault_state/file_operations.py`, `core/vault_state/file_mutations.py`, and `core/vault_state/pathing.py`.

## Refactor boundary

The two consumers have justified different behavior. Explorer deletion removes populated directory trees and multiple selections with recorded rollback states; file-tool directory deletion removes only empty directories and preserves files. Do not merge those execution paths as part of root protection. Extract a shared deletion-target resolver/validator in the existing mutation layer, where `VaultMutationRejected` already lives and both consumers already depend. It must reuse vault-relative path resolution and reject a target equal to the resolved vault root with `invalid_target`. Both deletion consumers use that invariant before mutation while preserving their distinct deletion, confirmation, locking, auditing, and recovery contracts. Ordinary read/list path resolution must continue accepting the root.

Use existing production-tool binding and explorer HTTP entry points with isolated app storage. Add root-alias rejection scenarios that compare file bytes and complete directory topology before and after each request; a populated vault must retain empty sibling directories too. Parameterize `.` and `./` aliases and include an explorer batch with a valid file preceding the invalid root target to prove preflight prevents partial removal. Existing single-file, directory-cleanup, and explorer rollback scenarios protect the distinct successful behaviors.

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

- Confirmed defect: the ordinary tool accepted `.` and removed empty descendant directories. Explorer root rejection already worked and is preserved. The shared validator now resolves and rejects root aliases before cleanup.
- No settings or schema migration is currently planned. This is a persistent-data boundary: reject before mutation.
- Complete single-target protection before [MULTI_DELETE_PLAN.md](MULTI_DELETE_PLAN.md); add the mixed-selection regression with that feature. Read ADR 0025 before changing shared mutation behavior.

## Implementation and evidence

- Added `resolve_vault_deletion_target` to `core/vault_state/file_mutations.py`, reusing existing boundary resolution and `VaultMutationRejected`. Empty-directory cleanup uses it before locking and again under the hierarchy lock; explorer deletion uses it during full-selection preflight. Removed the divergent string-only and inline resolved-root guards. File-tool and explorer deletion execution remain separate because their contracts differ.
- Meaningful red and explorer green baseline: `validation/runs/pytest_20261107_035254_153dbc6395/report.md`, 1 failed and 1 passed. The real file tool removed empty directory topology while accepting the root target; the explorer rejected the mixed valid-file/root selection with contents intact. Before/after byte-and-directory snapshots are retained. The earlier `pytest_20261107_035220_cdb9b03105` run also exposed a test-only API envelope mistake (`error_type` versus the actual `error` field); that assertion was corrected from the real API response, not treated as a product defect.
- Fixture correction: `pytest_20261107_035336_e584aad7a2` showed that a startup background rescan could create vault identity metadata during the preservation assertion. The scenario now waits for the real startup-refresh completion event before creating its vault. All preservation and rejection expectations remain intact; no application services are mocked or disabled. One formatting/admission mismatch was rejected by policy and corrected before the final run; it is not behavioral evidence.
- Green: `validation/runs/pytest_20261107_035449_d74b4c3aaa/report.md`, 31 passing cases. Eighteen root-protection cases cover `.`, `./`, and `././` across tool/explorer consumers and populated, empty-directory-only, and truly empty vaults. Mixed explorer selections place a valid file before the root alias. Successful cleanup, exact rollback, ordinary file operations, root listing, inline review, and mutation-hardening scenarios also pass.
- Quality gate passes: `uv run ruff check .`, `uv run black --check .` (780 files), and `uv run mypy api core` (298 production source files). Scenario inventory and all 34 admission/collection policy checks passed. No checker configuration, casts, suppressions, settings, schema, or runtime-state migrations were introduced.
- Hardening review: the shared invariant belongs at the existing mutation layer, keeping read/list root access intact; validation happens before identity creation or directory removal; both consumers retain their existing locks and error envelopes. Explorer rejection continues through its existing correlated `vault_explorer_delete_rejected` activity event, while the tool returns `invalid_target` without recording a successful cleanup. No new logging family or frontend flow was introduced. Validation uses real local services and isolated storage, without live dependencies or browser interaction.
- Full deterministic profile rerun is deferred to merge preparation after the remaining branch work. The earlier 497-case result predates this change and is not claimed as validation of root protection.
- Documentation update explicitly approved and applied to `docs/tools/file_write.md`: "`delete` cannot target the vault root, including aliases such as `.` and `./`." Direct diff review and `git diff --check` cover this prose-only update. ADR 0025's shared mutation and recovery contract is preserved and needs no change.

## Implementation steps

1. Add focused production-tool scenarios with populated vaults, empty directories, and normalized root aliases; record meaningful red evidence or genuine green baseline.
2. If a gap is demonstrated, enforce resolved-root rejection at the appropriate shared mutation boundary.
3. Rerun the same scenarios and existing cleanup/explorer preflight checks to green, verifying complete content and topology preservation.
4. Coordinate the mixed-selection root-alias regression with native multi-delete before enabling it.
5. Run relevant hardening and quality checks and record evidence and limitations.
6. Review relevant documentation (file-tool deletion, path-boundary documentation, and mutation decisions as applicable), identify affected files, present exact proposed text or diffs in chat, obtain explicit user approval, and then apply approved updates. Documentation edits remain pending until approval.

Next phase: review preparation for this completed milestone. Native multi-delete remains a separate workstream and must add its own mixed-selection root-alias regression before enabling that tool input.
