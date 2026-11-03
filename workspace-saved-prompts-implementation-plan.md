# Workspace saved prompts implementation plan

Status: implemented on `dev/UI-favorites`; targeted validation and review completed. Product documentation awaits explicit draft approval.

## Goal and agreed boundaries

Provide reusable saved prompts from a Markdown file in the current chat workspace. Users choose a prompt, edit it in the normal composer, and explicitly send it. The feature works in new and existing conversations. Saved prompts express a request; skills describe reusable working procedures, and workspace playbooks provide standing instructions. A saved prompt may ask the agent to use a skill without acquiring skill discovery, activation, or execution semantics itself.

Discovery belongs to a backend read service consumed by the UI. It does not execute a context template, create a session, start a task, change history, or inject the library into model context. A user-authored context template can deliberately read the file through existing file tools, independently of this feature. No changes to the default context template or skill catalog are planned.

## Findings from the current code

- `core/chat/workspace.py` normalizes vault-relative workspace paths. `api/services/chat_sessions.py` persists existing-session workspace changes and reports whether a workspace folder exists. `static/js/workspace-picker.js` and `static/js/chat-sessions.js` manage the frontend workspace for both unsent new chats and existing sessions.
- `core/vault_state/pathing.py` supplies configured-vault and vault-relative path resolution. `api/services/vault_files.py:resolve_vault_root` translates configured-vault errors for API consumers. Reuse those boundaries; do not introduce arbitrary filesystem reads through a client-supplied filename.
- `core/authoring/seed_templates/context/default.md` already loads workspace README/playbook information and the vault skill catalog. Saved-prompt discovery must remain independent of that authorable instruction stack.
- `static/app.js:setupEventListeners` currently dispatches a Send click directly to `chatTaskActions.sendMessage`, or to `stopChatResponse` while a response runs. `syncSendButtonState` replaces the Send button's icon and accessibility metadata. Keep the menu as a sibling of Send; long press and Alt+ArrowDown open it without adding another visible button.
- `static/js/chat-task-actions.js:sendMessage` owns model/vault checks, draft consumption, attachments, and task submission. Selecting a saved prompt must not call it. Keep normal Send clicks and Ctrl/Cmd+Enter on that existing path.
- `static/js/chat-composer.js:appendPromptBlock` already appends file-selection prompt blocks, while `static/js/file-references.js:insertReference` handles reference-specific selection replacement. Saved prompts need draft-preserving plain-text insertion; do not silently change either existing operation's semantics.
- General settings are owned by `core/settings/settings.template.yaml`, `core/settings/__init__.py`, and `core/settings/editor_schema.py`, persisted through the current settings repository, and displayed by the generic `static/js/configuration/settings.js` editor. A text setting needs no separate configuration screen or new database table.
- `markdown-it-py` is already a dependency. `core/utils/markdown.py:parse_markdown_sections` uses a regular expression that cannot distinguish headings inside fences and overwrites duplicate names. `core/authoring/shared/markdown_parse.py` uses Markdown tokens, but its sections stop at every heading, including nested headings. Neither section helper directly implements the required saved-prompt format; use Markdown token boundaries without altering their existing contracts.
- Relevant decisions: ADR 0016 keeps user content in vault files; ADR 0024 keeps direct vault interactions chat-native and locks prompt insertion during active work/review; ADR 0028 defines request authority; ADR 0053 makes settings.db authoritative for installation settings.

## Proposed v1 contract

### Configuration and discovery

Add one installation-wide Chat setting, `workspace_prompts_filename`, defaulting to `prompts.md`, with `restart_required: false`. It names a single nonempty Markdown basename, not an absolute path, directory, glob, or workspace-specific override. Validate the same rule for interactive settings updates and persisted-value diagnostics, and reuse that validation when reading the effective setting. Do not silently fall back to a different filename after invalid configuration.

Look only in the effective workspace folder selected by the composer. A blank workspace means no workspace library, not a search of the vault root. An unset workspace, missing workspace folder, or missing library produces an explicit empty result without creating files. There is no parent lookup, recursive discovery, global fallback, per-workspace settings file, or file creation/editor UI in v1.

Follow the existing convention-file lookup approach: prefer the exact configured basename; if absent, accept one case-insensitive match in the same folder. Multiple case-insensitive matches without an exact match are an ambiguity error. Resolve the workspace within the configured vault and resolve the candidate within that workspace. Reject a symlink that escapes the workspace, including a target elsewhere inside the vault. Workspace and filename validation must precede any prompt-content read.

