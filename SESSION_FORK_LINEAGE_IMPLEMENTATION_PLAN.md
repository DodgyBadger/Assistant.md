# Canonical Session Fork Lineage Implementation Plan

Status: implementation and deterministic validation complete; awaiting manual UI verification.

## Objective

Make a fork an independent, faithful copy of one chat session through the selected assistant message. The child must contain the exact canonical message prefix, applicable recovery-card or session-map checkpoints, structured tool history, and explicit lineage needed to explain where the branch came from. From the selected message onward, parent and child diverge independently.

This effort fixes fork persistence and context-strategy inheritance only. Cross-session search deduplication, conversation-family ranking, vault-to-session relationship interpretation, and copy-on-write storage remain deferred memory-layer work.

## Current Defect

`ChatStore.fork_session()` currently copies the source session's effective replay rather than its canonical raw prefix. A compacted recovery card or session map is flattened into an ordinary child message, checkpoint rows are omitted, compaction metadata is removed, and the API hard-codes the new session as `unassigned`. The child may therefore look compacted while losing strategy pinning, map observability, canonical pre-compaction evidence, and V1-to-V2 upgrade eligibility.

The existing `fork_sequence_index` contract also becomes ambiguous after Compaction v1 because recovery-card replacement history is assigned synthetic indexes rather than the retained messages' canonical source indexes. Correct forking requires resolving the selected visible assistant message back to its canonical source message before copying anything.

## User Contract

- Forking through an assistant message copies every canonical parent message through that exact message and no later message.
- The parent remains unchanged, and later work in either session cannot mutate the other.
- Structured tool calls and results belonging to the copied prefix remain inspectable and protocol-complete.
- A checkpoint is inherited only when everything its author observed is at or before the selected canonical fork point. A future-informed checkpoint must never leak into an earlier branch merely because its replacement card is displayed above that branch point.
- Inherited checkpoints receive child-owned checkpoint IDs while retaining explicit origin metadata. Their canonical message references continue to resolve because the referenced raw prefix is copied without renumbering.
- A child whose latest inherited checkpoint is a recovery card remains Compaction v1 and exposes the V2 upgrade action when V2 is configured. A child whose latest inherited checkpoint is a session map remains Compaction v2 and exposes the inherited map revisions and canonical transcript in the existing modal.
- A fork before the first eligible checkpoint is truthfully `unassigned`, matching the parent state at that historical point.
- Forking performs no model inference and spends no model quota.
- If a legacy checkpoint's visible retained message cannot be mapped unambiguously to canonical history, that replacement message exposes no fork action. The corresponding canonical transcript messages remain forkable by their exact raw indexes.

## Persistence Model

### Canonical message identity

The child copies parent raw `chat_messages` rows through the resolved canonical sequence index and preserves those sequence indexes. Session ID remains part of row identity, so parent and child are independently mutable even though their inherited sequence coordinates match.

Checkpoint replacement messages need an aligned nullable origin map: generated recovery cards and session-map context messages have no canonical message origin, while retained provider messages record their source `sequence_index`. Add a nullable checkpoint column such as `replacement_source_sequence_indexes_json` through the normal chat-database migration path, expose it on `StoredContextCheckpoint`, and validate that its length matches replacement history when present.

New recovery-card checkpoints must persist exact origin indexes for retained replacement messages. Session-map checkpoints currently replace context with one generated map message, so their aligned origin entry is null. Effective-history projection continues using display-local `sequence_index` values where required, but `fork_sequence_index` for a retained message must be its canonical origin. Generated checkpoint cards and map notices do not expose a fork action.

### Checkpoint eligibility

Every checkpoint needs a resolved observation boundary: the newest canonical message available to its author. Compaction v2 already records `map_observed_through_sequence_index`. For Compaction v1, the existing checkpoint `last_message_sequence_index` is the author observation boundary because the recovery-card author receives the complete effective snapshot, including the retained tail.

When forking, copy checkpoints in creation order only while their observation boundary is less than or equal to the selected canonical fork point. This rule applies independently of the checkpoint's rendered position in effective history. Historical session-map revisions remain available when safe; later revisions are excluded.

Each copied checkpoint receives a new unique checkpoint ID and child session identity. Preserve its creation time, kind, source, replacement history, source-linked map payload, and authoring audit fields. Add bounded metadata identifying the parent session and source checkpoint ID. Rebuild any session-level latest-checkpoint metadata with child checkpoint IDs rather than retaining stale parent pointers.

### Session lineage

