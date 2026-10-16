# UI enhancements implementation plan

## Scope and status

Branch: `dev/UI-enhancements`. This shortlist comes from `/home/codeman/docs/AssistantMD/! To Do.md`, reviewed against the current implementation. The user selected quote-selected-text as the first feature and approved the scoped implementation. Quote selection and the Explorer dismissal follow-up are implemented; other candidates remain backlog. Keep this branch focused on UI changes and small supporting backend changes; exclude substantial new backend capabilities, persistence redesign, and infrastructure work.

## First feature: quote selected text

### Requested interaction

Select text in the open Markdown document preview to reveal one floating chat icon near the selection. Activating the icon slides open a compact prompt panel with a multiline, vertically expandable textarea styled like the regular chat entry. The panel has two action buttons: **Add to draft** and **Send now**. Both construct the same text block: the open file's full vault-relative path, the selected text as a Markdown blockquote, then the typed prompt. Adding several selections builds one editable main-chat draft in document order chosen by the user; it does not create background jobs or submit anything automatically.

### Interaction contract

- Limit the first release to the current file's rendered Markdown body in preview mode. Exclude the source editor, frontmatter properties, revision previews, chat transcript, and non-Markdown files. Use the visible selected text rather than reconstructing Markdown source formatting.
- Capture text, path, vault, and chat context when opening the panel, before focus moves into the textarea. Show the source path and a compact read-only quote preview so the user can check what the prompt refers to. Preserve line breaks and prefix every selected line, including blank lines, as a blockquote.
- **Add to draft** appends the block to the end of the main chat entry with blank-line separation. Preserve all existing draft text and images, regardless of the main entry's old cursor/selection. Keep the document and its scroll position open, clear the successful popup entry, collapse back to the selection action, and show brief feedback. Do not move keyboard focus to the obscured main composer. The user can select another passage and repeat, including passages in other documents in the same vault/session.
- **Send now** submits only this popup's block to the current chat, using its selected model, thinking, context template, workspace, and approval mode. Preserve the main draft and its pending images. After task acceptance, dismiss the file/explorer layers to show the chat response. Retain the popup and its contents if task creation fails; report the failure there. Task acceptance, rather than stream completion, is the success boundary for dismissing the panel.
- Permit a blank typed prompt so the user can add or send just a path and quotation. Require a non-whitespace selection. Enter inserts a newline; Ctrl/Cmd+Enter invokes Send now, matching the main composer shortcut.
- Before expansion, clearing or changing the selection removes or repositions the icon. Once expanded, keep the captured selection stable while typing or scrolling. Do not silently replace a filled prompt when selecting another passage. Offer explicit dismissal; outside clicks do not discard a filled panel. Escape collapses the panel first and must not also close the containing file view. Explicit dismissal may discard the unsent popup entry; file navigation, close, and mode changes must guard against silently losing a filled prompt. No reload persistence for the popup is planned.
- Anchor and clamp the icon/panel to the visible preview/viewport, reposition on scroll and resize, and accommodate the mobile keyboard. Preserve native text selection and copy behavior. Support touch and keyboard selection, accessible icon/button labels, predictable focus return, and reduced-motion preferences.
- Respect existing active-run and pending-review locks for both actions, checking again at activation. Keep document reading available. Disable actions with an explanation when the preview belongs to another vault or its captured chat context is stale; never route an old popup into a different session. Preserve the entry when a lock appears.

### Shared text format

For an ordinary path, each added or immediately sent block has this shape. Use the existing file-reference convention, handling unusual path characters through the existing resolver's supported representation; never interpolate selected text as HTML.

```markdown
[[Notes/Example.md]]

> First selected line.
>
> Another selected paragraph.

The user's typed instruction.
```

Each queued selection repeats its own path, quote, and prompt so blocks remain understandable when collected across files. Keep the ordinary chat API payload and message persistence contract.

### Implementation boundaries and evidence

- `static/js/file-references.js` owns the active file preview, path/vault context, preview/edit/history modes, navigation, and interaction-lock updates. Attach selection handling only to `#vault-file-modal-preview` while Markdown preview is active, and remove listeners and temporary UI on close/replacement.
- Add a focused selection/prompt UI controller rather than accumulating positioning, selection capture, and panel lifecycle logic inside the file controller. Wire its callbacks through `static/app.js`; register its script in `static/index.html` and add styles in the appropriate existing stylesheet.
- `static/js/chat-composer.js` is the home for an append-block operation that dispatches the input event without replacing existing text or stealing focus. Existing `insertReference` inserts at the cursor and focuses the main input, so it does not provide the desired queueing behavior unchanged.
- `static/js/chat-task-actions.js` uses explicit per-send options to isolate popup submission from main attachments, preserve popup focus after failed starts, and expose task-accepted/start-failed feedback without waiting for stream completion. First-send session allocation updates the captured popup context so a failed start remains retryable. One task creation/streaming path serves both composers; ordinary sends retain their draft and attachment consumption behavior.
- ADR 0024 requires extending the shared Explorer and respecting interactive locks. ADR 0020 requires the existing task-owned chat execution path. No backend endpoint, schema, settings, secrets, or runtime-root changes are expected. Queued text lives in the existing main draft; sent text uses existing message persistence.