Read on every explicit menu opening so external edits, agent file writes, and setting changes are picked up without restarting. No persistent prompt index or new runtime database is needed. The open menu represents a snapshot; external edits become visible on the next opening.

### Markdown format

A top-level level-two Markdown heading names a prompt. Its body extends to the next top-level level-one or level-two heading, or end of file. Level-three and deeper headings are part of the prompt body. Headings inside fenced code, blockquotes, and lists do not create entries. Preserve file order and Markdown body text, normalizing line endings and removing only surrounding blank lines. Do not render the body as executable HTML, interpolate variables, expand includes, or interpret frontmatter as instructions or configuration.

An optional leading YAML frontmatter block may be ignored using the existing frontmatter parser; invalid frontmatter is a controlled format error. Preamble and document-title text outside a prompt body are not inserted. Ignore empty titles and bodies; a file without usable prompts returns an empty library. Duplicate titles remain separate entries in file order rather than overwriting each other. Assign response-local ordinal IDs so choosing one selects exactly its own body. IDs are scoped to the loaded response and are not durable prompt identifiers.

Example:

```markdown
# Project prompts

## Review current work
Use the review skill to inspect the current changes.

### Focus
Prioritize correctness and unnecessary complexity.

## Plan the next step
Read the workspace plan and propose the next useful increment.
```

Proposed fixed bounds: read at most 256 KiB plus one byte to detect overflow, and accept at most 100 usable prompts. Reject oversized libraries, excess entries, undecodable UTF-8, directories in place of the file, and read failures explicitly. Do not truncate a prompt or return a misleading partial menu. These are bounded-read implementation defaults, not additional user-facing settings.

### API and ownership

Add a thin authenticated `GET /api/vaults/{vault_name}/saved-prompts?workspace_path=...` endpoint. It supports the unsent workspace of a new chat, so it must not require or create a session. Reuse router-installed request authority and the same configured-vault read boundary as the explorer. Do not accept a principal ID, arbitrary source filename, or context-template name from the client.

Return typed fields for normalized workspace path, resolved vault-relative source path when present, library status, and ordered entries containing response-local `id`, `title`, and `body`. Suggested non-error statuses are `available`, `no_workspace`, `workspace_missing`, `file_missing`, and `empty`. Invalid scope, ambiguous filename, invalid format, invalid effective configuration, and read-limit failures use structured API errors with stable error codes. Never return raw OS exception text or absolute paths. Map new domain errors explicitly rather than turning optional-file absence into a generic server error.

Keep reading/parsing and its domain errors in `core/chat/workspace_prompts.py`; keep API translation in a small `api/services/saved_prompts.py` wrapper, models in `api/models.py`, and route wiring in `api/endpoints.py`. The domain reader receives a resolved vault root and validated filename rather than importing API services.

### Composer interaction

Use long press on the existing Send button to open the menu. Alt+ArrowDown while Send is focused provides keyboard access, including with an empty draft. Send advertises the shortcut through its tooltip and accessibility metadata. There is no additional arrow button. Proposed threshold: 500 ms for the primary pointer, cancelled by movement beyond a small tolerance, pointer cancellation, loss of capture, or a changed interaction scope. Use ordinary accessible menu actions, arrow-key navigation, Enter to choose, Escape to dismiss, and sensible focus restoration. Labels locate actions but their exact wording is not a behavioral contract.

A normal click still sends once; a completed long press consumes the release click so it never sends the existing draft. Keep Ctrl/Cmd+Enter unchanged, except that it must not submit while the saved-prompt menu is handling a selection. Do not change the Send/Stop dispatch through competing independent click handlers: give one handler ownership of click consumption and normal dispatch.

Treat long press exclusively as an idle Send interaction. While the button is Stop, preserve immediate normal stop behavior and do not arm a prompt timer. Disable prompt insertion/menu opening during active submission/response, cancellation, pending deferred review, or pending chat-mode save. Close an open menu and cancel pending work when those locks engage. Lack of a selected model must not prevent drafting a prompt. Missing workspace/library can show an empty state; it must not interfere with sending ordinary messages.

Selecting an entry inserts its body as plain editable composer text at the remembered caret, leaves all existing draft characters and attachments intact, dispatches the normal input event, and returns focus to the composer. For a noncollapsed selection, insert at its start without deleting the selected draft text. Do not add automatic variables or transform prompt content. Capture the insertion point before menu focus changes; if the draft changes while the menu is open, refresh or invalidate the saved position rather than applying stale offsets. Submission is always a separate explicit action.

