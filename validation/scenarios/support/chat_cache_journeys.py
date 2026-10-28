"""Execute controlled real code tool calls in a single chat session."""

from validation.scenarios.support.chat_tasks import chat_turn


async def code_turn(app, server, vault_name, session_id, code):
    server.tool_name = "code_execution"
    server.arguments = {"code": code}
    result = await chat_turn(
        app,
        {
            "vault_name": vault_name,
            "session_id": session_id,
            "prompt": "Execute the requested local operation.",
            "model": "code-tool-model",
        },
    )
    assert result["start_response"].status_code == 200
    assert result["terminal_event"]["event"] == "done"
    return result
