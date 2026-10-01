# Session Context Strategy Upgrade Plan

Status: implemented and validated.

## Objective

Allow a user to upgrade one explicitly selected Compaction v1 session to Compaction v2 from the session list without batch migration or automatic inference spending. Keep the exceptional transition isolated so the UI, endpoint, and migration orchestrator can be removed after legacy recovery-card sessions are no longer material.

## User Contract

- The session-list API reports the effective context strategy derived from the latest checkpoint: `unassigned` when no checkpoint exists, `recovery_card` for Compaction v1, and `stepped_session_map` for Compaction v2.
- An upgrade action appears only for a recovery-card session when Compaction v2 is the configured strategy.
- The user confirms the inference-bearing operation for one session. The API starts a background execution task and returns immediately.
- The UI monitors the normal execution-task endpoint. On success it refreshes the session list and reloads the active session, causing the upgrade action to disappear and the map affordance to appear.
- No batch or automatic migration path is added.

## Runtime Design

- Add one isolated `core/chat/context_strategy_upgrade.py` domain adapter. It owns eligibility, raw-history planning, bounded chronological authoring passes, progress logging, and the exceptional recovery-card-to-session-map checkpoint transition.
- Run the operation as a `context_strategy_upgrade` execution task under the chat-session scope and existing keyed session gate. Nested map-authoring calls continue to use the normal `session_map_authoring` task path.
- Build the target boundary from canonical raw history with the configured V2 low watermark and retained-group floor. Use historical recovery-card boundaries to split the consumed prefix into bounded chronological authoring passes, then supply the final retained raw tail as recent evidence.
- Stage all authored maps in memory. Do not change effective history until every authoring pass succeeds and the captured history revision still matches.
- Append one final session-map checkpoint with an exact recovery-card predecessor precondition. Preserve raw messages and all historical checkpoints.
- Keep ordinary Compaction v1 and v2 paths unchanged. They must continue rejecting implicit strategy changes.

## Persistence and Failure Invariants

- Canonical raw messages are never rewritten or deleted.
- Failure or cancellation before the final checkpoint append leaves the recovery-card checkpoint effective.
- A concurrent history change makes the final compare-and-swap checkpoint write fail.
- Only a latest recovery-card checkpoint can be upgraded; unpinned and existing V2 sessions are rejected.
- The configured global strategy must be `stepped_session_map`, and normal map-author model validation remains authoritative.
- The final checkpoint records ordinary map authoring and context-rendering provenance and uses `context_strategy_upgrade` as its source.

## API and UI Surface

- Extend `ChatSessionInfo` with `context_strategy` and `can_upgrade_to_v2`.
- Add `POST /api/chat/sessions/{session_id}/upgrade-context-strategy` with a vault-scoped request and a task-start response.
- Add one session-row action using the existing icon-button styles, confirmation pattern, and execution-task polling endpoint.
- Do not add settings, scheduled work, startup migration, batch controls, or a durable migration table.

## Observability

- `context_strategy_upgrade_started`: task ID, session ID, vault, source checkpoint ID, history revision.
- `context_strategy_upgrade_planned`: task ID, target boundary, raw token estimate, retained message/group counts, authoring pass count.
- `context_strategy_upgrade_completed`: task ID, new checkpoint ID, source checkpoint ID, target boundary, authoring pass count, raw-preservation flag.
- Task-runner failure and cancellation events remain the terminal lifecycle source; the domain logs a bounded `context_strategy_upgrade_failed` event before re-raising an execution error.

## Validation Target

Add a deterministic `integration/core/session_context_strategy_upgrade` scenario that proves:

1. unpinned, V1, and V2 sessions receive the correct list eligibility;
2. one V1 upgrade starts a background task and authors through the normal map-author task path;
3. the completed upgrade makes a session-map checkpoint effective while preserving canonical raw messages and the historical recovery-card checkpoint;
4. checkpoint provenance and map inspection remain available;
5. a repeated upgrade is rejected;
6. an authoring failure leaves V1 effective with no partial session-map checkpoint.

Frontend rendering and polling receive a focused syntax/build smoke check; the durable eligibility and migration behavior remain covered by the integration scenario.

## Implementation Sequence

1. Add the failing deterministic API and persistence scenario.
2. Add strategy projection and API models.
3. Implement the isolated gated migration task and exact checkpoint predecessor guard.
4. Add the endpoint and session-row interaction.
5. Run the focused scenario, relevant existing session-map and task scenarios, CSS/JavaScript build checks if needed, and the complete production Python quality gate.
6. Commit the coherent slice locally; push only on explicit request.

## Validation Result

- The deterministic `integration/core/session_context_strategy_upgrade` scenario covers eligibility, governed background execution, bounded child authoring passes, atomic success and failure, raw-history preservation, map inspection, repeated-upgrade rejection, and isolation from active chat-task reattachment.
- The relevant focused session-map, execution-task, and API scenarios pass.
- The complete deterministic `integration/core` profile passes: 127 of 127 scenarios.
- The production Python quality gate and JavaScript syntax checks pass.
