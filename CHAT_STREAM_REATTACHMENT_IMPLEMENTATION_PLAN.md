# Chat Stream Reattachment Implementation Plan

Status: implemented; targeted validation, the complete deterministic `integration/core` profile, JavaScript syntax checks, and the production Python quality gate pass.

## Goal

Make returning to an active chat session feel immediate and accurately represent the current task state without replaying hundreds of token-level events through repeated full Markdown renders.

The implementation must preserve AssistantMD's task-owned chat execution contract: the model run remains owned by a process-local execution task, SSE remains the canonical live observation surface, persisted session history remains the terminal durable record, and browser disconnects do not cancel the task.

## User-Visible Outcome

When the user reloads the browser or returns to a session with an active chat task:

- persisted session history renders normally;
- active-task discovery and stream reattachment begin without waiting for compaction-token estimation;
- the in-progress response, reasoning, tool states, and pending review state are restored in one bounded catch-up operation;
- live SSE consumption resumes after the restored event cursor without missing or duplicating content;
- an old model-retry event does not leave the UI incorrectly labeled `Reconnecting to model`;
- a task whose raw event cursor has expired can still restore its current projected state instead of forcing the browser to wait until terminal persistence;
- a task that completes between persisted-session loading and active-task discovery triggers one final session refresh instead of leaving the browser on a stale pre-completion transcript; and
- ordinary short network interruptions continue to use cursor-based SSE replay.

## Current Behavior and Confirmed Bottlenecks

The current flow is structurally correct but inefficient:

1. `loadSession()` requests and renders persisted session state, starts a compaction-status refresh, later awaits another compaction-status refresh, and only then calls `reattachActiveChatTask()`.
2. Reattachment finds the active task through `/api/chat/sessions/{session_id}/active-task` and starts `/api/chat/tasks/{task_id}/events` with `after_sequence=0`.
3. The event buffer replays up to 500 retained events. Each `delta` and `thinking_delta` causes the browser to reparse and rerender the entire accumulated Markdown response and scroll the chat.
4. If sequence zero has fallen outside the retained raw window, the endpoint returns `410`; the browser polls until task completion and then reloads persisted history.
5. Replaying a historical `chat_retry_scheduled` event calls `resetAssistantStream()`, which labels the response `Reconnecting to model`. Later text deltas do not restore an ordinary active status, so the historical state can appear current.
6. A task can complete after `ChatSessionDetailResponse` is read but before the active-task lookup. The lookup then returns `404`, and the browser keeps the pre-completion session payload because it has no signal to reload the newly persisted terminal response.

The main latency is therefore browser catch-up and request sequencing, not reconstruction of the model run.

## Architectural Fit

This work extends the contracts already established by:

- `docs/development/adr/0019-runtime-execution-task-runner.md`: runtime infrastructure owns generic task mechanics while the chat adapter owns chat event payloads and replay behavior;
- `docs/development/adr/0020-canonical-task-owned-chat-execution.md`: SSE remains the canonical live chat observation surface, while task/session state supports reconnection; and
- `docs/development/architecture.md`: chat tasks own model streaming, buffered event replay, cancellation, and safe recovery.

The replay projection will remain process-local, just like the execution task and raw event buffer. It is not a new durable store and must not become canonical chat history. A process restart may still end active work according to the existing execution-task contract.

## Proposed Design

### 1. Maintain a compact replay projection beside each raw event stream

Extend `core/chat/task_events.py` so each `_ChatTaskEventStream` incrementally maintains the current UI-relevant projection while events are appended. The projection should contain enough information to reconstruct the current in-progress assistant card without retaining or returning every token-level delta.

The projection should track:

- concatenated current response text;
- concatenated current reasoning text;
- safe tool lifecycle events or equivalent current tool states, preserving tool-call identity and display order but not full arguments or results;
- the current deferred-review event, when present;
- the effective terminal event, when present;
- retry redirect information when the task has handed off to a replacement task; and
- the latest incorporated raw event sequence.

