"""Canonical turns and explicit transcript-window transport inputs."""


def add_turn(store, vault, session, user, assistant):
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        UserPromptPart,
    )

    store.add_messages(
        session,
        vault,
        [
            ModelRequest(parts=[UserPromptPart(content=user)]),
            ModelResponse(parts=[TextPart(content=assistant)]),
        ],
    )


def add_window(store, vault, session, source_session, items, call_id):
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    payload = {
        "status": "ok",
        "operation": "get_transcript_window",
        "session_id": source_session,
        "vault_name": vault,
        "messages": items,
    }
    store.add_messages(
        session,
        vault,
        [
            ModelRequest(parts=[UserPromptPart(content="Retrieve source evidence.")]),
            ModelResponse(
                parts=[
                    ToolCallPart(tool_name="session_ops", args={}, tool_call_id=call_id)
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="session_ops", tool_call_id=call_id, content=payload
                    )
                ]
            ),
            ModelResponse(parts=[TextPart(content="Window retrieved.")]),
        ],
    )
