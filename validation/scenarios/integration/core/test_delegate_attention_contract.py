"""Distinct real repeated failures create distinct delegate attention records."""

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.delegate_attention_provider import attention_provider
from validation.scenarios.support.delegate_inputs import invoke_delegate

pytestmark = pytest.mark.scenario


async def test_distinct_delegate_attention_events_are_correlated(
    app: Application,
) -> None:
    vault = app.vault("DelegateAttentionVault")
    app.write(vault, "notes/exists.md", "actual input")
    await app.start()
    from core.runtime.state import get_runtime_context

    changed = app.request(
        "/api/system/settings/general/delegate_repeated_failure_limit",
        method="PUT",
        data={"value": "1"},
    )
    assert changed.status_code == 200
    with attention_provider(app) as server:
        for session in ("delegate-attention-one", "delegate-attention-two"):
            result = await invoke_delegate(
                vault,
                session,
                {
                    "prompt": "Read the missing input.",
                    "model": "code-tool-model",
                    "tools": ["file_read"],
                },
            )
            assert result.metadata["status"] in {"completed", "failed"}
        assert len(server.requests) == 6
        jobs = await get_runtime_context().task_coordinator.list_tasks(kind="delegate")
        assert len(jobs) == 2 and all(job.is_terminal for job in jobs)
        activity = app.request("/api/system/activity-log", params={"limit": 1000})
        assert activity.status_code == 200
        ids = {job.task_id for job in jobs}
        rows = [
            row["data"]
            for row in activity.json()["entries"]
            if row.get("data", {}).get("event") == "delegate_attention_required"
            and row["data"].get("task_id") in ids
        ]
        assert len(rows) == 2 and {row["task_id"] for row in rows} == ids
        assert len({row["issue"] for row in rows}) == 2
        assert all(
            row["status"] == "attention_required"
            and row["reason"] == "repeated_tool_failure"
            for row in rows
        )
