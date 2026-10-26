# UI Favorites Implementation Plan

## Status and scope

Implementation and targeted validation complete on branch `dev/UI-favorites`; chat resume behavior, per-vault scope, file and folder favorites, move/delete lifecycle, and bookmark controls are confirmed. Implement favorite chat sessions and favorite vault files. Workspace `prompts.md` quick prompts remain a separate specification effort and are outside this implementation.

## Observed implementation

- Chat sessions are canonical in `system/chat_sessions.db`, owned by `core/chat/chat_store.py`. Sessions already carry structured metadata, and access goes through `core/chat/session_access.py`. `api/services/chat_sessions.py` projects session information for lists, searches, and forks.
- `static/js/session-controls.js` renders the session browser and its row actions. Name filtering uses loaded sessions; content search returns ranked server results. `static/js/chat-sessions.js` coordinates loading and guards against stale vault/session responses.
- `static/js/chat-start-panel.js` renders the new-chat panel through `static/js/chat-rendering.js`. Favorite cards must refresh when session data loads or favorites change and use the existing session-opening flow.
- One shared Vault Explorer owns tree browsing and selection through `static/js/vault-path-picker.js` and `static/js/vault-explorer-{state,toolbar,controller,actions}.js`. `static/js/file-references.js` owns the individual file modal.
- Filename browsing/search uses `api/services/vault_files.py`; content search uses `api/services/vault_search.py`, `core/vault_state/search.py`, and `static/js/vault-explorer-search.js`. Filtering only the displayed results would miss favorites beyond existing result limits.
- File operations converge on `core/vault_state/file_mutations.py`. Stable vault identities exist in `core/vault_state/identity.py`; the vault manifest is rebuildable and must not own the lifecycle of durable preferences.
- Relevant architecture decisions reviewed: ADR 0003 (canonical chat SQLite), ADR 0015 (subsystem database ownership), and ADR 0024 (shared chat-native Vault Explorer).

## Proposed user-visible contract

### Chat sessions

- Add an accessible Lucide bookmark toggle to session rows, with a visible selected state and clear favorite/unfavorite labels.
- Show favorite sessions before other sessions in the browser. Proposed default: order each group by latest chat activity, and use deterministic ties. Favoriting must not change the chat activity timestamp or history revision.
- Render all favorite sessions from the selected vault as cards on the new-chat page, showing the session title or existing fallback and useful activity/workspace metadata.
- Proposed card action: resume that existing session through the ordinary session loader, respecting active-response switching locks.
- Preserve content-search relevance within favorite/nonfavorite groups unless the user chooses a different search behavior. Do not change agent session-discovery ranking merely to change browser display order.
- Existing sessions default to unfavorited. New chats and forks default to unfavorited; copying conversation metadata must not accidentally copy the source favorite flag.

### Vault files

- Add a favorite toggle to the individual file modal and explicit favorite/unfavorite actions for selected files in the shared tree, including multiple selected files.
- Confirmed batch behavior: one bookmark toggle for selected files and folders. If every selected item is favorited, remove all; otherwise favorite all. Updates are idempotent and validate all selected paths before changing preferences. Favoriting a folder bookmarks that folder without implicitly favoriting descendants.
- Add an accessible favorites-only toggle near the Explorer search controls, with an unmistakable active state. Proposed filtered view: a flat list of favorite paths within the current browsing/search scope; no-query browsing, filename search, and content search all honor the toggle.
- Apply favorites eligibility before pagination and result limits. An empty favorite set returns no results, without falling back to an unrestricted search. Normal browsing and search retain their current behavior when the toggle is off.
- Keep favorite state synchronized between the file modal, tree selection actions, and filtered views. Prevent stale responses from another vault or earlier request from updating the active view.
- Proposed lifecycle: follow successful in-app renames/moves, including files beneath moved directories; remove favorites after deletion. Account for failed operations, compensated batch moves, and revision restores before promising atomic behavior. External renames cannot reliably preserve identity and should leave a missing old path excluded from visible results.
- Favorites do not alter file bytes, frontmatter, chat content, or prompts. They persist across browser reloads and application restarts, scoped to the authorized principal and vault rather than browser storage alone.

## Confirmed decisions and defaults

- Chat cards resume the existing conversation using the same action as the session list. Favorites are per vault and sorted before other sessions, preserving latest-activity order within each group.
- Use the Lucide bookmark icon in session row actions and file/Explorer button rows. Active bookmarks are visibly filled and expose pressed state. One selection toggle adds all unless all are already favorites, in which case it removes all.
- Both files and folders can be favorited. Folder favorites bookmark the folder itself; entering a bookmarked folder uses ordinary browsing. Favorites-only filename and content searches match explicitly bookmarked items.
- Favorites follow in-app moves/renames, including descendants of moved folders, and are removed on deletion. External missing paths are excluded from results.
- The favorites view is a flat list with full paths within the current scope. Reset the filter on a new Explorer visit and preserve it while navigating between tree and file within that visit.

## Persistence and API design

