"""Canonical transcript and caller-supplied retrieved window messages."""


def seed_retrieval(store, vault, session):
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    store.ensure_session(session, vault, owner_principal_id="local-user")
    original = "The original pilot ceiling is $120,000."
    payload = {
        "status": "ok",
        "operation": "get_transcript_window",
        "session_id": session,
        "vault_name": vault,
        "messages": [{"sequence_index": 0, "role": "user", "content": original}],
    }
    store.add_messages(
        session,
        vault,
        [
            ModelRequest(parts=[UserPromptPart(content=original)]),
            ModelResponse(parts=[TextPart(content="Recorded the original ceiling.")]),
            ModelRequest(
                parts=[UserPromptPart(content="What was the original ceiling?")]
            ),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="session_ops",
                        args={
                            "operation": "get_transcript_window",
                            "sequence_index": 0,
                        },
                        tool_call_id="window-1",
                    )
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="session_ops",
                        tool_call_id="window-1",
                        content=payload,
                    )
                ]
            ),
            ModelResponse(
                parts=[TextPart(content="The original ceiling was $120,000.")]
            ),
        ],
    )
    return original


def append_fragment_retrieval(store, vault, session, text, start, end):
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
        "session_id": session,
        "vault_name": vault,
        "messages": [
            {
                "sequence_index": 0,
                "role": "user",
                "content": text[start:end],
                "content_start": start,
                "content_end": end,
                "content_complete": False,
            },
            {
                "sequence_index": 0,
                "role": "user",
                "content": "X" * (end - start),
                "content_start": start,
                "content_end": end,
                "content_complete": False,
            },
        ],
    }
    store.add_messages(
        session,
        vault,
        [
            ModelRequest(parts=[UserPromptPart(content="Retrieve the fragment.")]),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="session_ops", args={}, tool_call_id="fragment"
                    )
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="session_ops",
                        tool_call_id="fragment",
                        content=payload,
                    )
                ]
            ),
            ModelResponse(parts=[TextPart(content="Fragment retrieved.")]),
        ],
    )
