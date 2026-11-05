# UI favorites branch hardening

Review `dev/UI-favorites` against `main` (merge base `ebe33d96848360e08779afed0424349bc8daa786`). Preserve the approved feature contracts. Use delegated read-only analysis followed by root integration of concrete findings and small, separately validated corrections. No protected documentation edits without a draft and explicit approval; no full suite under the user's existing instruction.

## Stage 1: scope and contract map

- Favorites: chat bookmarks and vault file/folder bookmarks persist per principal and vault; API, tree filtering, chat cards, moves/deletes and restores must agree without leaking or reviving stale identities.
- Workspace saved prompts: a configurable workspace-local Markdown library supplies reusable composer text, with bounded validated input and current-context asynchronous results; prompts do not become injected instructions merely by existing.
- Chat UX: excerpt copying preserves selected source paragraphs; whole-message copying, long press and keyboard controls coexist. Session-map markers retain chronological placement during live turns. The mobile composer handle retains its original look and usable grab area.
- Explorer: one layout controller owns split/overlay mode, width and saved view; tab/session changes preserve open files and edits, vault changes refresh the binding with discard confirmation, and reload restores eligible saved navigation metadata. Focus, locks, responsive fallback and nested modals must remain coherent.
- Destructive operations: confirmed cross-location bulk and recursive deletion uses one activity and recorded file snapshots/directory states; exact rollback, rollback-of-rollback, conflict rejection, compensation, vault boundaries and retention must remain authoritative across API and UI. Agent-tool empty-directory semantics remain unchanged.

## Stage 2: structure and ownership

Delegate mutation/recovery, Explorer UI/state, and prompts/chat UX reviews; root reviews favorites persistence and shared mutation contracts, logging evidence, scenario policy, and final integration. Require severity, smell, contract, file/line evidence, concrete consequence and smallest defensible fix for each finding. Do not refactor based on size/style alone. Assign non-overlapping edits after reviewing reports.

## Stage 3: state, lifecycle and failure paths

Review success/denial/retry/reload/cancel/concurrency and partial failures. Reproduce meaningful defects through real isolated app/browser entry points before correction; retain stable behavioral expectations and source admission. Do not add assertions for wording, style or layout alone. Inspect cleanup artifacts and unexpected setup/worker failures separately.

## Stage 4: activity logging and observability

Read the logging guide and inspect actual retained event/System Activity records for changed mutation and persistence operations. Check lifecycle and searchable correlation identities, concise failure fields, warning issue identities and content redaction. Add focused semantic assertions when an operation's logging contract is missing.

## Stage 5: frontend and operator experience

Review keyboard/pointer/mobile/focus, nested dialogs, async current-context ownership, error/retry and unsaved-edit behavior through directly relevant browser checks. Keep status and confirmation consistent with durable backend results.

## Stage 6: validation and release readiness

Run directly affected deterministic scenarios after fixes, source admission/inventory, JavaScript syntax and the complete Python Ruff/Black/MyPy quality gate when production Python changes. Record the full deterministic pre-merge profile as not run by user instruction; it remains a merge-preparation requirement. Review current docs/ADR/release notes and propose exact necessary amendments for approval. Do not refresh unrelated dependencies or edit protected docs without approval.

## Findings and delivery evidence

Pending delegated reports and root review. Record confirmed findings, retained decisions, corrections, evidence paths, cleanup results, checks and remaining limits here before final handoff.

## Confirmed findings and integrated correction slices

### Backend recovery and diagnostics

- High — partial mutation/recovery boundary: bookmark cleanup ran after the exact-file transaction returned, so a failed cleanup could leave files deleted after an API error. Bookmark removal now joins the mutation-row SQLAlchemy transaction; failed cleanup rolls back that ledger commit and invokes filesystem compensation. Evidence: `test_favorite_mutation_hardening.py`, red `pytest_20261105_170851_6be121fee9`, green `pytest_20261105_171037_b1edd98528`.
- High — unrecorded directory deletion: a failed directory journal insert after `rmdir` lost an empty folder without a recovery record. The just-removed directory is now recreated before the error propagates; failed physical compensation reports an uncertain-state error. Evidence: `test_mutation_hardening.py`, red `pytest_20261105_170917_76f0089d22`, green `pytest_20261105_171032_29b329c760`.
- High — invalid lifecycle state: an activity completion-update failure left a committed deletion marked running, blocking rollback. Successful finalization is now inside the activity guard, failed completion can settle failed, and deletion failure logging covers context exit. Evidence: root completion case, red `pytest_20261105_171644_978da5639c`, green `pytest_20261105_172041_16d88a8efe`.
- Medium — misleading recovery availability: directory rollback promised success with missing/file-blocked unrecorded parents. Preview now reports an actionable parent-state conflict and execution rejects before mutation. File-only parent-restoration behavior remains unchanged.
- Medium — late validation: deleting vault identity metadata or its ancestor previously reached mutation/refresh before compensation rejected it. All selected targets now preflight the protected identity before any write.
- Medium — operator observability: recursive per-path mutation logs polluted System Activity while deletion lacked a correlated lifecycle. Per-path detail remains in the durable ledger/validation sink; System Activity now has start/completed/rejected/failed deletion summaries with vault/activity identities, stable statuses, independent issue IDs and controlled errors. Bookmark mutation terminal records also carry status. Actual System Activity assertions verify correlation and exclude file contents.

The exact-file transaction's favorite cleanup has one owner with an optional owning Session, rather than an adapter-level follow-up that can fail after the caller loses mutation results. Real SQLite constraints establish these faults; application services and recovery stay real.

### Chat features

- Medium — exported source corruption: excerpt selection trimmed PRE/code indentation and final newline. PRE contents are now exported without trimming; ordinary paragraph normalization remains unchanged.
- Medium — hidden failure: favorite PATCH errors called a status callback that ignored its arguments. Chat Settings now exposes an accessible, retryable error.
- Medium — asynchronous ownership: pending favorite state belonged to a replaceable button. One vault/session pending owner now locks replacement controls through re-render and vault round trips.
- Medium — diagnostic collapse/noise: saved-prompt warnings deduplicated independent workspaces/reasons and successful reads entered System Activity. Failures now have stable scope/reason identity and controlled fields; high-frequency successful reads are validation-only. Actual records verify dedup and private-content exclusion.

Chat red evidence: `pytest_20261105_170854_f473abeb3a`, `pytest_20261105_170854_d1a1f548c2`, `pytest_20261105_171350_734384a401`. Final green: browser `pytest_20261105_171446_d6e7dc8bcd` (three cases), prompt diagnostics `pytest_20261105_171046_79baa08bec`; existing excerpt keyboard/reverse/cancel case also passed in `pytest_20261105_171046_ea117a3892`.

### Retained decisions and scope limits

The session summary projection already uses per-session strategy/workspace queries; eliminating the additional bookmark metadata lookup would require a broader projection redesign without a demonstrated branch regression, so it is retained. Legacy dedicated code-block copying also trims whitespace in a pre-existing utility; this pass corrects the newly introduced excerpt export without broadening unrelated copy semantics. A suspected second Explorer name-search root was disproved: the scope selector exists only in generic pickers, so no speculative root rewrite was applied.

No runtime dependencies changed on this feature branch (lockfile changes are project version metadata), and this pass does not introduce an unrelated dependency refresh. The current ADR's rollback/recording contract is enforced by these corrections; no protected documentation edit is needed for this hardening slice. The full deterministic pre-merge profile remains required during merge preparation and is deliberately not run under the user's existing no-full-suite instruction.
