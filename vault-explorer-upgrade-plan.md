# Vault Explorer Upgrade Implementation Plan

## Objective

Reduce routine Vault Explorer and content-import friction across several co-equal gaps by replacing scattered row menus with a selection-aware operation bar, making action targets predictable, adding bounded vault-content search, supporting direct upload and URL import to folders, combining upload with Markdown import when requested, assigning import initiation to the Explorer and import observability/defaults to the Dashboard, and adding bounded batch workflows while preserving the shared chat-native Explorer, canonical filesystem storage, mutation safety, durable activity history, and durable ingestion jobs.

## Current Behavior

- The Vault Explorer is a right-side modal shared by toolbar access, workspace selection, file and directory links, and activity/revision entry points.
- The main surface is a lazy-loaded tree with workspace/vault scope and substring path search. Folder names expand or collapse the tree; file names open a separate preview/edit/history modal over the Explorer.
- Header actions create a file or folder from a typed vault-relative path and upload one or more local files to a typed vault-relative destination. When workspace scope is active, these fields default to the workspace root; expanding a folder does not make it the target of a header action.
- Each row exposes copy-path and overflow actions. Folder rows can create children and become the session workspace; all rows can be added to the prompt, renamed, moved, or deleted subject to current safety rules.
- Move mode lets the user choose an existing destination folder from the tree, rename the item in the same form, or jump to the workspace or vault root. Upload does not reuse this folder-selection interaction.
- Importing an uploaded PDF or image into Markdown is a separate workflow. The user must first upload the source and then ask chat to import its path, or upload it to `AssistantMD/Import` and manually trigger an inbox scan.
- Dashboard → Import currently combines four responsibilities: durable job monitoring, temporary per-run PDF/OCR overrides, inbox submission, and URL submission. Its “PDF conversion options” are request overrides for the adjacent forms rather than persisted import defaults.
- Mutations are create-only where appropriate, path-validated, attributed to Explorer activities, reflected in the manifest, and locked while an interactive agent mutation is active. Files use optimistic concurrency for edits, and retained revisions can be previewed and restored.
- There is no persistent current-folder concept, breadcrumb, drag-and-drop target, ordinary row selection, multi-selection, or batch mutation API. Refresh and successful mutations rebuild the root view and can discard expansion, search, scroll, and location context.

## Findings

### Strengths to preserve

- One shared Explorer prevents picker, browser, and editor behavior from drifting.
- The existing lazy tree scales better than eagerly rendering a vault and already supports direct-path reveal, search, pagination, mobile layout, and read-only locking.
- File preview, text editing, revision history, prompt references, and workspace selection make the Explorer useful without trying to reproduce Obsidian's knowledge-management feature set.
- Server-side path validation, create-only upload semantics, mutation attribution, collision rejection, and canonical vault files are sound boundaries that the UX upgrade should not bypass.

### Co-equal usability gaps

1. Action targeting is inconsistent. Workspace choice displays a dedicated “Use” button, Move turns the tree into a destination picker, Upload requires a typed relative path, header Create requires a complete path, and per-folder creation is hidden in an overflow menu.
2. The Explorer does not answer the basic file-manager question “which folder am I working in?” Expanded folders are navigation state, not a location, so top-level actions have no obvious contextual target.
3. Upload is detached from tree context. A top-level button asks for a destination after file selection instead of applying to an explicitly selected folder or the current folder.
4. Upload and Markdown import are unnecessarily separate for the common PDF/image workflow. The ingestion service already accepts vault-relative files and an explicit output destination, but the Explorer cannot submit those durable jobs directly.
5. Common repeated work is row-by-row. Every existing item has its own overflow menu, but users cannot select several items and then move them, import eligible items, or add them to a prompt together.
6. Context is fragile. Refreshing or completing a mutation resets to vault scope, clears search, collapses the tree, and only attempts to reveal one path; large paginated folders can make that reveal incomplete.
7. Search is useful but behaves as a replacement listing rather than an explicit search state. Starting a move from results leaves the user choosing a destination within the filtered result set, which is not a dependable folder-navigation experience.
8. The markup declares a tree but does not implement the expected tree keyboard model, focus movement, or explicit selection semantics. Adding multi-select without equivalent keyboard and touch actions would create input-method gaps.
9. Import initiation is split between chat, the Explorer upload path, and Dashboard forms even though source and destination selection are naturally vault-contextual. URL import is especially detached from its output location because the current form selects only a vault and otherwise relies on the configured output pattern.
10. Explorer search currently matches only file and folder names/paths. AssistantMD already has ripgrep-backed vault text search for the `file_read` tool, but no structured API or Explorer results view exposes content matches to the user.