### Acceptance checks and next steps

1. The scoped defaults were approved and implemented: Send now reveals chat, empty instructions are allowed, and selection is limited to the current Markdown preview.
2. Add focused behavior coverage alongside `validation/test_file_reference_modules.py` and `validation/test_chat_composer.py`, plus a suitable chat task-action harness. Verify repeated append order and draft preservation, multiline quote formatting, captured context isolation, attachment isolation, request failure retention, duplicate-send prevention, and task-acceptance timing.
3. Browser-smoke the complete selection-to-panel flow: mouse/touch/keyboard selection, focus changes, multiline resize, panel placement with mobile keyboard, scroll retention after Add to draft, cross-document accumulation, Send now revealing chat, busy/pending-review transitions, navigation/mode/close cleanup, and accidental-dismiss protection. Use a deterministic local submission fixture where possible; live model calls are opt-in.
4. Build CSS and run the full deterministic `integration/core` profile during hardening. Record completed checks below before handoff.

## Implementation evidence

- Added `static/js/vault-selection-prompt.js` for selection capture, context binding, popup lifecycle, viewport positioning, navigation guards, and both actions. Shared icons and button styles match the existing UI. No backend, settings, secrets, or persistence schema changes.
- Direct send preserves the main draft and images, disables duplicate submission and dismissal while starting, and retains the popup with focused input on failure. Unsaved file edits require an explicit discard decision before a successful send can close the file view.
- Added behavior checks in `validation/test_quote_selection.cjs` and real Chromium checks in `validation/test_quote_selection_browser.py`. Updated the existing file-navigation harness to isolate its selection-controller dependency.
- Passed 3 focused Node formatting/draft checks and 8 existing file-reference/composer checks. Passed 11 real-browser checks for both actions, cross-file accumulation, mouse selection, touch activation, short/mobile viewports, context/review locks, navigation cancellation, failure/retry, duplicate submission, scroll retention, and ordinary attachment sends. Desktop and mobile screenshots were visually inspected. Device-native selection handles and the physical iOS/Android keyboard still benefit from a device smoke check; the automated mobile checks use Chromium touch emulation and viewport resizing.
- `npm run build:css` passed; generated CSS has no content changes. Targeted Ruff/Black checks and JavaScript syntax checks passed. No production Python files changed, so the production Python quality gate is not applicable.
- The targeted deterministic scenarios `vault_file_reference_api` and `chat_task_event_stream_api` passed (2/2), using isolated bootstrap roots under `/tmp/assistantmd-quote-validation`.
- The full deterministic `integration/core` profile passed: 134/134 scenarios, zero failures or errors. Run ID: `20261016_184849_228267`; report: `validation/runs/reports/20261016_184849_228267.md`.
- Existing chat task lifecycle logging covers direct submissions. Selection text and unsent drafts are kept in browser state and introduce no telemetry content or new backend event contract.

## Explorer dismissal follow-up

The new To Do item requests that X and clicking outside the modal exit the entire Vault Explorer to chat, while Back alone follows file navigation history. This is implemented as a frontend-only follow-up. File previews can sit above the Explorer tree; dismissing only the file modal exposes that underlying tree and requires another close.

- Explicit dismissal uses one shared operation that respects unsaved file and selection-prompt guards, closes the file and underlying picker, clears navigation history, and returns focus to the main chat entry. Reuse it after successful selection-prompt submission.
- File replacement, Back, and the explicit browse-tree action retain their existing navigation paths. Cancellation of a discard decision must leave both layers and history intact. Keep the root picker's busy-operation restrictions.
- Browser checks cover X and backdrop dismissal with nested file history and a tree beneath, Back retaining the underlying tree, clean reopen after dismissal, and cancelled file/prompt discard. No backend, settings, secrets, or persisted-state changes are expected.
- Validation passed: 25 browser/module checks and the `vault_file_reference_api` deterministic scenario (run `20261016_191841_931323`). JavaScript syntax, targeted Ruff/Black, and diff whitespace checks passed. The prior full deterministic run remains 134/134; this follow-up changes only frontend dismissal/navigation and does not alter the backend contracts exercised by that profile, so it was not repeated. Next step: manual UI review; other branch candidates remain available.