- Add typed favorite state to chat API projections and an explicit idempotent mutation accepting the desired boolean. Prefer the existing session metadata merge boundary if it can preserve unrelated metadata without touching activity/history; otherwise use a dedicated schema field with an additive migration.
- Keep authorization checks on every chat favorite mutation and file favorites read/write. Chat access must conceal inaccessible sessions using the established policy; favorite state must not bypass vault/path checks.
- File preferences use an independent `vault_favorites` table in the existing vault-state database, keyed by principal, stable vault identity, and normalized relative path. There are no manifest foreign keys or expiration fields; refresh and safety-artifact cleanup do not affect bookmarks. The current schema initializer creates this additive table for existing and new installations, following ADR 0015.
- Expose file favorite state in relevant file/list responses and a bounded batch update endpoint. Extend Explorer list/search requests with an explicit favorites-only parameter. Keep UI filtering requirements out of agent tools unless the tool contract actually needs them.
- Integrate path lifecycle handling at shared mutation boundaries where possible so UI, tool, and workflow mutations behave consistently. Define locking, failure recovery, and rollback semantics before coding that slice.
- Runtime data is persistent: use isolated temporary data/system roots for validation. Do not experiment against repository runtime state or populated secrets.

## Backend milestone

Implemented chat favorite state and APIs, favorite-first UI session listing, durable file/folder bookmarks, scoped filename/content filtering before limits, and shared move/delete/restore lifecycle hooks. Committed as `f8d7780`. Four targeted deterministic scenarios passed: `ui_favorites`, `chat_session_fork_lineage`, `vault_file_reference_api`, and `vault_explorer_batch_move`. The complete Ruff, Black, and MyPy gates passed before the commit.

## Chat interface milestone

Implemented Lucide bookmark toggles in session rows and favorite conversation cards on the new-chat panel. Cards use the same session-selection method as browser rows. Session changes refresh the cards; late bookmark responses cannot modify another vault. Desktop/mobile browser tests and nearby controller tests pass (22 checks). The bookmark icon and styling are shared with the Explorer interface.

## Explorer interface milestone

Implemented bookmark toggles for individual files and selected files/folders, with mixed selections adding every selected item and fully bookmarked selections removing all. A shared API helper synchronizes the file modal and Explorer. The Explorer header filter applies to flat favorite listings, filename searches, and content searches; opening a bookmarked folder enters ordinary browsing. Pending mutations keep the bookmark control visible and disabled, and failures preserve bookmark state. Favorite revision guards prevent stale reads from overwriting completed edits.

Desktop/mobile browser workflows, pending/failure handling, and nearby Explorer controller checks passed (22 checks across the focused files). The CSS build passed. Mobile previews were visually inspected with no horizontal overflow. The complete Ruff and Black checks passed; the production Python MyPy gate passed at the backend milestone and production Python has not changed since. Session UI is committed as `dba4896`; Explorer UI is the final implementation commit. Remaining work is user review, deferred documentation, the separately scoped quick-prompts specification, and any later merge validation explicitly requested by the user.

## Validation and documentation constraints

The full `integration/core` run was started and then explicitly stopped at the user's request (exit 130); it is incomplete and must not be reported as passed. Continue with targeted checks only. The user has deferred documentation discussion and changes until later; no files under `docs/`, `README.md`, or `RELEASE_NOTES.md` have been edited. Leave the final documentation review step pending.

## Validation-first implementation sequence

1. Product decisions recorded and Feature Development guide read. Begin with deterministic favorites scenario assertions.
2. Add deterministic scenario assertions for chat favorite persistence, explicit add/remove, vault/principal isolation, unchanged activity/history, deletion, and forks starting unfavorited. Extend `chat_session_persistence_contract` where appropriate or add a focused favorites scenario when keeping its lifecycle separate is clearer.
3. Implement chat persistence/API, browser ordering/toggle, and new-chat cards as the first complete slice. Extend focused controller smoke checks for ordering, card session selection, refresh, in-flight locks, and stale responses.
4. Add file favorite scenario assertions for durable batch state, normal versus filtered listings, filename/content search before result limits, empty sets, invalid paths, vault/principal isolation, and the agreed move/delete/restore lifecycle. Extend `vault_file_reference_api` or add a focused file favorites integration scenario.
5. Implement the file preferences store/API and mutation integration, then connect modal toggles, batch actions, and the favorites-only filter through the shared Explorer. Extend existing Explorer state/toolbar/search/controller smoke tests for meaningful behavior.
6. Use ordinary structured mutation logging for successful favorite changes: proposed events `chat_session_favorite_updated` with `session_id`, `vault_name`, `favorite`; and `vault_file_favorites_updated` with `vault_name`, `favorite`, `requested_count`, `changed_count`. Log after successful persistence, avoid duplicate no-op noise, and keep reads uninstrumented unless a decision boundary warrants an event. Validate persistence/API artifacts directly rather than adding test-only events.
7. Run directly relevant checks and individual deterministic scenarios during development. Build CSS with `npm run build:css` if styles change, run the complete Production Python Quality Gate for production Python edits, and run `python validation/run_validation.py run integration/core` once the branch is stable during hardening or merge preparation.
8. Review desktop/mobile rendering, keyboard access, empty states, long titles/paths, favorite removal while filtered, and vault switching. Review the final diff and persistent-data boundaries.
9. Documentation review: identify relevant current-contract updates, likely `docs/use/getting-the-most.md`, `docs/development/architecture.md`, and possibly ADR 0024 if an architectural decision changes. Present exact proposed text or diffs in chat, obtain explicit user approval, then apply only approved documentation changes. Unapproved documentation remains pending; no protected documentation was changed during investigation.