## Recommended Interaction Model

Use a persistent, selection-aware operation bar at the top of the Explorer and remove mutation commands from each row's overflow menu. Keep row labels for navigation and file opening, and add a visible checkbox or selection control to every row so selecting an operand never depends on desktop-only double-click, modifier-key, hover, or long-press conventions.

The bar should show the current location and selected count, keep common applicable operations visible, place lower-frequency applicable operations in one bar-level overflow menu on narrow screens, and disable inapplicable operations with a short reason. This creates one place to learn the Explorer's action model without making every row carry a separate menu.

| Selection | Applicable operations |
| --- | --- |
| None | New file, New folder, Upload, Import URL, Refresh; creation, upload, and URL import target the active folder. |
| One file | Open, Edit/History where supported, Add to prompt, Copy path, Import to Markdown when eligible, Rename, Move, Delete. |
| One folder | Open or make active, New file, New folder, Upload, Import URL, Add to prompt, Copy path, Set as workspace, Rename, Move, Delete; creation, upload, and URL import target the selected folder and name that destination explicitly. |
| Several eligible files | Add to prompt, Import to Markdown, Move, Delete after the relevant batch contracts exist. |
| Several mixed items | Add to prompt, Move, Delete after the relevant batch contracts exist; Import is disabled with an eligibility explanation. |

Selection and location remain different state. Selecting a checkbox marks an item as an operation operand; activating a folder label navigates or makes it the active folder. New, Upload, and Import URL are destination operations: they target the single selected folder when exactly one folder is selected, otherwise the active folder when nothing is selected, and are disabled for every other selection shape. Move temporarily enters a distinct destination-picking mode and restores the prior selection and browse state on cancel.

### Unified folder selection

Do not ask users to type vault-relative paths in normal Explorer workflows. Paths remain visible and copyable identifiers, but the UI derives them from a selected folder plus, where needed, one local item name.

Every operation that needs a folder uses the same embedded folder-selection mode in the existing Explorer:

1. The operation bar identifies the selected source items and the operation being performed.
2. The tree switches to a folder-focused destination view without inheriting a file-search filter. Files are hidden or inert, folders remain navigable, and the current candidate is highlighted.
3. A breadcrumb shows the candidate folder and provides Vault root and Workspace root shortcuts.
4. Folder search returns folders only and selecting a result reveals it in the hierarchy so the user retains location context.
5. The user may create a folder without leaving destination mode; the new folder becomes the candidate after creation.
6. The confirmation action uses a specific label such as “Move here,” “Upload here,” “Import here,” or “Set as workspace.” Cancel restores the previous browse, search, expansion, scroll, and selection state.

New and Rename request only a basename. The chosen or active parent is displayed separately, and the UI previews the resulting path before confirmation. Move keeps the existing name by default and offers a separate optional name field; changing the destination never requires editing a combined path string.

Upload and Import URL begin with the active folder or one selected folder as their destination and expose a “Change folder” action that enters the same folder-selection mode. Upload & import defaults its Markdown output to that upload folder and exposes a separate “Change output folder” action only when the user wants a different destination. Workspace selection opens directly in the same folder-selection mode.

### Vault search

Keep path/name filtering and content search conceptually distinct even if they share one search field. The Explorer should offer clear Name/Path and Contents modes rather than silently changing what a query means.

Content search defaults to literal, case-insensitive matching across text files, scoped to Active folder, Workspace, or Entire vault. Results are grouped by file and contain vault-relative path, line number, and a bounded escaped line snippet. Activating a result opens that file; selection applies to the file path rather than to an individual matching line. Regex, replace, binary extraction, fuzzy/semantic ranking, and hidden-file search are not part of the initial content-search scope.

Reuse the existing ripgrep-backed core search capability, but refactor it behind a typed bounded search service instead of calling the `file_read` tool or parsing its human-formatted return text from the API. The existing tool operation and the Explorer API should become adapters over the same service so path validation, timeout behavior, hidden-file policy, and result interpretation do not drift.

## Import Surface Ownership