Implement the interaction in `static/js/saved-prompts.js`, with a narrow draft-insertion method in `static/js/chat-composer.js`; wire it through `static/app.js` and load it from `static/index.html`. Use `static/app.css` for the new control/menu. Avoid adding discovery or gesture logic to the already large task-submission module.

Capture vault, session identity, workspace, and request generation for each opening. Abort or discard late responses after any scope change, dismissal, or new request. Recheck scope and locks on selection, not only on fetch completion. Close/invalidate on session load, new-chat reset, vault change, workspace changes, and configuration refresh. A stale menu must never insert text into another conversation, even if both conversations use the same workspace.

## Acceptance and validation

Use the updated installed behavioral-testing skills and the current canonical pytest fixture/admission policy. Add meaningful behavioral checks only. No assertions on product headings, explanatory copy, CSS classes, menu placement, or presentation snapshots. Exact body values are justified here because the selected user-authored content must survive parsing, insertion, and eventual submission unchanged.

1. Add `validation/scenarios/integration/core/test_workspace_saved_prompts.py` using the real isolated `app` fixture and API. Cover ordered extraction, nested headings and fenced heading-like text, duplicate-name selection identity, empty/missing states, file changes observed on reread, configured filename selection, and workspace/vault isolation. Group related inputs within a small number of readable cases rather than creating a scenario per parser branch. The first meaningful red is the absent endpoint/feature through a real started application, not an import or fixture failure.
2. In the same API coverage, demonstrate traversal and symlink escape rejection, ambiguous lookup, invalid encoding, and bounded-read failures using actual files under temporary scenario roots. Retain structured responses and selected evidence; use synthetic data, with no real vault/system roots touched.
3. Extend `validation/scenarios/integration/core/test_settings_editors.py` only for the new setting's actual validation/persistence behavior: custom filename survives reload/restart and invalid values are rejected without changing the stored setting. Use the normal typed settings API and ownership/default upgrade path; a new setting alone should not require a structural database migration.
4. Add `validation/scenarios/browser/test_saved_prompts_browser.py` using `browser_app` and `ui`. Establish that opening/choosing with an empty or populated draft does not create a chat task or consume attachments; caret insertion preserves surrounding draft text; the inserted content is editable and becomes the ordinary submitted user content only after explicit Send. Use the existing controlled remote provider only for the submission case; the real app, agent, API, and stores remain active. This proves application behavior for that provider response, not a live provider's correctness.
5. Exercise short click versus long press, release-click suppression, gesture cancellation, keyboard selection/dismissal, and the active-response Stop path. Check actual task/provider/history outcomes and draft state. Timer advancement may provide environmental timing input, but do not stub application handlers or replace the menu with fabricated state.
6. Exercise delayed responses with `hold_delivery` from `validation/support/browser/browser_fullstack.py`: switch workspace and session before releasing the real response, then show that no stale entries can modify the new draft. Include a configuration/file refresh and unavailable-library case. Use existing lifecycle evidence to establish cleanup, including after failed/dismissed loading.
7. Scope a selected sentinel body to this library and show through real context processing or captured model requests that unselected bodies do not enter automatic context. Keep context/skills implementation unchanged and assert semantic source inclusion rather than instruction prose.

Register all added/changed canonical sources, hashes, and test identities in `validation/registry/pytest-admission.json`; keep import-free inventory and collection-policy checks clean. Keep red/green reports and original failed expectations under ignored `validation/runs/` and record actual commands/verdicts when implementation starts. Use the existing real fixtures and loopback dependency controls; no in-app mocks or new live-service dependencies.

Run selected new/affected backend and browser cases during implementation. Run the complete production Python quality gate (`uv run ruff check .`, `uv run black --check .`, `uv run mypy api core`) before handoff, plus JavaScript syntax and diff checks. Do not run the full scenario suite for this effort, following the user's instruction. Visual review covers menu placement, touch usability, and desktop/mobile wrapping without automated text/layout assertions. Compile CSS with `npm run build:css` if `static/input.css` changes.

## Observability

Use existing API errors, application logs, pytest request artifacts, and lifecycle observations as the primary evidence. Add one structured completion log at the discovery boundary with vault, workspace, source path, status, and prompt count; log a structured failure code for real read/format failures. Do not log prompt bodies or titles. No new semantic event contract is required: API responses, composer content, real submitted user messages, and task counts establish the intended behavior. Do not add recorder events merely to satisfy an instrumentation checklist.

