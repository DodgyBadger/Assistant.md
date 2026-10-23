# Vault Reading Review Workflow

Status: implemented and validated on `dev/settings-UI-upgrade`.

## Contract

Send now from a quoted selection keeps the file reading modal and explorer navigation open while the existing chat task runs. Add to draft keeps its existing behavior. Existing busy/review locks continue to prevent a second submission; there is no message queue. Chat composer drafts and attachments remain independent from immediate quote sends.

After an accepted immediate task finishes, refresh the currently open file in the same vault, including when the reader navigated to another file. Preserve reading position, do not reopen a dismissed modal, reject stale refresh responses, and never overwrite newer local file edits or an unsent quoted instruction. Refresh failures leave the old preview visible with actionable feedback. A failed task start leaves the quoted instruction available for retry. Confirmed pre-existing unsaved file edits are discarded only after task acceptance.

## Testable steps

1. Update real-browser quote-send regressions to require the modal to remain open, a blocked second send during processing, and refreshed agent edits after completion; retain append/draft/attachment and failure-path coverage.
2. Replace forced explorer dismissal with an accepted-send reading handoff and safe completion refresh through the current file-view owner. Keep chat task ownership and locking unchanged.
3. Verify navigation, dismissal, local-edit preservation, refresh failure and keyboard flows with browser tests and focused JavaScript checks. No backend or CSS behavior changes are planned.
4. Review documentation relevance; present any protected documentation drafts and obtain approval before applying them. Existing settings documentation drafts remain independently pending approval.

## Outcome and validation

Immediate quote submission keeps the reader and navigation open. Accepted sends return focus to the reader without consuming the normal chat draft or attachments. Completion refreshes the currently open preview in the same vault with no browser cache, retains scroll, and guards modal identity, request generation, local edits and saved hash before applying new content. Closed readers remain closed. Failed refresh leaves the prior preview visible with a theme-aware error.

Two lifecycle regressions were reproduced and corrected: background completion must not steal focus from continued reading, and cleanup from an older refresh must not unlock a newer pending send. A per-send request identity protects the shared sending state. Existing active-task/review locks still prevent queuing a second request. Add to draft is unchanged.

- Real Chromium browser checks: 22 passed, including mobile/keyboard behavior, retry, confirmed dirty-file handling, refresh/navigation/dismissal, scroll, local-edit preservation, focus and consecutive-send races.
- Vault Explorer controller checks: 2 passed.
- Node quote/composer checks: 3 passed.
- JavaScript syntax, complete Ruff/Black and diff hygiene checks passed. No production Python, API/storage contract, CSS or dependency changes were needed. The previously passed 139-scenario deterministic backend profile remains the backend baseline; this frontend change was verified directly in the browser rather than repeating that profile.
- Existing user-facing documentation and the quote-selection release-note entry remain accurate; no protected documentation edits are required for this change. Settings lifecycle documentation drafts are still separately awaiting approval.

Implementation and evidence are committed locally; no push was performed.
