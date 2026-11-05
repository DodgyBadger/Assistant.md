# Confirmed bulk and recursive explorer deletion

Allow selecting files and folders from different locations and deleting them together after an explicit confirmation listing the selected paths and warning that folder contents are included. Cancel performs no mutations. Fold selections covered by a selected ancestor into one top-level source. Keep existing run/review/upload locks and duplicate-submit protection.

Use one explorer deletion endpoint/activity for both single and multiple selections. Preflight every source and descendant under the vault hierarchy mutation lock before deleting anything; reject root/outside/missing/symlink or unsupported targets. Delete file states through the shared recorded exact-file mutation primitive so snapshots, favorites, manifest updates and rollback provenance remain intact. Record every removed directory with enough before/after state to restore empty directories too. Preserve agent-tool empty-directory deletion semantics; broaden only explorer operations.

Extend activity rollback narrowly for fully recorded explorer directory deletions and their rollback activities; unrelated directory operations remain unsupported. Preview must detect conflicts and unexpected contents before undoing a rollback. Restore file bytes through the existing exact-file transaction and directory topology through recorded directory transitions under the same hierarchy lock. A deletion, its rollback and rollback-of-rollback should remain visible as attributed activities. Failures must be reported accurately; do not claim deletion succeeded when any target remains.

Acceptance uses real isolated apps and browsers without external services: backend bulk/recursive deletion of text, binary and nested empty directories, overlap folding, rejected confirmation and invalid mixed targets without changes, one activity with usable rollback, byte-exact restoration and undo, and conflict rejection preserving newer content. Browser coverage selects files from different folders, confirms cancellation leaves files intact, and confirms the selection deletes only after submission. Establish red for missing bulk behavior before implementation, retain evidence, register scenario admission, run directly relevant cases and complete production Python Ruff/Black/MyPy checks. Do not run the full suite.

Documentation review is the final step: identify any affected product documentation or ADR, present exact proposed text in chat and obtain approval before editing protected documentation. No documentation edits are authorized yet.

## Delivery and evidence

Single and bulk Explorer deletion now share one deletion service and activity. File bytes are snapshotted and removed using the existing exact-file mutation transaction; every removed directory has a reversible state record. Activity rollback supports these directory states, rejects later contents and symlink/type conflicts, compensates file/directory restoration failures, and leaves unrelated directory operations unsupported. Favorites and manifest refresh use their existing mutation boundaries. The confirmation form freezes the complete selection; cancel changes nothing, and requests remain locked against duplicate submission and active-response edits.

The initial pre-implementation run in `validation/runs/pytest_20261104_182151_cfc9fbfde0/report.md` exposed the missing endpoint in the preflight case; its binary case failed at fixture setup because the text helper does not accept bytes. That setup was corrected with a native byte write and was not treated as behavioral red. An early API-facade import omission caused a setup/cleanup-unconfirmed report; subsequent fresh protected runs verified startup and cleanup. The existing non-empty-folder rejection expectation was explicitly updated to the user's newly approved recursive-delete contract while retaining independent single-file deletion/revision coverage. Browser activity recovery uses the existing Refresh Activity control before choosing the new activity.

Final evidence (seven distinct targeted scenarios across these runs):
- bulk_and_recursive_backend: `validation/runs/pytest_20261104_183408_b3a05c3797/report.md`.
- activity_rollback_regressions: `validation/runs/pytest_20261104_182528_480624af06/report.md`.
- existing_mutation_api: `validation/runs/pytest_20261104_182933_66915458bb/report.md`.
- browser_delete_and_activity_rollback: `validation/runs/pytest_20261104_182933_7e793f714a/report.md`.
- browser_single_file_regression: `validation/runs/pytest_20261104_182446_eb8eb40000/report.md`.

All selected phase verdicts passed without worker crashes. Retained lifecycle and listener artifacts confirm app/runtime cleanup; browser contexts closed without page errors. Complete production Ruff, Black and MyPy gates passed, along with JavaScript syntax, admission inventory and whitespace checks. No full suite or live-service scenarios were run.

Documentation completed: the user approved the two proposed amendments to ADR 0025, and the approved text was applied to its directory-recording bullet and rollback-limits paragraph.