| Surface | Responsibility |
| --- | --- |
| Vault Explorer | Initiate imports from existing vault files, local uploads, and public HTTP/HTTPS URLs; select the output folder; show concise per-request options and submission progress. |
| Dashboard → Import | Observe, filter, cancel, retry, and inspect durable jobs; process the background queue; edit persistent user-facing import defaults; open sources and outputs in the Explorer. |
| System settings | Retain expert/provider/runtime configuration such as OCR endpoint/model, URL timeouts and size limits, and worker concurrency or interval controls unless deliberately promoted into the Dashboard defaults form. |
| Chat and workflows | Continue to submit imports programmatically through the same durable ingestion service and defaults. |

Dashboard must not retain a second competing import composer. “Retry” or “Edit and retry” from a job row should open the Explorer for that job's vault with the appropriate file selection or URL import panel prefilled. The Explorer remains the place where the user confirms or changes the destination.

The existing inbox-scan API can remain for compatibility and automation, but the Dashboard's prominent “Import Inbox” action should be removed or moved into a clearly secondary legacy/automation area. Ordinary interactive users should navigate to files in the Explorer and choose Import to Markdown; those direct imports preserve their source files rather than relying on the inbox's consume-source behavior.

## Recommended Scope

### Phase 1: Selection-aware operation bar

- Add an explicit active-folder state, defaulting to the workspace root when browsing workspace scope and to the vault root otherwise.
- Show the active folder as a compact breadcrumb or location bar with a selectable vault-root segment. Keep the expandable tree, but distinguish the active folder highlight from expansion state and selected items.
- Add visible per-row selection controls and a selected-count summary while preserving direct file opening and folder navigation through row labels. Selected paths are keyed by normalized vault-relative path.
- Replace row-level copy and overflow action menus with the operation bar. Keep common operations visible and use a single responsive bar-level overflow for lower-frequency actions when space is constrained.
- Implement the selection/applicability matrix above. Do not silently reinterpret an unsupported selection; disable the action and expose the reason through visible help text or an accessible description.
- Make folder-name activation set the active folder and expand it when needed; keep the chevron dedicated to expand/collapse. File activation continues to open the viewer.
- Preserve selection when navigating or searching within the same Explorer instance, show the selected count at all times, provide Clear selection, and remove entries that no longer exist after a mutation refresh.
- Permit both files and folders in a batch selection, but reject ambiguous mutation sets containing both a directory and one of its descendants.
- Keep Rename, file open/edit/history, workspace selection, and creation as single-target actions even though they are launched from the shared bar.

### Phase 2: Consistent folder targeting and upload

- Make New and Upload target the single selected folder or, with no selection, the active folder. Their forms and buttons must state the resolved destination visibly before submission.
- Remove free-form destination-path fields from Explorer workflows. Use folder selection for the parent/destination and accept only a basename for create, rename, or an optional move rename.
- Extract a reusable folder-targeting state within the existing Explorer rather than opening a second folder modal. Use it for Move, Upload destination changes, import output destination changes, and workspace selection while allowing each action to supply its own verb and confirmation. It must support the active folder, vault root, workspace root, or a newly created folder, and must not inherit a file-search filter that hides valid destinations.
- Add folder-only search to destination mode. Search results must reveal the selected folder in the hierarchy rather than becoming a disconnected flat destination list.
- Use action-specific labels such as “Upload here,” “Move here,” “Import here,” and “Set as workspace” instead of the generic “Use” where the chosen folder's role could be unclear.
- Preserve the user's scope, active folder, expanded ancestors, and scroll position across refresh and successful create/upload/move operations where the affected paths still exist. Clear only state that has become invalid.
- Keep “Upload” for copying local files into the vault and “Import to Markdown” for ingestion. When composed, label the action “Upload & import” so both outcomes are explicit.

### Phase 3: Explorer-initiated file and URL import

