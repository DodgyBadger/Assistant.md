"""Canonical native tool and retry exchanges for mapped timeline tests."""

from __future__ import annotations


def seed_tool_timeline(store, vault):
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        RetryPromptPart,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    from core.identity import LOCAL_USER_PRINCIPAL_ID

    session = "mapped-tool-timeline"
    store.ensure_session(session, vault, owner_principal_id=LOCAL_USER_PRINCIPAL_ID)
    store.add_messages(
        session,
        vault,
        [
            ModelRequest(parts=[UserPromptPart("Inspect the planning file.")]),
            ModelResponse(
                parts=[
                    TextPart("<think>\n\n"),
                    ToolCallPart(
                        "file_read",
                        {"operation": "read", "path": "plan.md"},
                        "call-map-transcript",
                    ),
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        "file_read", "Planning evidence.", "call-map-transcript"
                    )
                ]
            ),
            ModelResponse(parts=[TextPart("The planning file is current.")]),
            ModelRequest(parts=[UserPromptPart("Continue with the next step.")]),
            ModelResponse(parts=[TextPart("I will continue.")]),
        ],
    )
    store.add_tool_event(
        session_id=session,
        vault_name=vault,
        tool_call_id="call-map-transcript",
        tool_name="file_read",
        event_type="call",
        args={"operation": "read", "path": "plan.md"},
    )
    store.add_tool_event(
        session_id=session,
        vault_name=vault,
        tool_call_id="call-map-transcript",
        tool_name="file_read",
        event_type="result",
        result_text="Planning evidence.",
        result_metadata={"status": "completed", "token_count": 5},
    )
    retry = "mapped-retry-timeline"
    store.ensure_session(retry, vault, owner_principal_id=LOCAL_USER_PRINCIPAL_ID)
    store.add_messages(
        retry,
        vault,
        [
            ModelRequest(parts=[UserPromptPart("Run the requested operation.")]),
            ModelResponse(parts=[ToolCallPart("file_read", {}, "call-map-retry")]),
            ModelRequest(
                parts=[
                    RetryPromptPart(
                        "The path is required.",
                        tool_name="file_read",
                        tool_call_id="call-map-retry",
                    )
                ]
            ),
            ModelResponse(parts=[TextPart("I need a path before I can continue.")]),
        ],
    )
    return session, retry
