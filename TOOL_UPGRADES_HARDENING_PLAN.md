# Tool Upgrades Branch Hardening

Branch: `dev/tool-upgrades`. Baseline: `718a7a5`; comparison base: `391ad820d8f767afc1744cbfcde0d2d6d663f820` (`main`). Status: all parallel reviews and correction reviews complete; confirmed fixes are committed and focused checks pass. Final deterministic validation passed: 552 core scenarios and both browser approval journeys, all with completed cleanup. One ADR wording clarification awaits approval.

## Scope and review assignments

- Session discovery/retrieval and compaction continuity: chronological ordering, references, field projections, pagination, persisted checkpoints, limits, errors, and prompt guidance.
- Vault-root protection and multi-delete: canonical targets, recursive selection, approval, journals, compensation, task/activity rollback, directory topology, and equivalent API/tool/script paths.
- Binary/text files and Gmail: byte-preserving copy, literal filenames, text/binary classification, overwrite and recovery, attachment metadata/limits, shared review UI, and Markdown-first guidance.
- Root integration: cross-workstream contracts, scenario quality and actual evidence, logging, documentation/release consistency, finding triage, and final branch readiness.

All reviewers follow the six stages in `docs/agent-guides/refactor-and-hardening.md` and the behavioral scenario review skill. Reviews are read-only to avoid overlapping changes. Confirmed defects require severity, source location, practical consequence, evidence, and a focused correction. Separate coverage limits and retained design choices from observed bugs.

## Contract invariants

- Session-map retrieval is chronological, verifiable, selective, and consistent with canonical message references. Prompt continuity must not over-weight old maps over recent messages.
- Deletion never targets the vault root or identity, preflights the complete effective selection, obeys standard approval policy, and preserves actionable recovery evidence.
- File transport is independent of extension; text operations support plain UTF-8 and reject unsupported binary contents before mutation. Exact literal filenames include genuine extensionless files; no `.md` inference or Exchange alias exists.
- Copy supports files only and preserves source bytes. Overwrite, review, mutation recording, and recovery remain authoritative at their shared boundaries.
- Gmail authorization, opt-in download policy, bounded transfer, safe destinations, and accurate provider metadata remain intact for arbitrary attachment types. Reading/importing remain capability-specific.
- Markdown remains preferred for saved knowledge and routine note context, while tasks can deliberately use other formats.

## Existing evidence and validation cadence

The effective feature baseline passed 538 deterministic core scenarios with complete cleanup in `validation/runs/pytest_20261107_205702_20a7b2fcd4/report.md`, the browser copy approval journey in `validation/runs/pytest_20261107_205648_8b62c58c1e/report.md`, and four follow-up logging/delegate checks in `validation/runs/pytest_20261107_210044_5481e363fe/report.md`. Ruff, Black, MyPy, JavaScript syntax, scenario inventory, and 34 admission/collection policy checks also passed. Do not rerun the full profile solely because the phase changed if effective behavior is unchanged.

For confirmed functional defects, follow the behavioral planning/scenario skills: write a meaningful failing real-application scenario, preserve its oracle, implement the smallest correction, and rerun relevant checks to green. Use isolated runtime roots and controlled external services; never use persistent repository `data/` or `system/` as scratch. Update source admission hashes and identities when scenarios/support change. Run the full production Python quality gate before each production checkpoint. Rerun the full core profile after stable behavioral corrections; live-service and experimental checks remain opt-in.

## Findings and integration record

### Confirmed corrections