- Extend the upload staging panel to identify supported import sources. The current registered file importers cover PDF and common image formats (`.png`, `.jpg`, `.jpeg`, `.webp`, `.tif`, and `.tiff`); eligibility must come from a server-owned capability response or validation contract rather than a duplicated browser-only list.
- Offer distinct primary actions when the staged files include eligible sources: “Upload” and “Upload & import.” The second action uploads every selected source to the chosen folder and submits the successfully uploaded eligible files as durable ingestion jobs without requiring `AssistantMD/Import` or a chat turn.
- Default the Markdown output destination to the upload folder, display it explicitly, and allow the user to choose a different output folder through the shared folder-targeting interaction. Preserve uploaded source files because direct vault-file imports already use `consume_source: false`.
- Keep the common path simple by applying the persisted Dashboard defaults and showing one concise summary such as “Using defaults: OCR then local text · Markdown · no enrichments.” Do not require users to open or confirm settings when the defaults are acceptable.
- Provide a collapsed “Options for this import” section for one-off overrides. Each control starts at “Use default,” changes only the current submission, and exposes Reset to defaults; the panel must not save global defaults implicitly.
- Limit one-off controls to options relevant to the selected sources: PDF output mode and strategy, OCR image capture, and OCR enrichments when OCR is applicable. For a URL whose media type is not yet known, label these as PDF handling that applies only if the URL resolves to a PDF. Keep destination selection outside the collapsed options because users must always understand where output will go.
- Include a direct “Edit defaults” link that opens Dashboard → Import defaults, but do not add a “save these as defaults” side effect to an import submission.
- When overrides are active, show that fact in the collapsed summary so the user cannot submit custom behavior while the controlling fields are visually hidden.
- Treat upload and import as one composed user operation, not one atomic filesystem transaction. Report the two stages separately: an upload can succeed while its import job fails, and the user must be able to retry Import to Markdown without uploading the source again.
- Submit every import as a durable job and return its identity promptly. “Import now” should request immediate processing and let the Explorer poll or subscribe to job status rather than hold the modal behind one long HTTP request; “Queue” should create the same job without requesting immediate execution.
- Keep import output create-only from the user's perspective. The current storage layer avoids overwrites by appending numeric suffixes; expose that as an explicit “create a numbered copy” collision policy, preview the expected output name when possible, and report the actual output paths. Do not add silent overwrite behavior.
- Enable “Import to Markdown…” in the operation bar for one or more selected supported vault files. It opens a compact confirmation panel with the selected sources, output destination defaulted to their common parent or the active folder, and an Import action that submits the durable job contract.
- Allow several supported existing files to be submitted in one import request. Each source remains an independently observable durable ingestion job, consistent with the current ingestion model.
- Add Import URL to the operation bar when no item or exactly one folder is selected. Its compact panel contains the URL, the explicit output folder, a “Change folder” action, a concise summary of effective defaults, and Import/Queue behavior consistent with file imports.
- Replace separate Explorer-facing file and URL request shapes with one direct-source import API that accepts one or more existing vault-relative paths or HTTP/HTTPS URLs, an explicit destination, supported per-request overrides, and `queue_only`. It should reuse `ContentImportService` source and option validation plus existing task execution rather than scan the inbox or implement another importer.
- Resolve and snapshot the effective user-facing import options when the job is created, including defaults plus explicit overrides, so a queued job does not silently change behavior if Dashboard defaults are edited before processing. Runtime capability and credential availability may still be evaluated when the job executes.
- Preserve existing specialized endpoints while they remain internal compatibility surfaces, but route the new Explorer UI through the unified direct-source contract.

### Phase 4: Refocus Dashboard → Import

- Keep the vault selector, status counts and filters, polling, job table, output links, cancel queued job, load older, refresh, and Process Queue Now controls.
- Add an Open Vault Explorer action for the selected vault. Job source/output links should open the relevant Explorer location; Retry or Edit and retry should open the Explorer's file or URL import panel with the job source, destination, and previous effective user-facing options prefilled as one-off overrides, plus a Reset to current defaults action.
- Remove the primary Import Inbox and Import URL composers from the Dashboard so users do not have two import-initiation models. Retain inbox triggering only as an explicitly secondary automation/compatibility control if an established workflow still requires it.
- Replace temporary “PDF conversion options” with a persistent “Import defaults” form. Saving must update actual settings and affect Explorer, chat, workflow, and API imports that do not provide an override; changing form controls without saving must not silently alter a one-off request.
- Keep the defaults form focused on user-facing choices such as PDF strategy order, default PDF output mode, OCR image capture, routine OCR enrichment defaults, and immediate-versus-queued preference if that preference is introduced. Keep provider endpoints, models, transport limits, and worker tuning in System settings.
- Treat `ingestion_output_path_pattern` as the fallback for imports without an explicit destination. Explorer imports normally provide an explicit selected folder and should not unexpectedly redirect to the global output pattern.
- Remove the separate “latest import result” panel once equivalent status is available in the durable jobs list; newly submitted Explorer imports should appear at the top and poll while active.

