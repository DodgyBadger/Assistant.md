# Compaction V2 Bounded Context and Canonical Timeline Plan

Status: implemented; manual browser and long-session performance validation pending.

## Objective

Make Compaction v2 recover substantial model-context headroom even when recent turns contain large tool exchanges, while keeping every canonical message complete and conveniently inspectable in the ordinary chat timeline. Separate the model's bounded effective context from the browser's paged view of canonical history.

## User Contract

- Compaction never truncates or deletes a persisted message.
- The ordinary chat timeline shows complete canonical user and assistant messages and collapsed tool activity. It does not expose partial message bubbles produced by context reduction.
- A newly opened session loads only a bounded newest page. The user can load older canonical pages in reverse chronological direction with an explicit **Load older messages** control.
- Loading an older page inserts it above the current timeline without moving the user's reading position.
- The compaction notice marks the boundary between canonical messages represented through the session map and the raw suffix supplied directly to the model. It no longer means that earlier messages are available only in the map modal.
- The session-map modal shows the current map, historical map revisions, revision metadata, and provenance ranges. It no longer contains a second canonical transcript viewer.

## Context-Reduction Contract

- The configured low watermark is a hard target for the raw effective-history projection selected during Compaction v2. The configured retained-turn count expresses a preference for recent complete conversational groups only while those groups fit within the target.
- Reduction first evicts the oldest complete protocol-safe groups. If the retained-turn preference would leave the projection above the target, reduction continues across that preference from oldest to newest.
- Canonical messages and tool events remain unchanged in SQLite regardless of the effective projection.
- Session-map authoring observes full canonical evidence for every newly consumed group before the checkpoint becomes effective.
- The first implementation should prefer whole-group eviction over text truncation. If no complete recent group fits, the map may become the only replacement context until the next user turn. A separate bounded message-projection policy may later retain selected user or assistant text, but it must be explicit, protocol-safe, provenance-linked, and marked when incomplete.
- Recovery-card behavior remains unchanged in this slice. The hard low-watermark rule applies to the session-map strategy whose purpose is bounded stepped eviction.

## API and Persistence Boundaries

- Keep `chat_messages` and `chat_tool_events` as the canonical source. Do not rewrite canonical rows or add a duplicate transcript table.
- Add a vault-scoped canonical timeline page endpoint keyed by a stable sequence cursor rather than an offset. A response returns chronological display rows, tool-call summaries needed by those rows, the next older cursor, and the effective Compaction v2 boundary needed to place the notice.
- Change the ordinary session-detail response to return only the newest canonical page plus paging metadata. Effective provider history remains an internal `ChatHistoryService` and checkpoint concern.
- Reuse the existing canonical display-row projection and tool-event safety rules currently used by the map transcript viewer. Move shared projection into a service helper rather than duplicating it.
- Remove transcript page data and transcript query parameters from the session-map response after the ordinary timeline consumes the canonical paging contract.
- Preserve map provenance as canonical message ranges. A later enhancement may navigate from a provenance range to the corresponding canonical timeline page; that navigation is not required for the first slice.

## Frontend Boundaries and Performance

- Initially render only the newest bounded page, targeting 40 display rows unless measurement supports a different value.
- Use an explicit load control before adding automatic upward-scroll loading. This avoids accidental request churn and makes behavior deterministic on mobile and desktop.
- Preserve the scroll anchor when prepending rows.
- Keep tool groups collapsed and continue loading full tool details only through the existing tool-detail endpoint.
- Do not add DOM virtualization in the first slice. Measure timeline rendering after repeated page loads; add a bounded DOM window only if accumulated pages show material slowdown.
- Keep live task streaming and reconciliation scoped to the newest loaded edge. Loading older pages must not trigger active-task reattachment or replace the current timeline.

## Observability