## Recommended candidates

| Item | Proposed scope | Affected areas and backend boundary | Validation target |
| --- | --- | --- | --- |
| Faster per-session approval toggle | Make the existing normal/inline-edit mode easier to reach and understand, with a visible current state. | `static/js/chat-selection.js`, `static/js/session-controls.js`, `static/app.js`, `static/index.html`; reuse existing session mode persistence and busy/pending-review restrictions. This controls interactive file-write review, not every approval mechanism. | Browser smoke test: toggle, reload and reopen session, verify retained mode and disabled controls during a run or pending review. |
| App/build version on System tab | Show an authoritative application version; include build identity only if supplied by existing packaging or a small metadata addition. | System UI and a narrow read-only metadata addition if needed. `pyproject.toml` and `package.json` have different version values; choose the application source explicitly rather than treating the npm package version as authoritative. Exclude release automation redesign. | Check displayed value against its chosen source, including behavior when optional build identity is unavailable. |
| Quote selected vault Markdown into chat | Select text in a Markdown preview, reveal a floating chat action, collect a prompt, and submit the selected text as a quote with that prompt. | Shared Vault Explorer, preview rendering, composer, CSS; reuse existing chat submission. Preserve the quote when focus moves, retain document context, escape content, and respect active-run restrictions. No new chat payload or server-side selection storage. | Browser smoke test: mouse and touch selection, multiline quote, prompt submission, dismissal, keyboard access, busy state, and selection isolation from other UI text. |
| Type-aware general settings controls | Render booleans, supported choices, and numbers with appropriate controls; retain explicit text/JSON fallbacks. | `static/js/configuration/settings.js`, settings models/service. Current `SettingInfo.value` is a string and `SettingsEntry` has no type/choice metadata. Include a small explicit metadata contract sourced from authoritative setting definitions; preserve string update payloads and current persistence. Do not infer types solely from string contents or redesign settings storage. | Verify boolean false, numeric zero, supported choices, text that looks numeric, JSON fallback, invalid input, save/reload, and restart notices. |
| Editable mkdir paths in live review cards | Add a folder-path input and optionally reuse the shared path picker for mkdir review. | `static/js/deferred-reviews.js` plus a narrow operation-specific exception in `api/services/deferred_reviews.py`. The current override allowlist rejects changed mkdir paths; retain operation/policy immutability, ordinary path validation, and restrictions for other operations. Update ADR 0023 to describe the resulting contract. | Extend `file_ops_inline_edit_policy` to verify edited mkdir execution, invalid/out-of-vault path rejection, and unchanged immutable-field restrictions; browser smoke test editing and locked cards. |

## Conditional candidate

- Mobile attachment upload failures and actual error display: reproduce the mobile-specific chat attachment path first. Include only if the fix is confined to client handling, error presentation, or a small diagnostic correction within the existing upload contract. If it requires new ingestion behavior, transport redesign, or deployment changes, defer it to a separate branch. Target mobile browser reproduction plus `chat_multipart_upload_limits` for any affected upload behavior.

## Excluded from this branch

- Vault Explorer move/copy choice: the current UI action named copy copies a path, while the mutation API supports create, move, and delete without a copy operation. A real file-copy feature needs backend operation semantics, access validation, tracked mutations, and conflict handling.
- Binary PDF and `/exchange` support in `file_write`: tool and filesystem capability work.
- Settings persistence/migration model: lifecycle and storage architecture work.
- Model aliases and fallback profiles: model resolution, validation, execution, and persistence work.
- JSON URL ingestion: ingestion behavior and artifact policy work.
- Trusted multiuser support: authentication, authority, ownership, and visibility contracts.
- Obscura browser replacement: browser tool, policy, packaging, and execution work.
- Advanced system prompt editing: ownership, validation, versioning, reset, and backup contracts beyond a UI surface.
- Recurring dependency maintenance: separate maintenance scope.

## Implementation sequence and validation

1. Scope and implement quote-selected-text first, following the detailed feature section above. Other candidates remain available for later selection. Mobile upload remains conditional on reproduction.
2. Move to Feature Development and read its phase guide. Refine each selected item's behavior and acceptance checks before editing; inspect its implementation and relevant ADRs in detail.
3. Use focused existing module checks and deterministic scenarios while developing, plus browser smoke checks for interaction and responsive layout. Build CSS with `npm run build:css` when styling changes.
4. Once implementation is stable, run `python validation/run_validation.py run integration/core` during hardening or merge preparation. Live-service and experimental scenarios remain opt-in.

No secrets or configured runtime roots need changes. Settings UI changes continue to write user settings through the existing API; approval mode continues to persist through the existing session contract. Do not alter local persistent runtime state merely to prepare this shortlist.