Extend the existing `fork` metadata object rather than adding a parallel subsystem. Preserve its current `source_session_id` and `through_sequence_index` fields for compatibility, define them as the immediate parent and canonical parent fork point going forward, and add `root_session_id`, `child_owned_from_sequence_index`, creation time, and the inherited checkpoint count. Nested forks retain the original root while naming their immediate parent.

These fields are sufficient for future conversation-family search without defining ranking or deduplication behavior in this branch. They are provenance, not a cross-session memory index.

## Legacy Compatibility

Existing checkpoints have no replacement-origin column. A deterministic compatibility resolver aligns retained replacement messages against canonical raw messages in order, using provider-native persisted message equality and the checkpoint observation boundary. Recovery-card and session-map generated context messages remain originless. Ambiguous or missing alignment does not expose a fork action on the replacement message and never guesses based only on prose. Canonical transcript inspection remains independently forkable because each displayed archival message already has an exact raw sequence index.

Do not automatically rewrite already-created flattened forks. Their canonical parent prefix has already been discarded from the child, and some may have substantial divergent work. Record this limitation and assess a separate explicit repair operation only after the forward fork contract is stable. New forks from legacy parent sessions must use the compatibility resolver and receive the corrected canonical/checkpoint representation.

## Module Boundaries

- `core/chat/chat_store.py` owns atomic raw-message, tool-event, checkpoint, and lineage copying plus low-level replacement-origin persistence.
- `core/chat/schema.py` owns the additive checkpoint-origin migration.
- `core/chat/compaction.py` supplies origin indexes when writing recovery-card replacement history.
- `core/memory/session_map/checkpoints.py` supplies the null origin for its generated map-context replacement and remains authoritative for map payload validation.
- `api/services/chat_sessions.py` validates the requested branch point against the projected canonical fork index, delegates persistence to `ChatStore`, and derives the returned strategy and map flags from the completed child rather than hard-coding them.
- `api/models.py` changes only if the existing fork response cannot express the clarified canonical branch point or lineage result without ambiguity.
- Existing chat rendering remains the UI surface. No new fork settings, model configuration, background task, or modal is required.

Do not add a second fork service or a memory-specific fork implementation. The chat store remains the single owner of canonical session branching, while the API stays a thin authority-checked adapter.

## Atomicity and Safety Invariants

- Session creation, raw-message copying, tool-event copying, checkpoint copying, lineage metadata, and initial history revision are one SQLite transaction.
- Any mapping, validation, uniqueness, or persistence failure leaves no partial child session.
- Parent rows are read-only throughout the operation.
- Child raw history contains no message after the selected canonical sequence index.
- Copied tool activity includes only calls reachable from copied messages and preserves complete call/return groups.
- Every copied map source range resolves within the child's copied raw prefix.
- No copied checkpoint has an observation boundary after the fork point.
- New child messages begin after the inherited canonical prefix without renumbering or colliding with copied rows.
- Authorization remains vault- and owner-scoped through the existing fork endpoint.

## Observability

Retain one completion event at the existing fork decision boundary and make its structured payload explicit: `event=chat_session_fork_completed`, `source_session_id`, `new_session_id`, `vault_name`, `canonical_through_sequence_index`, `raw_message_count`, `tool_event_count`, `inherited_checkpoint_count`, `latest_checkpoint_kind`, and `lineage_root_session_id`.

Emit `chat_session_fork_rejected` only for a persistence-specific decision that the ordinary API error boundary cannot explain. Include the source session, requested canonical index, reason code, and checkpoint ID when applicable; do not log message contents.

## Testable Slices

### Slice 0 — Lock the canonical fork contract

Add a deterministic `integration/core/chat_session_fork_lineage` scenario before changing persistence. Cover an uncompacted fork, a recovery-card fork, a session-map fork with multiple revisions, and a fork taken before a later checkpoint's observation boundary. Assert exact raw prefixes, absence of later messages, effective replay parity at the branch point, tool-event integrity, checkpoint kinds/counts, map source validity, returned strategy flags, and parent immutability.

Also extend the existing Compaction v1 scenario to replace its obsolete expectation that a compacted fork is a flattened effective-history copy. The new assertion must distinguish canonical raw history from effective replay rather than relying only on displayed message count.

**Exit condition:** The new assertions fail for the current flattening implementation and precisely identify missing canonical messages, checkpoints, and strategy inheritance.

### Slice 1 — Preserve replacement-message origins

Add the nullable checkpoint origin mapping and migration, populate it for newly authored V1 and V2 checkpoints, and project canonical `fork_sequence_index` values for retained checkpoint messages. Implement deterministic legacy alignment without mutating legacy rows during ordinary reads.

