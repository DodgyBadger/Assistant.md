"""A child delegate request limit leaves its parent chat usable."""

import json

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_tasks import chat_turn
from validation.scenarios.support.delegate_inputs import delegate_provider

pytestmark = pytest.mark.scenario


async def test_delegate_limit_failure_preserves_parent_chat(app: Application) -> None:
    vault = app.vault("DelegateParentRecoveryVault")
    app.write(vault, "notes/0.md", "Actual read before request limit")
    await app.start()
    from core.chat.tool_history import analyze_tool_history
    from core.runtime.state import get_runtime_context

    update = app.request(
        "/api/system/settings/general/delegate_model_requests_limit",
        method="PUT",
        data={"value": "1"},
    )
    assert update.status_code == 200
    with delegate_provider(app) as server:
        server.arguments = {
            "prompt": "Read the child input.",
            "model": "code-tool-model",
            "tools": ["file_read"],
        }
        result = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": "delegate-parent-limit",
                "prompt": "Delegate a bounded read.",
                "model": "code-tool-model",
            },
        )
        assert (
            result["terminal_event"]["event"] == "done"
            and result["task"]["status"] == "completed"
        )
        runtime = get_runtime_context()
        events = [
            row
            for row in runtime.chat_store.get_tool_events(
                "delegate-parent-limit", vault.name
            )
            if row.tool_name == "delegate" and row.event_type == "result"
        ]
        assert len(events) == 1
        metadata = json.loads(events[0].result_metadata_json or "{}")
        assert (
            metadata["status"] == "failed"
            and metadata["limit_kind"] == "model_requests"
            and metadata["limit_setting"] == "delegate_model_requests_limit"
        )
        assert analyze_tool_history(
            runtime.chat_store.get_history("delegate-parent-limit", vault.name) or []
        ).ok
        children = await runtime.task_coordinator.list_tasks(kind="delegate")
        assert (
            len(children) == 1
            and children[0].parent_task_id == result["task"]["task_id"]
            and children[0].is_terminal
        )
        app.events.require(
            "delegate_failed",
            session_id="delegate-parent-limit",
            task_id=children[0].task_id,
        )
