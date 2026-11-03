# Live file-approval mode assessment

Decision: defer. The user authorized implementation only if the change is relatively straightforward. No runtime or validation sources are changed by this assessment.

## Requested behavior

Changing file-approval mode during a running response should govern the next eligible tool invocation. It should not interrupt an invocation that has already started or automatically approve a pending review.

## Current contract and evidence

- `static/js/chat-selection.js` allows changes after response startup, persists them through `PATCH /api/chat/sessions/{session_id}/mode`, and describes them as applying to the next message.
- `api/services/chat_sessions.py:set_chat_session_mode` stores the session preference; it does not update active agent policy.
- `core/chat/task_execution.py:start_streaming_chat_task` captures the accepted mode and preserves it when a turn waits in the session queue. Acceptance also persists a future-session preference, so reading that preference during another run cannot identify an explicit live toggle.
- `core/chat/executor.py:_prepare_agent_config` passes a fixed approval-tool set into the shared tool binder. `file_write` requires review in inline-edit mode. The binder sets the SDK tool's static `requires_approval` flag when constructing the run.
- `PreparedChatExecution` retains the accepted mode and serializes it into deferred-review resume configuration. Resumed agents rebuild approval settings from that configuration.
- `validation/scenarios/integration/core/test_chat_submission_replay_contracts.py:test_queued_turn_retains_accepted_review_mode` explicitly proves that a queued inline-edit turn still requires approval after subsequent submissions and an explicit mode PATCH select normal mode. Replacing accepted mode with the latest session preference would violate this contract.
- The installed Pydantic AI version provides per-tool execution hooks and `ApprovalRequired`, so rebuilding an agent between tool calls is not inherently necessary. A hook alone does not establish correct policy ownership or deferred-review resumption semantics.

## Why the direct change is insufficient

A tool-boundary read of the session preference would conflate an explicit user toggle with the preference saved by another accepted/queued request. Retaining the initial static approval flag would also prevent switching approvals off dynamically. Any live policy must preserve already-approved deferred calls and carry the appropriate mode into subsequent review/resume behavior and reporting.

## Bounded future approach

1. Define an explicit live-change revision and mode, distinct from the session preference written during turn acceptance. Capture the revision and accepted baseline when execution begins, so already-queued turns retain their accepted mode until a later live change during their own execution.
2. Introduce a chat-owned policy evaluated before each eligible tool invocation, using the SDK approval mechanism. Preserve approved-call identity and authorization; leave executing calls and pending review requests untouched.
3. Carry the effective policy consistently into deferred-review resume configuration and logs. Avoid introducing a second competing approval gate in the shared tool binder, nested authoring, or MCP execution.
4. Add real-app acceptance scenarios with controlled external provider timing: toggle normal to inline-edit before a returned write; toggle inline-edit to normal before a returned write; preserve pending approvals and approved resumption; keep the existing queued-turn scenario green. Assert actual writes, review requests, and persisted/resumed state, not toggle copy.
5. Only implement after reviewing the policy scope and deciding that this work warrants a separate behavior change. Keep validation targeted; no full suite is needed for the present assessment.

## Documentation review

No protected documentation edits are proposed for this deferred change. If implementation proceeds, identify any product guidance that describes when approval-mode changes take effect, present the exact draft in chat, and obtain explicit approval before editing files under `docs/`, `README.md`, or `RELEASE_NOTES.md`.
