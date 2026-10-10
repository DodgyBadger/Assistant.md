# 0020 - Use Task-Owned Streaming As Canonical Chat Execution

## Status

Accepted.

Amends [0019 - Centralize Runtime Execution Task Running](0019-runtime-execution-task-runner.md)
for chat-specific execution semantics.

## Decision

AssistantMD uses task-owned streaming execution as the canonical chat execution
contract.

The chat API does not maintain a separate synchronous or non-streaming
request/response execution path. Chat clients submit a chat execution task and
observe task progress, task terminal state, or persisted session history.

Streaming SSE is the canonical live observation surface for the web chat UI.
Other chat surfaces may consume live events, poll execution task state, or wait
for completion and reload session history, but they use the same task-owned chat
execution contract.

This decision does not remove non-streaming model calls from the system.
Non-chat domains may still use awaited model calls when their execution contract
does not require chat task streaming semantics.

Response-stream completion and execution-task completion are distinct. A successful response `done` event marks the committed assistant answer, while the owning chat task can still be running automatic post-turn compaction. Clients may finalize the visible answer immediately, but refresh effective-context indicators after the task reaches terminal state. Such observation must remain independent of chat controls and task cancellation, bounded, and guarded against session navigation or a newer turn. Deferred-review response completion does not imply approval has been granted or the reviewed operation has run.

## Rationale

A single chat execution path prevents drift in queueing, cancellation, timeout
behavior, rollback provenance, transcript persistence, tool-event capture, and
error handling.

Task-owned chat execution also lets chat turns survive web client disconnects.
The browser, or a future external chat surface, can reconnect by task/session
state instead of owning the model run through one long-lived HTTP request.

Keeping live event consumption optional allows non-web chat surfaces to adopt the
same execution contract without requiring token-by-token UX.