- Extend Compaction v2 planning and completion logs with the configured retained-turn preference, actual retained group count, whether the preference was relaxed, target tokens, and estimated post-compaction tokens.
- Log canonical page requests at debug level with session ID, cursor, returned row count, and whether another older page exists. Do not log message text or tool payloads.
- Treat failure to project a canonical page safely as an API error with bounded diagnostics; do not silently omit malformed protocol groups.

## Validation Targets

1. Extend the stepped-eviction planner scenario with tool-heavy recent groups proving that Compaction v2 reaches the low watermark by relaxing the retained-turn preference and preserves a protocol-safe boundary.
2. Extend the session-map post-turn scenario to prove the author receives every newly consumed canonical message and the committed effective history is bounded.
3. Add a deterministic canonical-timeline paging scenario proving newest-page loading, stable reverse cursor paging, no duplicates or gaps, complete canonical messages, collapsed tool metadata, boundary placement, and vault isolation.
4. Update the map-inspection scenario to prove revision browsing remains available without transcript payloads.
5. Add focused frontend tests or DOM probes for initial newest-page rendering, load-older prepending, scroll-anchor preservation, collapsed tool rendering, and map-only modal behavior.
6. Run relevant focused scenarios during implementation. Run `python validation/run_validation.py run integration/core` when the branch returns to merge-preparation hardening.

## Implementation Slices

### Slice 1 — Enforce the Compaction v2 budget

- [x] Change the stepped eviction planner so the retained-turn count is preferred rather than absolute.
- [x] Permit complete-group eviction past the preference until the target is reached, including consuming every completed group when none fits.
- [x] Keep map authoring, checkpoint provenance, history-revision fencing, and canonical persistence unchanged.
- [x] Add result metadata and logs that make preference relaxation visible.

### Slice 2 — General canonical timeline paging

- [x] Extract the map transcript's canonical display-row projection into a reusable service path.
- [x] Add stable reverse-cursor API models and an endpoint for canonical session timeline pages.
- [x] Return a bounded newest page from session detail and expose its older cursor and current effective boundary.
- [x] Keep tool-call summaries and tool-detail authorization correct for canonical rows outside effective provider history.

### Slice 3 — Reverse-paged ordinary chat UI

- [x] Add **Load older messages** above the currently loaded timeline when older rows exist.
- [x] Prepend pages without rebuilding newer content or changing the reading position.
- [x] Render the Compaction v2 boundary notice at its canonical location while keeping complete older messages available above it.
- [x] Preserve fork actions and tool-detail links on canonical historical rows.

### Slice 4 — Make the map modal map-only

- [x] Remove the evicted-transcript section, transcript paging state, transcript tool links, and transcript fork actions from the modal.
- [x] Retain revision selection, map collapse state, revision metadata, and provenance display.
- [x] Update labels so the modal and notice describe the canonical timeline as the route to original messages.

### Slice 5 — Focused hardening

- [x] Cover large complete groups, successful tool exchanges, multiple map revisions, forks, and sessions without checkpoints through focused deterministic scenarios.
- [x] Add a frontend interaction probe for reverse paging, boundary placement, and scroll-anchor preservation.
- [ ] Manually exercise active-task reattachment and mobile loading, then measure response size, database query time, render time, and DOM growth on a production-sized long session before deciding whether virtualization belongs in a later branch.
- [x] Update ADR 0049 and ADR 0050 to record the bounded-context and canonical-timeline contracts.

## Validation Record

- Focused Compaction v2, checkpoint, fork-lineage, tool-replay, upgrade, and persistence scenarios pass.
- Frontend session-map and chat-rendering smoke tests pass, including an interaction probe for loading older canonical rows without a scroll jump.
- Production Python quality gates pass: Ruff, Black, and MyPy report no findings.
- The complete deterministic `integration/core` profile passes: 132 of 132 scenarios.

## Immediate Next Step

Manually verify the paged timeline on mobile and against a production-sized session before deciding whether DOM virtualization or a persisted display-row index warrants a later branch.