### Phase 5: Batch mutations

- Enable Add to prompt for any non-empty selection and Import to Markdown when every selected item is an eligible file; these can ship before filesystem batch mutations because they do not need an atomic vault mutation primitive.
- Add Move selected after implementing the batch mutation contract below.
- Add bulk Delete only with a dedicated reviewed contract: one confirmation showing the exact item count and paths, server preflight of every target, preservation of the existing non-empty-directory restriction, and an all-or-nothing outcome. If that contract is not implemented in this effort, leave bulk Delete out rather than performing a client-side partial delete loop.

### Phase 6: Focused explorer polish and optional drag-and-drop

- Make search mode explicit, show the searched scope and root, and offer a one-action return to the active folder. Moving or uploading should temporarily show an unfiltered folder-only destination view and restore the prior browse/search state on cancel.
- Add keyboard behavior for tree navigation, selection controls, the operation bar, Escape/cancel, overflow menus, and focus restoration after mutations. Preserve visible focus and accurate `aria-expanded`, `aria-selected`, and checkbox labels.
- Improve operation feedback with a stable completion/error summary, especially for multi-file uploads where some create-only destinations collide. Keep successful files and failed files distinguishable and make retry apply only to failures.
- Ensure revealed or newly affected paths remain reachable when a directory has more than one API page of children; direct-path reveal must not silently depend on the target being in the first 100 entries.
- Evaluate external-file drag-and-drop after the selection-aware bar is usable. If retained, dropping on a folder row targets that folder, dropping on the active-folder area targets the active folder, the destination receives an unambiguous highlight, and the normal upload staging/confirmation panel still appears. Do not add drag-to-move vault items in this effort.

### Phase 7: Vault content search after the Explorer upgrade is stable

- Begin this phase only after the operation bar, folder targeting, import flows, Dashboard handoff, and included batch actions are working and have passed their focused validation. Search must not destabilize or delay hardening of those workflows.
- Extract a typed search result with `path`, `line_number`, and bounded `snippet` fields from the current `search_vault_files_operation` implementation, and keep the tool's existing prose/metadata shape as an adapter.
- Use ripgrep as a backend implementation detail because it is already installed in supported host/container environments. Invoke it without a shell, constrain it to resolved vault roots, preserve the configured timeout, do not follow escaping symlinks, and return a clear unavailable error if the binary is missing.
- Add a read-only Explorer search endpoint with query, scope path, scope kind, and bounded limit. Run the blocking subprocess work off the async request loop and support request cancellation or stale-result suppression in the browser.
- Make Explorer content queries literal and case-insensitive by default. Use structured ripgrep output rather than colon-delimited parsing so valid filenames and snippets cannot corrupt result fields.
- Enforce a global match cap, maximum snippet length, and bounded response size. Report truncation explicitly and ask the user to narrow the query; do not allow a broad search to buffer unbounded ripgrep output in memory.
- Present Name/Path and Contents as explicit search modes. Preserve the existing name/path behavior for navigation and use Active folder, Workspace, and Entire vault as understandable content-search scopes.
- Group matches by file, deduplicate file-level selection, and open the file from any match. Opening directly at the matching line is desirable but may be deferred if it would couple the first search slice to a file-viewer navigation redesign.

## Explicit Non-Goals

- Replacing Obsidian or a desktop file manager with tabs, backlinks, graph views, metadata editing, plugins, or a full IDE.
- Drag-to-move or reorder vault items, clipboard cut/copy/paste, recursive folder upload, archive operations, downloads, file-type viewers beyond current safe text/Markdown behavior, or user-defined sort modes. External-file drag-and-drop is optional polish rather than a prerequisite for a complete upload workflow.
- Overwriting upload or move destinations by default.
- Recursive deletion of non-empty directories.
- Hiding the distinction between upload completion and ingestion completion. “Upload & import” is one user-initiated workflow with two visible durable stages, not a promise that conversion is synchronous or cannot fail after upload.

## Contract-Sensitive Areas

