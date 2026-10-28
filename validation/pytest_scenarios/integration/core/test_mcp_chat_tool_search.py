"""Actual MCP catalog discovery, deferred visibility and tool activity."""

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.mcp_chat_search_model import searching_model
from validation.pytest_scenarios.support.mcp_http_inputs import mcp_http_server

pytestmark = pytest.mark.scenario


async def test_mcp_chat_deferred_search_and_tool_activity(app: Application) -> None:
    vault = app.vault("MCPChatToolSearchVault")
    await app.start()
    from pydantic_ai import Agent
    from pydantic_ai.usage import UsageLimits

    from core.authoring.shared.tool_binding import resolve_tool_binding
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.llm.capabilities.assistant_tools import build_assistant_tools_capabilities
    from core.llm.capabilities.chat_tool_output_cache import (
        build_chat_tool_output_cache_capability,
    )
    from core.llm.capabilities.mcp_tools import acquire_mcp_chat_capabilities
    from core.mcp import MCPConnectionManager
    from core.runtime.state import get_runtime_context

    async with mcp_http_server(app) as server:
        runtime = get_runtime_context()
        manager = MCPConnectionManager(
            connections=runtime.mcp_connections, idle_timeout_seconds=0
        )
        try:
            app.evidence.register_secret(server.url)
            created = await app.request_async(
                "/api/system/mcp/connections",
                method="POST",
                data={
                    "display_name": "Gmail",
                    "url": server.url,
                    "transport": "streamable_http",
                    "auth_mode": "none",
                    "allow_private_http": True,
                    "allowed_tools": ["search_messages"],
                },
            )
            assert created.status_code == 200, created.text
            connection = created.json()
            session = "mcp-chat-session"
            runtime.chat_store.ensure_session(
                session, vault.name, owner_principal_id="local-user"
            )
            with use_execution_authority(LOCAL_USER_AUTHORITY):
                binding = resolve_tool_binding(["file_read"], vault_path=str(vault))
                bundle = await acquire_mcp_chat_capabilities(
                    manager=manager, authority=LOCAL_USER_AUTHORITY
                )
                try:
                    assert not bundle.unavailable
                    assert await manager.evict_idle() == 0
                    assert bundle.model_tool_names == ("gmail_search_messages",)
                    observed = []
                    capabilities = [
                        *build_assistant_tools_capabilities(
                            tools=binding.tool_functions,
                            instructions=binding.tool_instructions,
                        ),
                        *bundle.capabilities,
                        build_chat_tool_output_cache_capability(
                            vault_name=vault.name,
                            session_id=session,
                            now=None,
                            event_sink=runtime.chat_store,
                        ),
                    ]
                    result = await Agent(
                        searching_model(connection["connection_id"], observed),
                        capabilities=capabilities,
                    ).run(
                        "Find a project email",
                        usage_limits=UsageLimits(tool_calls_limit=2),
                    )
                    assert result.output == "MCP result observed"
                    assert result.usage.tool_calls == 2
                    assert server.calls == [
                        ("search_messages", {"query": "project alpha"})
                    ]
                    events = runtime.chat_store.get_tool_events(session, vault.name)
                    assert [
                        (event.event_type, event.tool_name)
                        for event in events
                        if event.tool_name == "gmail_search_messages"
                    ] == [
                        ("call", "gmail_search_messages"),
                        ("result", "gmail_search_messages"),
                    ]
                    app.evidence.save_json("mcp-chat-model-observations.json", observed)
                finally:
                    await bundle.snapshot.close()
                assert await manager.evict_idle() == 1
                assert sum(server.closed_sessions().values()) == 1
            deleted = await app.request_async(
                f"/api/system/mcp/connections/{connection['connection_id']}",
                method="DELETE",
            )
            assert deleted.status_code == 200, deleted.text
        finally:
            await manager.shutdown()
