# 0024 - Use One Chat-Native Vault Explorer Surface

## Status

Accepted.

Complements
[0016 - Treat Vault Files As Canonical User Data](0016-vault-files-canonical-user-data.md).

## Context

AssistantMD is chat-first and markdown-first. Users may keep Obsidian or another
editor beside it for advanced knowledge-management workflows, but routine vault
navigation and editing should not require leaving a chat session or asking the
model to perform operations that need no model reasoning.

File references, workspace selection, directory navigation, direct file editing,
and retained activity snapshots all need similar vault browsing behavior.
Implementing each workflow as a separate tree, picker, or editor would duplicate
path policy and interaction state, cause the surfaces to drift, and gradually
turn the chat UI into a second full-time vault application.

## Decision

Use one shared, on-demand Vault Explorer as AssistantMD's direct vault
interaction surface.

The same Explorer is opened from:

- the chat toolbar;
- workspace selection;
- resolved file and directory references in rendered chat messages;
- file and snapshot links in vault activity views.

An entry point may reveal a specific path or open a file directly, but the user
can return to the tree and navigate the active vault without reopening another
picker. The shared surface owns folder expansion, path search, copy/add-reference
actions, workspace selection, text preview/editing, path mutations, exact-path
revision history, and revision restore.

Vault files remain canonical. The Explorer does not create a parallel document
store, persistent editor workspace, or independent mutation path. Direct UI
writes use the same validated operations in `core.vault_state.file_operations`
and tracked mutations in `core.vault_state.file_mutations` as agent and authored
tool writes.

The Explorer remains navigable while a chat turn or deferred review is active,
but mutation, edit, workspace, prompt-insertion, restore, and rollback actions
are read-only until the interactive mutation surface is idle. Advanced editing
and knowledge-management features remain the responsibility of external vault
editors unless a concrete AssistantMD workflow requires them.

## Rationale

A shared modal preserves the minimal chat workspace while removing unnecessary
model calls and app switching for routine file work. Reusing one navigation and
editing surface also centralizes path resolution, stale-file handling, mutation
locking, and responsive behavior instead of allowing several nearly identical
pickers to evolve independently.

Keeping writes behind the normal vault services means UI convenience does not
create a second correctness or audit contract. Keeping the filesystem canonical
preserves portability and lets users continue using any external editor.

## Consequences

- New chat-native vault navigation should extend the shared Explorer rather than
  add another file tree or editor modal.
- Entry points may choose an initial path or mode, but navigation and file
  behavior stay consistent after the Explorer opens.
- Direct Explorer mutations are first-class attributed vault activity and follow
  normal snapshot, revision, refresh, and conflict rules.
- Files that are visible in the tree may still be ineligible for inline editing;
  text validation belongs at the file API and operation boundary.
- The Explorer is intentionally sufficient for routine mobile and in-chat work,
  not a replacement for every feature of Obsidian or another vault editor.

## Evidence

- Current system map: `docs/development/architecture.md`
- Implementation: `static/js/file-references.js`,
  `static/js/vault-path-picker.js`, `static/js/workspace-picker.js`,
  `static/js/vault-activity.js`, `api/services.py`
- Validation: `validation/scenarios/integration/core/vault_file_reference_api.py`
- Implementation plan: `inline-editor-implementation-plan.md`
