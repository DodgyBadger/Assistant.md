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

Delegated reports and root integration are complete. Confirmed findings, retained decisions, corrections, evidence paths, cleanup results, checks and remaining limits are recorded below.

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

### Explorer frontend integration

- Medium — stale context: persistent docks retained the previous session's workspace binding and recovery notice. One context-sync hook updates the current picker binding/folder while preserving reader DOM and edits (`static/app.js:519`, `static/js/vault-path-picker.js:163`).
- Medium — invalid busy lifecycle: overlay Escape bypassed upload/mutation close guards. The shared layout controller now owns guarded Explorer Escape in both modes; generic pickers keep their separate modal lifecycle (`static/js/vault-explorer-layout.js:137`).
- Medium — functional accessibility: modal/mobile readers allowed Tab into underlying chat controls. Focus now stays in the top modal surface, while split view remains modeless (`static/js/vault-explorer-layout.js:137`).
- Medium — stale data: normal composer tool writes did not refresh an open clean reader. One lock-release refresh lifecycle serves normal, selection, resumed and review runs, with the existing dirty/mode/current-response safeguards; the selection-send-specific refresh was removed (`static/js/file-references.js:929`).
- Medium — lost asynchronous ownership: older workspace saves could overwrite a newer session or newer same-session choice. Direct saves and picker writes share one generation/context owner and persistence function; new input invalidates old ownership and stale failures cannot restore obsolete values (`static/js/workspace-picker.js:3`).

Meaningful Explorer reds: `pytest_20261105_171018_63be3e927a` (busy Escape, modal focus, real tool preview), `pytest_20261105_171346_babd334675` (workspace rebinding), `pytest_20261105_172100_e230b0dbac` (cross-session save), `pytest_20261105_172646_88d25f890d` (same-session save ordering). Locator/oracle corrections preserve the behavioral contract: use the stable opener whose accessible label includes context, use the actual final preview tab stop, and explicitly choose Workspace view when the user had independently navigated to vault root. No style, wording or fixed-layout assertions were introduced.

## Final stage exit evidence

1. Scope/contracts: deletion, rollback, bookmark persistence, prompt discovery, excerpt export, dock navigation and async ownership were traced through their real API/UI and shared mutation boundaries. Agent-tool empty-directory semantics and workflow/script mutation ownership were retained.
2. Structure/ownership: recovered mutation bookkeeping has one database transaction, Explorer modality has one owner, file refresh has one lock-release owner, and workspace saves/favorite requests have context ownership. No new blanket suppressions, untyped production helpers or dependency wrappers were added. Low-risk retained decisions are documented above.
3. Lifecycle/failure: real SQLite faults, actual delayed/disconnected responses, protected identity and parent conflicts, reload, navigation, cancellation and explicit confirmation exercise the relevant negative paths. Retained successful phase and cleanup evidence was inspected by the root after delegated reports.
4. Observability: inspected actual System Activity `vault_explorer_delete_started/completed/rejected/failed`, `vault_file_favorites_updated`, `chat_session_favorite_updated`, and `saved_prompt_discovery_failed`, plus existing rollback lifecycle records. Vault/activity/session/workspace identities remain searchable. Independent deletion failures have distinct issue IDs; repeated identical prompt failures deduplicate by scope/reason. Per-path mutation and successful prompt-read detail are intentionally validation-only; retained tests exclude document/prompt sentinels from System Activity.
5. Frontend/operator: real browser checks cover desktop/mobile favorites/prompts, nested modal Escape/Tab, split resize/reload, workspace/session switches, real tool-file writes, dirty readers, retryable errors, clipboard byte fidelity and out-of-order response handling. Existing selection stay/edit/refresh-failure/new-send cases passed against the shared refresh lifecycle.
6. Release readiness: complete Ruff, Black and MyPy production quality gates passed, as did changed JavaScript syntax, admission inventory and whitespace checks. No protected documentation changes are required for these contract-preserving fixes; the current ADR/release notes remain consistent. No dependencies were refreshed. The full `integration/core` pre-merge profile was not run under the user's existing instruction and remains a merge-preparation check; this pass does not claim full-profile merge readiness.

### Final retained green reports

- backend feature regression: `validation/runs/pytest_20261105_171443_49ff076a77/report.md`.
- backend finalization recovery: `validation/runs/pytest_20261105_172041_16d88a8efe/report.md`.
- chat feature hardening: `validation/runs/pytest_20261105_171446_d6e7dc8bcd/report.md`.
- excerpt existing keyboard regression: `validation/runs/pytest_20261105_171046_ea117a3892/report.md`.
- explorer hardening: `validation/runs/pytest_20261105_172750_1202c8a900/report.md`.
- final workspace owner placement: `validation/runs/pytest_20261105_172923_a35641e1b5/report.md`.
- existing selection refresh regression: `validation/runs/pytest_20261105_173047_a83423cec8/report.md`.
- existing desktop/mobile feature regression: `validation/runs/pytest_20261105_172307_e8f311f913/report.md`.

Distinct selected green cases: 15 backend and 28 browser. No worker crashes or unresolved cleanup verdicts in these successful runs. Root review confirmed lifecycle/listener/browser cleanup artifacts and no browser page errors.
