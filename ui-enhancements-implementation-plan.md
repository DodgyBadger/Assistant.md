# UI enhancements implementation plan

## Scope and status

Branch: `dev/UI-enhancements`. This shortlist comes from `/home/codeman/docs/AssistantMD/! To Do.md`, reviewed against the current implementation. The user selected quote-selected-text as the first feature and approved the scoped implementation. Quote selection, Explorer dismissal, the approval toggle, version display, and mkdir path editing are implemented. Type-aware settings were reverted at the user’s request and require a new specification on a future branch. Mobile upload remains conditional on a concrete reproduction; documentation drafts for the latest changes await explicit approval. Keep this branch focused on UI changes and small supporting backend changes; exclude substantial new backend capabilities, persistence redesign, and infrastructure work.

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
| Editable mkdir paths in live review cards | Add a folder-path input and optionally reuse the shared path picker for mkdir review. | `static/js/deferred-reviews.js` plus a narrow operation-specific exception in `api/services/deferred_reviews.py`. The current override allowlist rejects changed mkdir paths; retain operation/policy immutability, ordinary path validation, and restrictions for other operations. Update ADR 0023 to describe the resulting contract. | Extend `file_ops_inline_edit_policy` to verify edited mkdir execution, invalid/out-of-vault path rejection, and unchanged immutable-field restrictions; browser smoke test editing and locked cards. |

## Conditional candidate

- Mobile attachment upload failures and actual error display: reproduce the mobile-specific chat attachment path first. Include only if the fix is confined to client handling, error presentation, or a small diagnostic correction within the existing upload contract. If it requires new ingestion behavior, transport redesign, or deployment changes, defer it to a separate branch. Target mobile browser reproduction plus `chat_multipart_upload_limits` for any affected upload behavior.

## Excluded from this branch

- Vault Explorer move/copy choice: the current UI action named copy copies a path, while the mutation API supports create, move, and delete without a copy operation. A real file-copy feature needs backend operation semantics, access validation, tracked mutations, and conflict handling.
- Binary PDF and `/exchange` support in `file_write`: tool and filesystem capability work.
- Type-aware general settings controls: deferred by the user for a future branch after more specification.
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

## Remaining branch implementation

The user authorized completing the remaining scoped tasks one at a time without another planning cycle: a visible per-session file approval toggle, application version on System, editable mkdir review paths, and a bounded mobile-upload investigation. Keep substantive backend work excluded. Documentation edits under `docs/`, `README.md`, and `RELEASE_NOTES.md` require approval of the actual draft under the current AGENTS.md; hold those edits until approved. Browser checks use Obscura through CDP as requested by the user.

- Approval toggle: reuse the existing session mode API and selector as canonical state; show the toggle next to the main composer, synchronize session/default loads, lock during active runs/reviews/saves, and restore the persisted state if a PATCH fails. Validate via Obscura CDP and the existing session mode scenario.
- Approval toggle completed: seven Obscura CDP behavior checks and `file_ops_inline_edit_policy` passed. CSS build, JS syntax, targeted Ruff/Black, and whitespace checks passed. Focus layout reserves space for the always-visible toggle.
- Version display: read the authoritative application version from shipped `pyproject.toml`, expose it in System status and FastAPI/OpenAPI metadata, and display it at the top of System. No build identity is invented and no release-pipeline change is required. Validate API values against project metadata and display through Obscura CDP.
- Version display completed: the expanded `api_endpoints` scenario passed, including API/OpenAPI agreement with project metadata. Obscura confirms display of the API version. Full Ruff, Black, and MyPy gates passed.
- Mkdir review: add a folder-path input to the existing deferred review card and allow only the mkdir path to change in approval overrides. Execute through the same file-write tool and vault path validation; preserve operation/policy immutability and non-mkdir target immutability. Validate persisted review overrides, actual corrected directory creation, traversal rejection, locked-card rendering, and submitted UI payload. ADR 0023 needs a narrowly scoped text update; hold that draft for user approval.
- Mkdir review completed: three Obscura CDP checks and the expanded `file_ops_inline_edit_policy` plus `deferred_review_task_skeleton` scenarios passed. Corrected paths create the intended directory, traversal attempts fail, and operation/policy and other-operation targets remain immutable. Full Ruff, Black, and MyPy gates passed. ADR prose remains pending explicit draft approval.

