"""Failed startup and rejected compaction admission leave runtime state usable."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_persistence import PERSISTENCE_CONTEXT
from validation.scenarios.support.chat_tasks import chat_turn
from validation.scenarios.support.compaction_journeys import (
    add_turn,
    compaction_provider,
    configure_compaction,
)
from validation.scenarios.support.mcp_http_inputs import mcp_http_server
from validation.scenarios.support.runtime_cleanup_contracts import live_background_tasks

pytestmark = pytest.mark.scenario


async def test_failed_bootstrap_cleans_mcp_workers_and_repair_closes_real_client(
    app: Application,
) -> None:
    app.vault("MCPBootstrapRecoveryVault")
    await app.start()
    from core.identity import LOCAL_USER_AUTHORITY
    from core.mcp import MCPAuthMode, MCPConnectionCreate
    from core.runtime.config import RuntimeConfigError
    from core.runtime.state import get_runtime_context, has_runtime_context
    from validation.support.configuration import install_configuration

    original = app.configuration()
    invalid = deepcopy(original)
    invalid["settings"]["default_model"]["value"] = "missing-bootstrap-model"
    app.install_configuration(invalid)
    await app.controller.stop_system()
    assert not has_runtime_context()
    baseline = live_background_tasks()
    with pytest.raises(RuntimeConfigError):
        await app.start()
    assert not has_runtime_context()
    await asyncio.sleep(0)
    assert live_background_tasks() == baseline
    assert app.controller.cleanup_failure is None
    install_configuration(app.root / "system", original)
    async with mcp_http_server(app) as server:
        server_tasks = live_background_tasks()
        await app.start()
        application_tasks = live_background_tasks() - server_tasks
        assert (
            application_tasks
        ), "Observe live application work before checking its release"
        runtime = get_runtime_context()
        assert runtime.mcp_connections is not None and runtime.mcp_manager is not None
        connection = runtime.mcp_connections.create_connection_for_authority(
            LOCAL_USER_AUTHORITY,
            MCPConnectionCreate(
                display_name="After failed startup",
                url=server.url,
                auth_mode=MCPAuthMode.NONE,
                allow_private_http=True,
            ),
        )
        lease = await runtime.mcp_manager.acquire(LOCAL_USER_AUTHORITY, connection)
        assert server.initialize_count() == 1
        assert {tool.name for tool in lease.tools} == {
            "search_messages",
            "archive_message",
        }
        await lease.close()
        await app.controller.stop_system()
        assert not has_runtime_context()
        assert all(task.done() for task in application_tasks)
        assert server.closed_sessions() and set(server.closed_sessions().values()) == {
            1
        }


async def test_closed_task_admission_preserves_completed_chat_and_history(
    app: Application,
) -> None:
    vault = app.vault("ClosedCompactionAdmissionVault")
    app.write(vault, "AssistantMD/Authoring/full_history.md", PERSISTENCE_CONTEXT)
    with compaction_provider() as server:
        await app.start()
        from core.chat.executor import _try_auto_compact_after_turn
        from core.runtime.execution_tasks import ExecutionTaskKind
        from core.runtime.state import get_runtime_context

        configure_compaction(app, server, mode="none")
        metadata = app.request("/api/metadata").json()
        assert (
            app.request(
                "/api/system/settings/general/disabled_tools",
                "PUT",
                {"value": json.dumps([tool["name"] for tool in metadata["tools"]])},
            ).status_code
            == 200
        )
        runtime = get_runtime_context()
        session = "closed-admission"
        store = runtime.chat_store
        store.ensure_session(session, vault.name, owner_principal_id="local-user")
        add_turn(
            store, vault.name, session, "Earlier user turn.", "Earlier assistant turn."
        )
        completed = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": session,
                "prompt": "Complete before compaction admission closes.",
                "model": "test",
                "context_template": "full_history.md",
            },
        )
        assert (
            completed["terminal_event"]["event"] == "done"
            and completed["task"]["status"] == "completed"
        )
        history = store.get_history(session, vault.name, mode="raw")
        assert (
            history and store.get_latest_context_checkpoint(session, vault.name) is None
        )
        assert (
            app.request(
                "/api/system/settings/general/compaction_type", "PUT", {"value": "auto"}
            ).status_code
            == 200
        )
        await runtime.task_coordinator.shutdown(reason="validation_admission_closed")
        await _try_auto_compact_after_turn(
            session_id=session, vault_name=vault.name, vault_path=str(vault)
        )
        assert store.get_history(session, vault.name, mode="raw") == history
        assert store.get_latest_context_checkpoint(session, vault.name) is None
        tasks = await runtime.task_coordinator.list_tasks(
            kind=ExecutionTaskKind.HISTORY_COMPACTION.value
        )
        assert tasks == [] and server.requests == []
        original = await runtime.task_coordinator.get_task(completed["task"]["task_id"])
        assert original is not None and original.status == "completed"
        activity = app.request("/api/system/activity-log?limit=200")
        assert activity.status_code == 200
        warning = next(
            entry["data"]
            for entry in activity.json()["entries"]
            if entry.get("data", {}).get("event") == "chat_post_turn_compaction_failed"
            and entry["data"].get("session_id") == session
        )
        assert (
            warning["status"] == "failed"
            and warning["reason"] == "unexpected_post_turn_compaction_failure"
            and warning["error_type"] == "RuntimeError"
        )