- `static/js/vault-path-picker.js`: modal lifecycle, tree rendering, expansion/reveal, pagination, folder-only loading, search transitions, keyboard behavior, and refresh restoration.
- `static/js/vault-explorer-actions.js`: operation-bar rendering and dispatch, folder-targeting reuse, upload/create defaults, upload/import staging, existing-file import, batch actions, validation, progress, failure summaries, and focus restoration.
- `static/js/file-references.js`: Explorer entry-state construction, prompt insertion for several references, return from file view, and interaction locks.
- `static/app.css` and compiled `static/output.css`: active-folder, selected-row, destination-mode, operation-bar, breadcrumb, responsive, and focus styles. Run `npm run build:css` after stylesheet changes.
- `api/models.py`, `api/endpoints.py`, and `api/services/vault_files.py`: any batch mutation request/response, preflight results, normalized paths, and activity attribution.
- The directory-listing or file-reference API may need a folder-only search contract that returns enough ancestor information for deterministic reveal; avoid downloading or walking the entire vault in the browser.
- `core/vault_state/file_operations.py`, `api/models.py`, `api/endpoints.py`, and `api/services/vault_files.py`: typed vault-content search, the existing `file_read` adapter, bounded structured API results, request-loop isolation, and error mapping.
- `api/import_models.py`, `api/endpoints.py`, and `api/services/ingestion.py`: direct vault-file import request/response, eligibility validation, destination/options translation, queue/immediate execution, and durable job projection.
- `static/index.html`, `static/js/configuration/imports.js`, `static/js/configuration/import-jobs.js`, `static/js/configuration.js`, and `static/js/configuration/runtime.js`: remove competing Dashboard composers, persist defaults, keep job operations, launch Explorer import flows, and simplify transient import state.
- `static/app.js`: expose an Explorer-launch callback to Dashboard job/defaults modules and support opening the correct vault, source selection, active location, or URL-import panel.
- `core/ingestion/import_service.py` and the importer registry: reuse as the source of path validation, supported source types, destination translation, job creation, and source-preservation behavior.
- `core/settings/settings.template.yaml` and settings accessors: distinguish persistent import defaults from per-request overrides and add only defaults that the Dashboard genuinely owns. This effort changes persisted system settings if new defaults are introduced and must preserve safe template repair behavior.
- `docs/use/importing-content.md`, `docs/use/getting-the-most.md`, and relevant setup/settings documentation: describe Explorer initiation, Dashboard job/default ownership, explicit destinations, source preservation, and any retained inbox automation according to the final current contract.
- `core/vault_state/file_operations.py` and `core/vault_state/file_mutations.py`: only if batch move/delete requires a shared lock, compensation, or a stronger all-or-nothing primitive than the API service can safely compose.
- Persisted runtime state is not changed. Selection, active folder, expansion, and search state remain transient to the open Explorer. Vault files and durable mutation/activity records retain their existing ownership and retention rules.

## Frontend Module Boundaries

- Keep `static/js/vault-path-picker.js` as the coordinator for modal lifecycle, tree loading, row rendering, navigation, pagination, and delegation. It must not absorb operation policy, import orchestration, or content-search parsing.
- Add `static/js/vault-explorer-state.js` as a DOM-light controller for active folder, normalized selected items, selection validity, and the operation applicability matrix. Its public methods and emitted snapshots form the testable state contract.
- Add `static/js/vault-explorer-toolbar.js` for operation-bar rendering, responsive grouping, accessible disabled reasons, and dispatch of user intent. It consumes state snapshots and callbacks; it does not perform fetches or own mutations.
- Add `static/js/vault-explorer-controller.js` as the small composition boundary between selection state, toolbar intent, tree callbacks, and the existing action module. It owns no network or tree-loading behavior and prevents cross-module UI lifecycle logic from accumulating in the picker.
- Keep `static/js/vault-explorer-actions.js` focused on create, rename, move, delete, and upload action panels. Use `static/js/vault-explorer-destination.js` for normalized destination-mode state and validation rather than growing the action module further.
- Add `static/js/vault-explorer-imports.js` in Phase 3 for upload/import composition, direct file/URL import requests, per-import overrides, and durable job progress. It consumes destination selection through callbacks and does not own tree state.
- Add `static/js/vault-explorer-search.js` only in the final search phase for content-query state, request cancellation, result grouping, and safe result rendering. Name/path tree filtering remains coordinated by the picker.
- Keep `static/js/file-references.js` as the integration boundary for opening the Explorer or file viewer, inserting prompt references, interaction locks, and app-level callbacks. Do not move tree or action implementation into it.
- Register every new browser module explicitly in `static/index.html` before its consumer and add a focused module-load/composition test. Prefer small controller factories over new global mutable state.
- On the backend, place typed content search in a dedicated `core/vault_state/search.py` service and a thin `api/services/vault_search.py` wrapper when the final search phase begins. Keep import orchestration in `api/services/ingestion.py` and batch vault mutations in their owning vault-state service rather than expanding `api/endpoints.py` beyond request routing.