| Severity | Finding and consequence | Correction |
| --- | --- | --- |
| High | Deletion preflight resolved raw backslash names while mutation serialization interpreted separators differently; selected and executed targets could differ, including an identity-file decoy. | Normalize request separators before resolution/protection and reject unrepresentable physical backslash names before recursive mutation. `21a61d6`. |
| High | Copy approval checked source/destination only at submission; queued changes or alias retargets could overwrite unreviewed data. | Persist effective approved canonical identities/existence/hashes atomically with the review claim, re-resolve paths after lock acquisition, recheck expected states under the copy lock, and mutate the checked canonical destination. `75c751e`. |
| Medium | Choosing another copy destination still checked the unused original destination, incorrectly rejecting approval. | Retain original source expectation; capture the explicitly chosen physical destination at submission, while retaining the original expectation for equivalent path spellings. `75c751e`. |
| Medium | Recursive review snapshot/hash scans could block the async event loop, including while waiting for hierarchy locks inside a SQLite transaction. | Offload scans; capture under the session-history lock before opening the persistence transaction. `21a61d6`. |
| Medium | Session-map ranking materialized payload columns for all historical rows before applying requested bounds. | Rank identities, select rows, then hydrate selected payloads; use a payload-free snapshot-anchor lookup. `d184e30`. |
| Medium | Corrupt authorized maps lost checkpoint/target-session identities in System Activity. | Add sanitized resource identities after authorization; suppress malformed map text and chained validation details. `d184e30`. |
| Medium | Generic line editing interpreted formfeed and Unicode separators as additional physical lines. | Use validated text through `StringIO(newline="").readlines()` to retain physical line and CRLF semantics. `9b48720`. |
| Medium | PDF-only-era attachment decoding rejected valid empty base64, preventing zero-byte downloads. | Permit empty encoded strings while retaining type, decode, authorization, and size checks. `22a5bc1`. |
| Low | Invalid physical deletion paths returned 400 but logged Explorer failures rather than rejections. | Classify `invalid_path` as rejected and prove the System Activity outcome. `4d31a7e`. |
| Low | Completed review cards said “Applied” even for denied calls or error tool results. | Use the neutral “Reviewed” lifecycle label; keep operation outcomes in tool history. `150edb3`. No wording-only test added. |

### Review outcomes by workstream

- Session discovery/retrieval: corrected bounded payload loading and diagnostic identity. Ordering, projections, references, selectors, authorization, forks, and append-during-pagination behavior passed review and targeted checks.
- Compaction continuity: no new defect found. Guidance remains conditional on missing detail; author prompts and salience policy remain unchanged. Deterministic checks do not establish live-model retrieval judgment.
- Vault-root protection/multi-delete: corrected target normalization and event-loop blocking. Additional real-runtime failed/cancelled-task probes preserved earlier/later ordinary writes and empty directory topology after successful or compensated deletion; no further rollback defect found.
- Binary/text/Gmail: corrected approval fences/overrides, literal line handling, and empty downloads. Copy still supports files only; text remains UTF-8; no new permission registry, ingestion strategy, or Exchange-specific behavior was added.
- Shared UI and logs: corrected rejection classification and review wording, checked actual System Activity, and reran real browser copy/destination editing and effective multi-delete approval.

### Evidence

- Initial root red: `validation/runs/pytest_20261107_223127_281db3b979/report.md`, 9 meaningful failures and one initially premature responsiveness check that passed; that check was strengthened with real validation-start observation before claiming it as red.
- Deterministic lock/identity red: `validation/runs/pytest_20261107_223341_185e6e9f35/report.md`, 2 failures. The independent thread holds a real lock until an event-loop callback can release it; the oracle is logical progress and preserved state, not elapsed-time performance.
- Session diagnostic red/green: `validation/runs/pytest_20261107_223223_cfbc873ee2/report.md` (1 failure), then `validation/runs/pytest_20261107_223415_d3d82de0d1/report.md` (14 passed/complete cleanup). Synthetic exact-query benchmark at `/tmp/assistantmd-session-map-query-benchmark.json`: median 24.493 ms before versus 0.780 ms after on 600 rows with three 32-KiB payloads. This is query evidence, not an application latency claim; no timing assertion was added.
- Deletion/threading green: `validation/runs/pytest_20261107_223803_2a32ad54e1/report.md`, 39 passed. Text/Gmail green: `validation/runs/pytest_20261107_223859_8ffe6b3ecb/report.md`, 7 passed.
- Copy red: `validation/runs/pytest_20261107_224129_ff39d5b621/report.md`, 5 failures covering queued source changes, modified/new destinations, same-byte alias retarget, and destination overrides. Green integration: `validation/runs/pytest_20261107_224318_ba39d43150/report.md`, 30 passed.
- Explorer rejection-log red: `validation/runs/pytest_20261107_224722_1f83a2047d/report.md`, 1 failure. Final boundary green: `validation/runs/pytest_20261107_224900_95c93bdbea/report.md`, 9 passed.
- Narrow copy lock-identity green: `validation/runs/pytest_20261107_225211_be86b3d399/report.md`, 1 passed with complete fixture cleanup. Earlier read-only standalone probes demonstrated the alias overwrite before correction and rejection afterward; standalone setup/cleanup mistakes were not claimed as product evidence. The fixture case uses frame observation only for synchronization; real lock behavior and byte preservation supply the oracle.
- Browser green: `validation/runs/pytest_20261107_225211_29ac829946/report.md`, 2 passed with complete cleanup: copy destination editing/approval and normalized recursive deletion selection/approval.
- Independent findings were reproduced through actual bound tools, review APIs, real task/session gates, actual filesystem locks, provider HTTP boundaries, and isolated storage. Original root scenarios were separated into focused modules with inputs/actions/oracles retained to support coherent correction commits.
- Final Ruff, Black (791 files), MyPy (299 production files), JavaScript syntax, scenario inventory, and 34 admission/collection policy checks pass. The final full deterministic core run passed: `validation/runs/pytest_20261107_225211_2c89e482c6/report.md`, 552 passed in 654 seconds, all with complete cleanup.