Validate fresh databases, upgraded databases with null legacy origin data, exact recovery-card retained-tail alignment, session-map generated-context handling, tool-call/return groups, and fail-closed ambiguous alignment. Run focused schema migration, chat history compaction, repeated compaction, session-map checkpoint, and API detail scenarios.

**Exit condition:** Every forkable assistant message exposed by the session-detail API has a verified canonical source index; generated checkpoint context messages expose no fork index.

### Slice 2 — Atomically clone canonical history and safe checkpoints

Refactor `ChatStore.fork_session()` to copy the raw canonical prefix, referenced tool events, eligible checkpoints with new IDs, and normalized lineage metadata in one transaction. Preserve source sequence coordinates, filter checkpoints by observation boundary, rewrite child-level checkpoint pointers, and validate copied map provenance against child raw history before commit.

Cover recovery-card strategy inheritance and upgrade eligibility, V2 map modal inspection across inherited revisions, forks before and after checkpoints, nested forks, source deletion after a successful fork, and injected mid-transaction failure with no partial child.

**Exit condition:** The child is independently resumable with the same effective context the parent legitimately had at the selected message, and all copied provenance resolves locally.

### Slice 3 — Derive the API result from durable child state

Remove the hard-coded `unassigned`, `has_session_map=False`, and `can_upgrade_to_v2=False` fork response. Resolve these fields through the same backend strategy and checkpoint projections used by the session list. Return the canonical branch point and copied-message count with unambiguous semantics; preserve the current response shape where possible.

Validate that the newly returned session row matches the next session-list response exactly and that the existing browser flow immediately shows the V1 upgrade or V2 map affordance without a manual reload.

**Exit condition:** Fork creation and subsequent list/detail calls agree on strategy, map availability, lineage, and message boundaries.

### Slice 4 — Hardening and current-contract documentation

Review duplicated checkpoint-copy and row-projection logic, keep compatibility code isolated and removable, document the current fork contract, and add an ADR recording canonical-prefix forks with physical isolation and logical lineage. Update the architecture overview only to the extent needed to describe session lineage as durable chat state; do not specify future memory ranking behavior.

Run the production Python quality gate, relevant frontend smoke checks, focused deterministic scenarios, and the complete `integration/core` profile. Inspect one manually created V1 fork and one V2 fork through the normal UI, including upgrade eligibility, map revisions, canonical transcript inspection, continued chat, and parent/child divergence.

**Exit condition:** Code, API behavior, validation, ADR, architecture documentation, and manual UI evidence agree on one fork contract with no temporary repair scripts or dead compatibility paths.

## Deferred Work

- Cross-session FTS result grouping and conversation-family deduplication.
- Semantic ranking or vector embeddings across branch families.
- Vault-to-session link ownership and inherited-link presentation.
- Copy-on-write transcript or checkpoint storage.
- Automatic repair of legacy flattened forks.
- Batch lineage migration or a user-facing family browser.

The lineage recorded here is deliberately sufficient for those later designs without making this branch choose their retrieval semantics.

## Next Step

Build the branch on a live server and manually inspect at least one recovery-card fork and one session-map fork, including inherited strategy controls, map revisions, canonical transcript inspection, continued chat, and parent/child divergence. Whole-branch hardening remains a separate follow-up across the complete Compaction v2 branch.

## Implementation Results

- Checkpoint replacement history now carries aligned canonical source indexes through chat database migration 9. Fresh recovery-card and session-map checkpoints write the mapping directly, while legacy checkpoint projection uses exact ordered provider-message matching and leaves ambiguous messages non-forkable.
- Fork persistence now copies the canonical raw prefix without renumbering, preserves timestamps and reachable structured tool events, clones only checkpoints whose author observation boundary is safe, and gives inherited checkpoints child-owned IDs with explicit origin metadata.
- Fork lineage records the immediate parent, original root, canonical branch point, child-owned boundary, inherited checkpoint count, and copied tool-event count. Nested forks preserve the root.
- The fork API accepts canonical assistant-message branch points and derives strategy, V2 upgrade eligibility, and map availability from durable child state.
- Deterministic coverage now includes V1 raw-history restoration, structured tool activity, V2 future-checkpoint exclusion, inherited revision inspection, nested lineage, parent immutability, and migration behavior.
- Deterministic failure coverage injects a SQLite abort during checkpoint cloning and proves that the transaction leaves no child session, messages, or checkpoints. A separate legacy fixture duplicates provider-native assistant history and proves that origin recovery remains unresolved without preventing a fork from an explicit canonical transcript index.
- The session-map transcript exposes fork actions on protocol-complete assistant messages, reusing the ordinary confirmation, request, and navigation path. The fork API validates against canonical raw history so messages evicted from effective context remain valid historical branch points.
