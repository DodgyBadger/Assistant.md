"""Continue a supplied interrupted frontier using a real read tool and HTTP model."""

from __future__ import annotations

import json

from validation.scenarios.application import Application
from validation.scenarios.support.code_tool_chat import configure_provider
from validation.scenarios.support.retry_provider import retry_provider


async def replay_pending_read(app: Application, vault, decision) -> None:
    from core.llm.agents import create_agent, generate_response
    from core.llm.model_factory import build_model_instance
    from core.tools.file_read import FileRead

    assert decision.checkpoint is not None
    with retry_provider("replay") as provider:
        configure_provider(app, provider)
        agent = await create_agent(
            model=build_model_instance("code-tool-model"),
            tools=[FileRead.get_tool(str(vault))],
        )
        checkpoint = app.events.checkpoint()
        result = await generate_response(
            agent, None, message_history=decision.checkpoint.messages
        )
        assert result == "continued"
        app.events.require(
            "tool_invoked", after=checkpoint, tool="file_read", operation="read"
        )
        assert len(provider.calls) == 1
        returned = [
            message
            for message in provider.calls[0]["messages"]
            if message["role"] == "tool"
        ]
        assert len(returned) == 1
        assert "RECOVERY_READ_CONTENT" in json.dumps(returned)
        app.evidence.save_json("recovery-replay-requests.json", provider.calls)