## Batch Mutation Contract

- Do not implement multi-item move or delete as independent browser calls hidden behind one button. That would allow partial completion, create one activity per item, and make failures and rollback surprising.
- Introduce a batch request only for the operations included in the delivered UI. The server must normalize all sources and destinations, reject duplicates and ancestor/descendant overlap, validate source existence and target collisions, reject moves into a selected source tree, and finish preflight before applying mutations.
- Record one Explorer activity for the user's batch command with path-level mutations underneath it. Return deterministic per-item results for display, but treat a failed preflight as no-op for the full batch.
- The implementation must explicitly decide and validate compensation behavior for failures after preflight. If atomic compensation cannot be guaranteed for a proposed operation, exclude that operation from the first multi-select release rather than claiming all-or-nothing behavior.

## Validation-First Targets

- Extend `validation/scenarios/integration/core/vault_file_reference_api.py` or add a focused `vault_explorer_batch_mutation.py` scenario before backend implementation. Assert normalized multi-source move behavior, one durable Explorer activity, path-level mutation records, collision rejection without filesystem changes, ancestor/descendant rejection, vault-boundary enforcement, and compensation behavior for an injected mid-operation failure.
- Extend `validation/scenarios/integration/core/vault_explorer_upload.py` only if the upload API contract changes. Preserve create-only bytes, size limits, traversal/symlink rejection, partial-write cleanup, and upload activity attribution.
- Add a deterministic integration scenario for direct vault-file import. Assert supported-source validation, explicit output destination, source preservation, one durable job per source, queue-only and immediate modes, mixed invalid-source rejection, and reuse of existing ingestion output and failure contracts.
- Extend that scenario for direct URL import with an explicit destination, URL validation, queue-only and immediate behavior, output placement, and parity between file and URL request options. External retrieval itself should use a deterministic local fixture rather than a live service.
- Assert prompt durable-job acknowledgement for immediate submissions, observable queued/processing/terminal transitions, create-only numbered collision behavior, and exact reported output paths.
- Add focused settings/API coverage proving that Dashboard-edited defaults persist, apply only when a direct request omits an override, and do not override an explicit Explorer destination or per-request option. Also prove that queued jobs retain the effective user-facing options captured at submission after defaults subsequently change.
- Extend `validation/scenarios/integration/core/file_read_search_text_files.py` or add a focused vault-search API scenario covering literal metacharacters, case-insensitive matching, active/workspace/vault scope, hidden and binary files, filenames containing colons, global truncation, snippet bounds, timeout/error mapping, and parity between API and tool adapters.
- Expand the focused frontend harness around `validation/test_vault_path_picker_modules.py` to exercise the selection/applicability matrix, active-folder targeting, operation-bar dispatch, upload/import staging, destination-mode transitions, folder-only search and reveal, state restoration, and keyboard behavior without asserting incidental HTML formatting. Add drag/drop target tests only if that optional polish is implemented.
- Add frontend coverage for switching Name/Path and Contents modes, stale request suppression, grouped results, file-level selection deduplication, scope changes, truncation feedback, safe snippet rendering, and opening a matched file.
- Add focused Dashboard frontend coverage showing that job observation and queue controls remain available, import composers are absent, defaults use saved settings rather than transient request state, and job retry delegates to the Explorer with the correct vault and source.
- Add a manual responsive smoke pass for desktop and narrow/mobile widths covering: operation discoverability with no selection and each selection shape; open at workspace and vault root; navigate and create in a nested folder by entering only a name; select deep destinations using navigation, folder search, root shortcuts, and create-folder-in-place; upload to the active or one selected folder; upload only; upload and import eligible and mixed file sets; retry import after upload success; import one or several existing files; cancel destination selection; move one item with and without renaming; select and move several items; search then return to browsing; open and return from a file; interaction locking; collisions; focus restoration; and optional external-file drop behavior if implemented.
- During development, run the focused frontend tests plus the directly affected deterministic scenario. Once behavior is stable, run `npm run build:css` and `python validation/run_validation.py run integration/core` during hardening or merge preparation.

## Recommended Delivery Slices