## Final hardening and pending input

- The approval toggle now restores the actual pre-toggle mode even if the session list has not refreshed yet; the added Obscura check passes. Before the settings rollback, focused results were 23 Obscura CDP checks, 21 existing module checks, and 3 Node quote/draft checks pass. Full Ruff, Black, MyPy, and CSS build pass. The final deterministic `integration/core` profile passed 134/134 scenarios with zero failures or errors (run `20261016_193914_264287`; report `validation/runs/reports/20261016_193914_264287.md`).
- Mobile upload investigation found a client guard that silently discards files without a MIME type. This is a candidate cause, not a reproduction of the user's mobile failure. No upload behavior or contract was changed. Obtain the phone/browser, image/file type, picker source, and whether the failure occurs during selection, Send, or response processing before implementing a fix.
- No edits were made under docs in this batch because the current AGENTS.md requires approval of the actual documentation draft. Proposed drafts to present in chat are below.

### Pending usage-guide draft

For `docs/use/getting-the-most.md`, add the following chat paragraph and a System settings section:

Use **File approvals** in the Chat Settings modal header to turn inline review of file changes on or off for the current session. The control is unavailable while a response or review is active. Folder-creation approval cards let you correct the folder path before approving.

#### System settings

System shows the application version.

### Pending ADR draft

For `docs/development/adr/0023-split-file-tools-and-inline-edit-review.md`, replace the review override restriction sentence with:

Review overrides may edit operation content, a move destination, or a mkdir directory path. Other operation targets and overwrite policy remain immutable.

## Type-aware settings rollback

The user requested rolling back type-aware settings and reserving that work for a future branch with a fuller specification. Revert commit `fb9debf` while preserving the later approval-toggle fix, application version, quote selection, Explorer dismissal, and mkdir path editing. Restore the plain-text settings editor and original settings API, remove editor metadata and feature-specific validation, and remove type-aware settings from the pending documentation draft. No runtime settings or persisted user values need changes. Validate with the existing settings/API checks and Python quality gate; remaining UI checks use Obscura CDP.

Rollback completed: settings implementation files and their API scenario match `fb9debf^` exactly. The targeted `api_endpoints` scenario passed (run `20261016_195305_233128`), eight existing configuration checks passed, and twelve remaining Obscura CDP checks passed. Full Ruff, Black, and MyPy gates passed. The earlier full-profile result predates this rollback; this branch-development change was validated with the focused checks above.

## Approval toggle placement revision

The user requested moving file approvals out of the main chat area and into the Chat Settings modal header, outside collapsible Options. Park the persistent button in the existing hidden controls source, move it into the header on open, and restore it before destroying the modal. Keep session state, save rollback, and busy/review locks unchanged. Remove the duplicate visible mode selector and the composer footer, and restore the original focus-layout height allocation. Validate with Obscura CDP using the actual application markup, including collapsed Options, toggle interaction, close/reopen, and lock behavior; run existing session/composer checks. No protected documentation is edited; the pending usage-guide draft now refers to the modal header.

Placement validation passed: 36 Obscura CDP checks, including the modal lifecycle and header bounds at 320px, 390px, and 1024px with the application's markup/styles, plus five existing session-control/composer checks. CSS build, JS syntax, targeted Ruff/Black, and diff checks passed. Header checks confirm the title, approval button, and close button fit without overlap. No backend or persisted-state contracts changed, so no broader deterministic rerun was needed for this placement change.

## Approval icon revision