Projection reduction must follow existing browser semantics:

- `delta` and `thinking_delta` append content;
- `chat_retry_scheduled` with `reset_response=true` clears response and reasoning text but does not invent a current reconnect status;
- `chat_retry_redirect` records the replacement task and terminal redirect state;
- tool start and finish events preserve the safe payloads already exposed by SSE;
- `review_required` preserves the review artifact payload;
- `done`, `cancelled`, and `error` preserve their effective terminal payloads; and
- unknown future non-delta events must not cause unbounded projection growth or silently corrupt the cursor contract.

The raw bounded deque remains unchanged for ordinary cursor replay and diagnostics. Trimming raw events must not discard the compact projection.

### 2. Expose a task-owned replay snapshot API

Add a read-only endpoint under the existing chat task surface, tentatively:

```text
GET /api/chat/tasks/{task_id}/replay-snapshot
```

The response should include:

```json
{
  "task_id": "...",
  "latest_sequence": 123,
  "terminal": false,
  "events": []
}
```

`events` should be a compact effective event sequence that can be consumed by the existing frontend event reducer: at most one accumulated reasoning event, at most one accumulated response event, bounded safe tool lifecycle events, an optional review event, and an optional terminal or redirect event. Reusing existing event payload shapes avoids creating a second interpretation of tool, review, terminal, and redirect semantics in the browser.

The endpoint must use the same authority-mediated task lookup as the existing event stream. It must reject non-chat tasks as not found and use the same inaccessible/unknown behavior as other task APIs. A queued or running chat task that has not emitted its first event should return an empty snapshot at sequence zero; a stable unavailable response is appropriate only when a retained task no longer has replay state.

Snapshot retrieval and cursor capture must be atomic under the event-buffer lock. Events appended after `latest_sequence` will then be obtained through the existing SSE endpoint with `after_sequence={latest_sequence}`. This is the race-free handoff between catch-up and live observation.

### 3. Hydrate reattached assistant state once, then resume SSE

Update `static/app.js` so `reattachActiveChatTask()`:

1. discovers the active task as it does today;
2. creates the in-progress assistant card;
3. requests the replay snapshot;
4. applies the compact snapshot events without rerendering Markdown after each event;
5. performs one final render of response and reasoning state;
6. resumes `consumeChatTaskEvents()` after the snapshot's `latest_sequence`; and
7. follows existing terminal, review, cancellation, error, and redirect behavior.

The stream consumer should accept an initial cursor and existing accumulator state rather than always starting from sequence zero. Snapshot hydration should reuse `applyChatStreamPayload()` and the existing tool/review handlers where practical, with a batch-render option rather than duplicating their behavior.

For normal newly started tasks, retain the current live SSE path from sequence zero. For short network interruptions in an already attached page, retain cursor replay from the last received sequence.

If an attached stream receives `410 ChatTaskEventCursorExpired`, replace the current wait-until-terminal fallback with snapshot hydration followed by SSE resubscription from the snapshot cursor. Waiting for terminal persistence remains the final fallback only when no replay snapshot is available.

### 4. Remove avoidable work from the reattachment critical path

Update `loadSession()` so compaction-status refresh is not awaited before active-task discovery. Keep one non-blocking refresh after the selected session is known and rely on the existing request ID guard to discard stale responses.

Do not fold active-task state into `ChatSessionDetailResponse`; session persistence and process-local execution state have distinct ownership. The existing active-task endpoint remains the discovery boundary.

Close the session-load/active-task race explicitly. When active-task discovery returns `404` after loading a session that may have changed during the request sequence, perform one guarded final session-detail refresh or compare a lightweight session revision before deciding the initially rendered payload is current. The guard must prevent recursive reload loops and must abandon the refresh if the selected vault or session changes.

### 5. Correct transient status handling