1. **Selection state contract:** add failing module tests for normalized selection, active-folder state, selection persistence, invalid ancestor/descendant combinations, and operation applicability; implement `vault-explorer-state.js` and register it without changing visible behavior.
2. **Operation bar:** add focused DOM/controller tests; implement `vault-explorer-toolbar.js`, visible row selection, selected-count/Clear behavior, responsive action availability, and single-item dispatch; remove row mutation menus only after parity is covered.
3. **Current folder and destination mode:** add tests for breadcrumb/location state, basename-only create/rename, selected/active-folder targeting, cancel restoration, folder-only search/reveal, and invalid move destinations; implement `vault-explorer-destination.js` and migrate Move, New, Upload, and workspace selection.
4. **Upload staging:** test multi-file destination display, collisions/failures, retry-only-failures, and interaction locks; implement the revised Upload and optional Upload & import staging without changing ingestion yet.
5. **Direct-source import contract:** add deterministic API scenarios first; implement the unified file/URL job-submission service, effective-option snapshot, explicit destination, prompt durable acknowledgement, status polling, numbered collision reporting, and source preservation.
6. **Explorer import UI:** test effective-default summaries, collapsed one-off overrides, selected-file import, Upload & import, Import URL, immediate/queue behavior, retry, and output links; implement `vault-explorer-imports.js` against the validated API.
7. **Dashboard ownership:** add frontend/settings tests; refocus Dashboard → Import on job observability, queue control, Explorer handoff, and persisted defaults; remove the primary inbox/URL composers and transient latest-result state.
8. **Batch Move:** define failing atomicity/activity scenarios; implement one-activity batch Move and connect it to multi-selection. Decide and separately validate batch Delete or leave it out.
9. **Explorer hardening:** make reveal pagination-safe, finish responsive/keyboard/focus behavior and operation summaries, evaluate optional external-file drag/drop, run focused Explorer/import scenarios, and stabilize the feature set before search begins.
10. **Vault content search last:** after slices 1–9 are working and validated, extract the typed bounded ripgrep service, add its thin API, implement explicit Name/Path and Contents modes in `vault-explorer-search.js`, and run dedicated backend/frontend search coverage.
11. **Final hardening:** build CSS, align current-contract documentation, run the production Python quality gate if Python changed, and run `python validation/run_validation.py run integration/core` once against the stable branch.

## Decisions to Confirm Before Feature Development

- Whether multi-select must include Delete in this release or whether Move, Add to prompt, and eligible-file Import to Markdown are sufficient for the first bounded version.
- Whether the active folder should change on a single folder-name click or only through an explicit “Open/Use folder” affordance; the recommendation is single click on the name, with the chevron reserved for expansion.
- Whether selection should persist across search queries in one modal session; the recommendation is yes, with a visible selected count and a Clear action.
- Whether “Upload & import” should process jobs immediately by default or queue them by default; the recommendation is immediate for small interactive batches and queue-only above a clearly stated threshold.
- Whether external-file drag-and-drop adds enough value after the operation bar is complete; the recommendation is to validate the button-driven workflow first and treat drag-and-drop as optional polish.
- Which current PDF/OCR controls deserve persisted defaults. The recommendation is to persist routine strategy/output choices and OCR image capture, while keeping rarely used OCR enrichments available as collapsed per-import overrides unless repeated use justifies global defaults.
- Whether the inbox trigger has known interactive users that require it to remain visible. The recommendation is to preserve its API contract but remove it from the primary Dashboard workflow.
- Whether Explorer imports may be submitted while a chat response or deferred review holds the current Explorer mutation lock. The recommendation is to keep all vault-writing Explorer actions, including import submission, disabled until the interactive mutation surface is idle so the shared surface retains one understandable safety rule.
- Whether a selected existing file should default its output to the source parent or the active folder when those differ, such as selection from search results. The recommendation is the source parent for a single file or shared source parent for a homogeneous selection, with the destination always shown before submission; otherwise use the active folder.
- Whether the existing numbered-copy import collision behavior remains the product policy. The recommendation is yes for this effort, with explicit UI messaging and no overwrite option.
- Vault content search is included but sequenced as the final feature slice, after the rest of the Explorer and import upgrade is working and validated.

## Next Phase

Feature Development begins with delivery slice 1 using frontend contract tests to establish selection, operation-applicability, active-folder, and destination invariants before modifying visible behavior.