Replace the header text toggle with the shared Lucide user-check icon button and position it in a title cluster, separated from the close action. Keep state available in the accessible name and tooltip. Reuse the workspace icons' accent-color/drop-shadow rules, driven by the active theme's CSS variables, for the enabled glow; remove the glow when off. Keep all existing session persistence, modal lifecycle, and busy/review locks. Validate through Obscura CDP at phone and desktop widths and across all bundled themes; no protected documentation or backend changes.

Completed: the icon uses the existing shared icon-button styling and workspace glow rules next to the modal title. Obscura CDP passed 58 checks covering behavior, layout at 320px/390px/1024px, theme-accent colors and neutral off states in all six themes, and the theme-variable glow declaration. Obscura does not render CSS filters, so the glow itself requires manual visual confirmation. Five existing session-control/composer tests, CSS compilation, JS syntax, targeted Ruff/Black, and whitespace checks passed.

## In-flight Chat Settings availability — scoped proposal

Allow opening and closing Chat Settings during an active response. Allow selecting file approvals, model, thinking level, and context script for the next submitted turn; the running task retains its accepted configuration. Keep file approvals locked during a pending deferred review and during a save. Keep vault/workspace changes, session switching/new-session actions, and active-session deletion/context-strategy upgrades unavailable while running. Session filtering and searching are read-only and can remain available. This proposal does not change approval policy for a running tool call or a resumed review; ADR 0023 pins tool approval binding to execution configuration.

Implementation would touch app control locks, chat-selection mode guards, and session-modal action guards/disabled states. Persisted session mode is involved: investigate and cover startup/queued-task writes of the accepted mode so an in-flight preference change cannot be overwritten. Preserve save rollback and ensure response completion/review events do not reset the next-turn preference. Validate through Obscura CDP with an active-response fixture, including opening the modal, changing next-turn settings, retaining blocked session/workspace actions, pending-review locks, and rollback; extend a deterministic mode-persistence scenario if backend persistence changes are needed. Next phase is Feature Development after settling whether the requested toggle targets the next turn or the running response. No application behavior changed during this assessment.

Implementation: save an accepted queued turn's approval preference before exposing its task ID, and keep queued execution bound to its captured policy without writing the preference again. The UI keeps approvals locked during initial acceptance/history loading, saves in-flight changes through the existing endpoint, and preserves newer choices when a history reload races a save. Open the modal and edit next-message controls during execution; show an in-flight scope notice. Guard session switching/new-session actions and active-session deletion/upgrades in handlers as well as their disabled controls; retain existing vault/workspace locks and pending-review approval locks. Validate the persisted preference and accepted mode through the existing queue scenario, exercise the modal/locks via Obscura CDP, and run session/composer checks plus the full deterministic profile and Python quality gate for the task-persistence change. No protected docs edits are included.

Completed and validated: 93 Obscura CDP checks passed, including in-flight modal/lock behavior at 390px and 1024px, startup and review locks, save rollback, next-message scope, and retaining Stop while saving. Eight existing/extended frontend tests passed, including history responses racing preference saves and saves finishing for another session. The targeted queue and deferred-review scenarios passed. The full deterministic `integration/core` profile passed 134/134 scenarios with zero failures/errors (run `20261016_210921_261266`, report `validation/runs/reports/20261016_210921_261266.md`). Full Ruff/Black/MyPy gates, JavaScript syntax, CSS compilation, and diff whitespace checks passed. Send waits for an outstanding approval save while drafting and Stop remain available. Protected documentation was not edited.

## Selection icon visual refinement

Completed: reduce the collapsed selection button from 36px to 32px and its glyph from 16px to 14px, use 72% opacity with a softer shadow, and restore full opacity on hover or keyboard focus. Keep the expanded instruction panel fully opaque and preserve theme colors. The three existing quote-selection checks, CSS build, and whitespace checks passed; this CSS-only refinement does not change selection or chat behavior.

## Pre-merge review corrections

