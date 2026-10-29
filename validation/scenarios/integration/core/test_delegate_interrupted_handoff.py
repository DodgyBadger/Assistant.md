"""An interrupted real child retains bounded audit and safe handoff metadata."""

import json

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_checkpoint_retry_provider import (
    checkpoint_retry_provider,
)
from validation.scenarios.support.delegate_inputs import invoke_delegate

pytestmark = pytest.mark.scenario


async def test_interrupted_delegate_exposes_completed_tool_audit(
    app: Application,
) -> None:
    vault = app.vault("DelegateInterruptedHandoffVault")
    app.write(vault, "notes/1.md", "FIRST_CHILD_EVIDENCE")
    app.write(vault, "notes/2.md", "SECOND_CHILD_EVIDENCE")
    await app.start()
    from core.runtime.state import get_runtime_context

    with checkpoint_retry_provider(app) as server:
        result = await invoke_delegate(
            vault,
            "delegate-interrupted-handoff",
            {
                "prompt": "Read two notes.",
                "model": "code-tool-model",
                "tools": ["file_read"],
            },
        )
        assert result.metadata["status"] == "failed"
        assert result.metadata["retryable"] is True
        assert len(server.requests) == 3
        audit = result.metadata["audit"]
        assert (
            audit["tool_call_count"] == 2
            and audit["settled_tool_call_count"] == 2
            and audit["unsettled_tool_call_count"] == 0
        )
        assert [row["tool"] for row in audit["tool_calls"]] == [
            "file_read",
            "file_read",
        ]
        assert "FIRST_CHILD_EVIDENCE" in json.dumps(
            audit
        ) and "SECOND_CHILD_EVIDENCE" in json.dumps(audit)
        assert isinstance(result.metadata["handoff_references"], list)
        tasks = await get_runtime_context().task_coordinator.list_tasks(kind="delegate")
        assert len(tasks) == 1 and tasks[0].is_terminal
        activity = app.request("/api/system/activity-log", params={"limit": 1000})
        assert activity.status_code == 200
        failures = [
            row["data"]
            for row in activity.json()["entries"]
            if row.get("data", {}).get("event") == "delegate_failed"
            and row["data"].get("task_id") == tasks[0].task_id
        ]
        assert len(failures) == 1 and failures[0]["status"] == "failed"