### Six-stage exit record

1. Scope/contracts: mapped all five workstreams and equivalent tool/API/script boundaries. No change to approval policy, source document conversion, Markdown context classification, or shell mount ownership.
2. Structure/ownership: reused mutation/recovery primitives; review artifacts remain the durable approved-state source. Narrow identity-first SQL avoids unnecessary payload work. No checker suppression, parallel permission policy, or new database migration was introduced.
3. Lifecycle/failures: exercised actual queue waits, alias changes, failed/cancelled task recovery, journal compensation, unsupported paths, corrupt maps, empty downloads, and denial semantics. Worker scans are read-only after cancellation; cancellation-under-lock-contention remains a coverage limit, not a demonstrated defect.
4. Observability: verified checkpoint/session identity without private map text, Explorer rejection classification, actual copy activity completion, and validation-only review-scan start events. Mutation details remain in Vault Activity; no high-frequency System Activity stream was added.
5. Frontend/operator: actual browser journeys pass, destination controls remain usable, and the neutral reviewed label no longer implies every operation succeeded. No CSS or layout change required rebuilding styles.
6. Docs/release/validation: existing product docs and approved v0.9.5 notes remain accurate. A precise ADR 0023 copy-fence clarification was drafted and approval requested; it has not been applied. No unrelated dependency update is justified by the findings, so dependency versions remain unchanged. Final broader validation passed; no confirmed code finding remains unresolved.

## Documentation and delivery

Root implementation plans can be updated during integration. Before changing protected documentation or release notes, present exact proposed text/diffs and obtain explicit user approval under AGENTS.md. Do not treat earlier approval of the previous drafts as approval for newly required changes. Commit validated milestones locally; do not push or merge without authorization.

Code hardening is complete: all confirmed findings are corrected and locally committed, all required checks pass, and no confirmed code finding remains unresolved. The precise ADR 0023 wording clarification remains pending explicit draft approval and is the only unapplied documentation proposal. No push or merge has occurred.

## Corrected source boundaries

- Deletion target resolution and recursive validation: `core/vault_state/path_deletion.py:93` (`_prepare_selection`) and `:281` (`_validate_target`).
- Async review capture/persistence boundary: `core/chat/task_execution.py:1166`, final turn persistence; submission scans and effective copy approval: `api/services/deferred_reviews.py:86`, `submit_chat_deferred_review`.
- Copy canonical identities/expected states: `core/vault_state/file_operations.py:793`, `copy_vault_file_operation`; persisted snapshot validation: `core/chat/deferred_reviews.py`, `_copy_snapshots`, `prepare_approved_review_context`, and `reviewed_copy_states`.
- Physical text lines: `core/vault_state/file_operations.py:691`, `_edit_vault_line_locked`; zero-byte response decoding: `core/integrations/google/gmail.py:195`, `GmailAPIClient.download_attachment`.
- Session-map query hydration: `core/chat/chat_store.py:1916`, `list_session_map_revisions`; corruption privacy/identity: `core/memory/session_map/retrieval.py:38`, `_load_map`, and `core/tools/session_ops.py`, `_session_failure_diagnostics`.
- Rejection classification: `api/services/vault_files.py:851`, Explorer deletion handling; neutral completion wording: `static/js/deferred-reviews.js:510`, `reviewStatusLabel`.