The user authorized correcting both peer-review findings and a narrow audit of existing logging. Keep documentation proportional to this UI release: do not expand usage docs for minor controls or add an ADR for these changes. Preserve the user's uncommitted release-notes and upgrading-guide edits.

Affected contracts: (1) a pending approval-preference save belongs to its vault/session and must retain its intended mode across A→B→A navigation, save success/failure, and history refresh; (2) a chat request rejected because a review is already pending must not mutate the saved mode, while admitted queued turns keep their accepted execution policy and cannot overwrite newer preferences when execution begins. Keep the gate-time pending-review check to cover reviews created during queuing. These corrections touch existing UI save/load coordination and queued-task preference persistence, without introducing new API fields or operation families.

Validation: add regressions for both reproduced sequences, run relevant frontend and deterministic queue/review scenarios after corrections, inspect existing task/review/mutation activity records, then run the complete Ruff/Black/MyPy gate and full `integration/core` profile against the stable corrected code. Review modal behavior through Obscura CDP if browser testing is needed. Record hardening-stage results and concrete event evidence here; no protected documentation changes are planned.

Hardening stages 1–5: scope is limited to session-specific preference save/load coordination and review-blocked task admission; original accepted execution policy and file-mutation ownership remain unchanged. Structural review retained the existing controllers and endpoints, with no new service, API field, type suppression, or duplicated policy layer. State/failure regressions cover A→B→A navigation, successful and failed saves, both save/history completion orders, and a rejected normal-mode request preserving the saved inline-edit preference. Targeted queue/review scenarios and 20 frontend/module checks pass. Obscura CDP passes 93 existing modal, theme, approval, version, and mkdir checks; native phone selection/keyboard behavior remains a manual interaction rather than a browser-engine assertion.

Logging audit: actual isolated activity records contain execution task created/started/completed/cancelled events and chat_turn_started/chat_turn_completed with task/session/vault identities and compact counts. chat_deferred_review_created, chat_deferred_review_claimed, and chat_deferred_review_terminal retain artifact/session/vault and originating/resumed task identities; failed review settlement includes controlled error guidance and error_type. Claim records correctly leave a not-yet-admitted resumed task ID empty. Corrected mkdir arguments continue through the existing file_write and vault directory mutation path. No branch-specific logging gap or new operation family was found; selection, icon, and modal clicks do not need extra activity events.

Stage 6/documentation decision: the user directed proportionate documentation and no ADR for these minor UI changes. Earlier usage-guide and ADR draft proposals are not proceeding. The approved v0.9.1 release entry is already committed; preserve subsequent user edits to release notes and upgrading instructions. Project version, lockfile version, and release heading agree on 0.9.1, and uv lock --check passes. Full Ruff/Black/MyPy, JavaScript syntax, and whitespace checks pass. The final deterministic profile is running against the corrected code; final result and commit will be recorded below.

Final result: both P2 findings are corrected and independently rereviewed with no remaining findings. The A→B→A regression failed against the original implementation and passes with the fix; review-blocked requests preserve inline_edit both at admission and after asynchronous failure. Full integration/core passed 134/134 with zero failures/errors (run 20261020_192010_402213; report validation/runs/reports/20261020_192010_402213.md). Full Ruff/Black/MyPy and lock consistency checks, 20 module checks, 93 Obscura CDP checks, JavaScript syntax, and whitespace checks passed. No new logging events, user-guide expansion, or ADR were needed. No tracked runtime/secret files or temporary probes are included; user edits to RELEASE_NOTES.md and docs/setup/upgrading.md remain outside this correction commit.

## Session picker initial-message preview — proposal