## Implementation sequence

1. Finalize the proposed v1 choices in this plan; preserve any user refinements before coding. The next phase is feature development with the selected failing API/browser acceptance cases.
2. Add the setting and validation, bounded domain reader/parser, API models/service/endpoint, and backend acceptance cases; rerun the same behavioral checks to green.
3. Add the saved-prompts controller, idle-only gesture/click arbitration, keyboard shortcut on Send, and draft insertion; integrate scope and interaction-lock invalidation. Implement against the selected browser acceptance cases and retain meaningful red/green evidence.
4. Review file/context/skills separation, path boundaries, stale-response handling, and normal Send/Stop behavior. Remove incidental assertions and unused code. Finish with targeted scenarios, inventory/admission checks, Python quality gates, and manual presentation review.
5. Commit the coherent implementation after the selected checks pass. Push only when requested.

## Delivery evidence

- Backend acceptance was red against the missing endpoint and setting: `validation/runs/pytest_20261103_173328_f67fa572af/report.md`. The application started successfully; these were feature failures, not setup failures.
- Three new backend cases and the existing settings-editor scenario passed: `validation/runs/pytest_20261103_173958_f3e2aeee28/report.md`.
- The browser acceptance first failed on the missing menu trigger with a real application/browser: `validation/runs/pytest_20261103_173616_39e9904592/report.md`. Earlier missing browser-library setup errors are not red feature evidence.
- Final desktop/mobile insertion and cancelled-gesture checks passed after the viewport/focus refinement: `validation/runs/pytest_20261103_175052_b56439172f/report.md`. Final browser evidence combines these cases, the explicit-send/Stop case, and the updated stale-response scenario. The stale-response case covers both a different conversation using the same workspace and a different workspace selected through the real Explorer: `validation/runs/pytest_20261103_174909_b2dada67ec/report.md`. The other three saved-prompt cases passed in `validation/runs/pytest_20261103_174706_0a2dff92dc/report.md`; that report also preserves the superseded navigation-setup failure in the expanded scope case.
- The existing normal-chat-with-image scenario passed: `validation/runs/pytest_20261103_174609_b485a79c2f/report.md`.
- All 34 policy checks passed; canonical inventory reported zero issues. Complete Ruff, Black, and MyPy checks passed, as did syntax checks for changed JavaScript and diff whitespace checks. Verified lifecycle artifacts report runtime inactive after shutdown and controller cleanup confirmed.
- Tests use temporary app roots, synthetic content and controlled loopback model input. Browser libraries and tokenizer caches were restored outside protected execution; the scenario network policy remains intact. One existing PydanticAI transport deprecation warning remains. No full scenario-suite run was performed, following the user's instruction.
- No default context, skill catalog, database schema, secrets, or existing prompt-insertion behavior was changed. The new filename uses the ordinary settings default/override persistence path.

## Documentation review and approvals

Likely product documentation: `docs/use/getting-the-most.md`, alongside its existing workspace README/playbook and skill explanations, to explain saved prompts, the configurable convention, format, and explicit user selection. Consider `docs/development/architecture.md` only if its API/frontend subsystem description needs a concrete update. No ADR is required by this plan; the proposed storage and API boundaries follow the existing decisions.

Before editing any protected documentation, present the exact affected files and proposed text/diff in chat, obtain explicit approval of that draft, then apply only approved changes. Product docs should describe the resulting contract directly and should not teach migrations or contrast with previous behavior. No protected documentation is edited as part of this planning task; future documentation work remains pending draft approval.

## Follow-up: single Send control

Removed the extra arrow at the user's request. Long press and Alt+ArrowDown on Send open the existing menu, and Escape restores focus to Send. Normal Send and Stop retain their existing click behavior. Existing behavioral scenarios use the shortcut and long press; no presentation assertions were added. The prior proposed documentation draft needs its menu-opening sentence updated before approval.

## Follow-up: saved prompt availability indicator

Show a small dot at the upper right of Send when the current workspace library contains prompts. Check availability when the conversation/workspace changes, configuration is refreshed, the window regains focus, or active work finishes; explicit menu opening also updates availability. Clear previous scope state immediately and discard late responses. Hide the cue while Send is acting as Stop or prompt insertion is locked. The cue uses a CSS pseudo-element so Send/Stop icon replacement preserves it. Announce availability through accessibility metadata. Reuse the bounded library endpoint; no new backend or persistent state is introduced. Review the visual cue directly and rerun existing behavioral interactions without adding layout/text assertions.
