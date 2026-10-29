"""Cancel a real blocked MCP preflight before any primary model dispatch."""

import asyncio

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_stream_provider import streaming_provider
from validation.scenarios.support.chat_task_journeys import start_chat
from validation.scenarios.support.chat_tasks import chat_turn
from validation.scenarios.support.mcp_http_inputs import mcp_http_server
from validation.scenarios.support.session_ops_chat import configure_provider

pytestmark = pytest.mark.scenario


async def test_cancel_chat_during_real_mcp_preflight(app: Application) -> None:
    vault = app.vault("ChatPreflightCancellationVault")
    with streaming_provider() as model:
        model.release.set()
        await app.start()
        from core.runtime.state import get_runtime_context

        configure_provider(app, model)
        runtime = get_runtime_context()
        async with mcp_http_server(app) as mcp:
            app.evidence.register_secret(mcp.url)
            mcp.list_release.clear()
            try:
                created = await app.request_async(
                    "/api/system/mcp/connections",
                    method="POST",
                    data={
                        "display_name": "Preflight Probe",
                        "url": mcp.url,
                        "transport": "streamable_http",
                        "auth_mode": "none",
                        "allow_private_http": True,
                    },
                )
                assert created.status_code == 200, created.text
                session = "cancel-mcp-preflight"
                task_id = await start_chat(
                    app, vault.name, session, "Cancel before model dispatch."
                )
                async with asyncio.timeout(30):
                    await mcp.list_entered.wait()
                task = await app.request_async(f"/api/tasks/{task_id}")
                assert task.status_code == 200
                assert not model.requests
                cancelled = await app.request_async(
                    f"/api/tasks/{task_id}/cancel", method="POST"
                )
                assert (
                    cancelled.status_code == 200
                    and cancelled.json()["cancelled"] is True
                )
                mcp.list_release.set()
                assert (await app.wait_task(task_id))["status"] == "cancelled"
                assert not model.requests and not mcp.calls
                replay = await app.request_async(f"/api/chat/tasks/{task_id}/events")
                assert (
                    replay.status_code == 200 and '"event": "cancelled"' in replay.text
                )
                removed = await app.request_async(
                    f"/api/system/mcp/connections/{created.json()['connection_id']}",
                    method="DELETE",
                )
                assert removed.status_code == 200
                follow = await chat_turn(
                    app,
                    {
                        "vault_name": vault.name,
                        "session_id": session,
                        "prompt": "Continue after preflight cancellation.",
                        "model": "session-ops-model",
                    },
                )
                assert (
                    follow["terminal_event"]["event"] == "done"
                    and len(model.requests) == 1
                )
            finally:
                mcp.list_release.set()
                await runtime.mcp_manager.shutdown()