The user is considering a bounded preview of the initial user message on hover over unnamed sessions, with truncation for long messages. Current session summaries expose title/identity/activity and mode/context metadata but no initial-message text; canonical messages already retain the original user prompt. This is a small read-only extension: obtain a bounded, whitespace-normalized first-user-message excerpt (proposed limit 300 characters with ellipsis) for authorized session IDs, include it in the existing list/search row payload, and expose it as a plain-text hover/focus preview without requesting full history on hover. Use the existing indexed canonical message records and session access boundary; no model call, schema migration, stored title update, or new persistent projection is needed. Only unnamed rows need the preview; empty sessions have no excerpt. Native touch access would need a separate explicit trigger because hover is unavailable.

Validate bounded truncation and correct first-user-message selection, empty and compacted sessions retaining canonical history, authorization/vault isolation, batched summary fetching, and escaped tooltip text. Extend existing session API/discovery scenarios and session-controls UI checks. Next phase would be Feature Development after the user chooses to include this item; no application behavior is changed by this assessment. Preserve uncommitted release-notes/upgrading edits.

Preview feasibility audit: read the checkout-local system/chat_sessions.db in SQLite read-only/query-only mode. It contains 70 sessions (33 named, 37 unnamed). Manual assessment of the unnamed sessions' earliest retained user-prompt text grouped 11 as task/topic-specific, 10 as tool/testing-oriented, 8 as generic or context-dependent, 7 as greeting-only openers, and 1 with no user-text message. These are judgment categories, not an automated usefulness score, and the data includes test sessions. Only four unnamed openers exceed 300 normalized characters. Five unnamed histories already begin mid-conversation, so an earliest-retained-message preview cannot promise the original opener for older data. Skipping greetings improves some previews but frequently exposes another generic vault-orientation request. This evidence weakens the case for adding first-message previews universally; richer session identification can be deferred to a separate branch. No database or application behavior was changed.

## Quoted prompt disappears after response — correction

The user reported that a selection prompt appears immediately but disappears after the response/history refresh. The saved quote begins with a wikilink (or Markdown link for unusual paths). Existing API projection and canonical SQL grouping heuristically classify any bracket-prefixed text containing a closing bracket as tool traffic, so a genuine user prompt is hidden or merged into an assistant turn when saved history is loaded. The original message remains stored. Replace that presentation heuristic with classification based on provider-native tool call/return/retry parts and identifiers, keeping actual tool content withheld and preserving canonical display pagination/grouping. No migration, prompt-format change, or UI workaround is needed.

Validation targets: reproduce with a stored wikilink quote and ordinary Markdown-link user message; assert API detail and canonical timeline retain their user role and exact content before/after completion and reload, while genuine tool messages remain hidden. Extend the existing history-renderer harness for frontend parity and relevant deterministic replay/timeline scenarios. Run affected checks, the full Python quality gate, and full integration/core against the stable correction. Preserve the user's documentation edits and the deferred session-preview proposal; no product docs or ADR changes are planned.

The session-preview proposal is deferred from v0.9.1 at the user's request. For the quote-reload correction, structural classification now uses ToolCallPart, ToolReturnPart, NativeToolReturnPart, and tool-scoped RetryPromptPart; SQL uses matching serialized kinds. Tool contents remain withheld even when a part lacks a call ID, and visible assistant prose remains intact. The new deterministic replay regression failed against the original classifiers and passes after correction; it checks exact stored prompt text, API projection, canonical grouping, and private tool-result withholding. The actual history renderer passes the new regular/session-map refresh regression. Twenty-one focused frontend/module tests and three quote-format checks pass; full Ruff/Black/MyPy and deterministic profile are being verified against the frozen final correction. No database migration, new logging operation, product documentation, or ADR is needed.

Final quote-reload validation passed: full integration/core 134/134, zero failures/errors (run 20261020_230830_014286; report validation/runs/reports/20261020_230830_014286.md), 21 focused module tests, three quote-format checks, full Ruff/Black/MyPy, and whitespace checks. Independent rereview found no remaining issues. Existing stored quoted messages are preserved and can become visible again through the corrected projection without a database repair. Browser production code was unchanged; rendering parity was checked through the actual history-renderer module harness. The correction commit excludes the user's release-notes and upgrading-guide edits.