Snapshot hydration must represent current projected state, not replay historical retry labels. A snapshot that contains current response or reasoning content should show an ordinary active status after hydration unless a tool, review, terminal, or error event specifies something more precise.

After a live SSE reconnect succeeds, receipt of a current semantic event should clear the browser-level `Reconnecting…` status. Keep browser transport reconnection distinct from model retry status in both copy and state transitions.

## Contract-Sensitive Areas

### Event cursor semantics

`latest_sequence` means every raw event through that sequence is represented by the snapshot. The client must subscribe with that exact value and the event stream must continue to return only events with greater sequence numbers.

### Raw event retention

The existing 500-event raw retention remains a bounded incremental replay mechanism. The new projection is specifically what allows reconstruction after the raw cursor expires. Increasing the raw buffer is not part of this change.

### Tool privacy

The projection must contain only the same safe tool status data currently sent through routine SSE payloads. It must not reintroduce full tool arguments or results into routine browser responses.

### Terminal persistence

Persisted chat messages remain authoritative after task completion. The replay snapshot is only for an active or recently terminal process-local stream and must not replace session reload after terminal completion.

### Settings and persistent runtime data

This change adds no setting, database migration, or persistent runtime data. Replay projections disappear with the process-local task/event buffer.

## Affected Areas

- `core/chat/task_events.py`: compact replay projection, atomic snapshot lookup, retention behavior.
- `core/chat/task_execution.py`: only if event normalization or snapshot shaping belongs beside existing SSE serialization; do not move generic execution policy here.
- `api/models.py`: typed replay snapshot response.
- `api/endpoints.py`: authority-checked replay snapshot endpoint beside the current chat event endpoint.
- `api/services/` only if a narrow projection service keeps endpoint code consistent with existing task APIs.
- `static/app.js`: session-load sequencing, snapshot hydration, initial cursor support, expired-cursor recovery, and status correction.
- `static/js/chat-rendering.js`: batch mutation/render support for assistant response and reasoning state.
- `validation/scenarios/integration/core/chat_task_event_buffer.py`: deterministic projection reducer and retention assertions.
- `validation/scenarios/integration/core/chat_task_event_stream_api.py`: endpoint, cursor handoff, expired raw replay, and task-ownership assertions.
- `docs/development/architecture.md`: current-contract note that reconnecting clients hydrate a compact process-local projection before resuming SSE.
- `RELEASE_NOTES.md`: user-visible faster and more reliable active-chat reconnection.

## Validation-First Workflow

### Server-side deterministic contract

Extend the existing integration scenarios before implementation with assertions that:

- multiple text and reasoning deltas collapse into compact snapshot events with exact concatenated content;
- a reset-response retry removes stale pre-retry response and reasoning content from the snapshot;
- tool start/finish state, review-required state, terminal events, and retry redirects remain reconstructable;
- snapshot `latest_sequence` is atomic and subsequent `events_after(latest_sequence)` returns only newer events;
- trimming the raw deque beyond sequence zero does not prevent snapshot reconstruction;
- the replay snapshot endpoint rejects non-chat or inaccessible tasks consistently;
- an active chat task snapshot can be fetched while the SSE subscriber is detached; and
- completion between session-detail loading and active-task discovery results in the terminal persisted response being rendered; and
- the task continues running after subscriber disconnect, preserving the existing ADR 0020 invariant.

The primary scenarios are `chat_task_event_buffer.py` and `chat_task_event_stream_api.py`; no live model is required.

### Frontend-focused checks

Because the repository does not currently have a JavaScript unit-test runner, keep frontend changes narrow and isolate batching/state-transition helpers so they can be smoke-tested without browser automation where practical. Run `node --check` on changed JavaScript files and perform a browser smoke test covering:

1. start a response that emits enough content or tool activity to remain active;
2. reload or close and reopen the page;
3. return to the session and verify immediate catch-up, correct partial content, correct tool state, and continued live streaming;
4. force a raw cursor-expiry condition with a reduced validation buffer and confirm snapshot recovery rather than wait-until-terminal behavior;
5. exercise a model retry or synthetic retry event and verify stale `Reconnecting to model` copy does not persist; and
6. complete, cancel, and deferred-review terminal paths and verify the normal persisted-session reload still occurs.

If implementation introduces a reusable pure JavaScript reducer substantial enough to warrant long-term unit coverage, use Node's built-in test runner rather than adding a third-party frontend test framework solely for this change.

### Agent-owned checks

During implementation, run the two relevant individual integration scenarios directly, targeted Python tests, `node --check` for changed JavaScript, and the complete production Python quality gate:

```bash
uv run ruff check .
uv run black --check .
uv run mypy api core
```

Once branch behavior is stable, run the full `integration/core` validation profile during hardening or merge preparation.

## Implementation Sequence

1. Add failing replay-projection assertions to `chat_task_event_buffer.py`, covering delta coalescing, retry reset, tool/review/terminal state, cursor capture, and survival after raw-event trimming.
2. Implement the narrow replay projection and atomic snapshot method in `ChatTaskEventBuffer` without changing existing `append()`, `events_after()`, or `subscribe()` behavior.
3. Add the typed snapshot API response and failing endpoint assertions to `chat_task_event_stream_api.py`, including authority, non-chat rejection, and snapshot-to-SSE cursor handoff.
4. Implement the replay snapshot endpoint through the existing execution-task access boundary.
5. Add batch-capable assistant rendering and let the stream consumer accept an initial sequence cursor.
6. Change reattachment to hydrate the snapshot once and continue SSE after its cursor.
7. Route `410` cursor expiry through snapshot recovery, retaining wait-until-terminal only as a fallback when projected state is unavailable.
8. Remove the duplicate/blocking compaction-status refresh from `loadSession()`, close the session-detail/active-task completion race with one guarded refresh, and correct reconnect status transitions.
9. Run the targeted scenarios, JavaScript syntax/smoke checks, and full Python quality gate; then perform focused peer review before committing.
10. Update architecture documentation and release notes to describe the final current contract.

## Non-Goals

- Making active execution tasks durable across application restarts.
- Persisting token-level partial assistant output in `chat_sessions.db`.
- Replacing SSE with WebSockets or polling.
- Increasing the event-buffer limit as the primary solution.
- Changing model-stream retry, cancellation, queueing, or rollback policy.
- Returning full tool arguments or results in reconnect snapshots.
- Folding process-local active-task state into canonical session-detail persistence.

## Risks and Mitigations

- **Snapshot/live race:** Capture projection and `latest_sequence` atomically; resume SSE strictly after that cursor.
- **Reducer drift between server and browser:** Reuse existing SSE event payload shapes and the existing frontend event reducer; keep server projection rules limited to coalescing and current-state reduction.
- **Historical retry state shown as current:** Apply reset semantics inside the projection but omit historical reconnect presentation state.
- **Tool-detail exposure:** Build snapshots only from already-safe SSE payloads and assert absence of arguments/results in validation.
- **Memory growth:** Coalesce response and reasoning text, retain bounded tool/control state, and avoid copying the raw event backlog into snapshot responses.
- **Terminal duplication:** Treat snapshot terminal events through the same frontend path as live terminal events, then reload canonical persisted history once.
- **Session-load completion race:** When active-task discovery finds no task after an earlier session-detail read, perform one guarded freshness check without recursively invoking normal reattachment.
- **Frontend regression without a full JS harness:** Keep hydration helpers small, add Node smoke coverage when practical, and perform the explicit browser scenarios above.

## Phase Handoff

Implementation, focused hardening, documentation, targeted integration scenarios, the complete deterministic `integration/core` profile, JavaScript syntax checks, and the production Python quality gate are complete.
