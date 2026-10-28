"""Workflow tasks and deterministic chat endpoints through the real API."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.api_endpoint_inputs import (
    API_SYSTEM_PROBE_WORKFLOW,
    SKIPPED_PROBE_WORKFLOW,
    STATUS_PROBE_WORKFLOW,
)
from validation.scenarios.support.chat_tasks import chat_turn

pytestmark = pytest.mark.scenario


async def test_workflow_api_task_status_and_system_scoping(app: Application) -> None:
    vault = app.vault("IntegrationApiVault")
    app.write(vault, "AssistantMD/Authoring/status_probe.md", STATUS_PROBE_WORKFLOW)
    app.write(vault, "AssistantMD/Authoring/skipped_probe.md", SKIPPED_PROBE_WORKFLOW)
    app.controller.prepare()
    path = app.root / "system/Authoring/api-system-probe.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(API_SYSTEM_PROBE_WORKFLOW, encoding="utf-8")
    await app.start()
    status = app.request("/api/status")
    assert (
        status.status_code == 200
        and status.json()["total_vaults"] == 1
        and status.json()["total_workflows"] == 2
    )
    assert app.request("/api/vaults/rescan", "POST").status_code == 200
    for name, expected, reason in (
        ("status_probe", "completed", None),
        ("skipped_probe", "skipped", "status-probe-skipped"),
    ):
        response = await app.request_async(
            "/api/workflows/execute", "POST", {"global_id": f"{vault.name}/{name}"}
        )
        assert response.status_code == 200 and response.json()["success"] is True
        task_id = response.json()["task"]["task_id"]
        task = await app.wait_task(task_id)
        assert task["status"] == expected
        if reason:
            assert task["terminal_reason"] == reason
        else:
            assert task["metadata"]["workflow_result"]["status"] == "completed"
        assert app.request(f"/api/tasks/{task_id}").json()["task_id"] == task_id
        assert any(
            item["task_id"] == task_id
            for item in app.request("/api/tasks").json()["tasks"]
        )
        assert any(
            item["task_id"] == task_id
            for item in app.request(
                "/api/workflows/tasks", params={"vault_name": vault.name}
            ).json()["tasks"]
        )
        cancel = app.request(f"/api/tasks/{task_id}/cancel", "POST")
        assert (
            cancel.status_code == 200
            and cancel.json()["cancelled"] is False
            and cancel.json()["task"]["status"] == expected
        )
    checkpoint = app.events.checkpoint()
    response = await app.request_async(
        "/api/workflows/execute",
        "POST",
        {"global_id": "system/api-system-probe", "vault_name": vault.name},
    )
    assert response.status_code == 200
    task_id = response.json()["task"]["task_id"]
    task = await app.wait_task(task_id)
    workflow_id = f"{vault.name}/system/api-system-probe"
    assert (
        task["status"],
        task["label"],
        task["metadata"]["workflow_id"],
        task["metadata"]["workflow_result"]["status"],
    ) == ("completed", workflow_id, workflow_id, "completed")
    app.events.require(
        after=checkpoint,
        name="workflow_task_started",
        fields={"workflow_id": workflow_id, "task_id": task_id},
    )
    assert app.request("/api/chat/sessions/not-active/active-task").status_code == 404


async def test_chat_metadata_title_export_and_replay_api(app: Application) -> None:
    vault = app.vault("IntegrationApiVault")
    await app.start()
    metadata = app.request("/api/metadata")
    assert metadata.status_code == 200
    payload = metadata.json()
    assert (
        "default_model_thinking" in payload["settings"]
        and payload["settings"]["default_chat_mode"] == "inline_edit"
    )
    assert {"workflow_run", "session_ops"} <= {
        item["name"] for item in payload["tools"]
    }
    data = {
        "vault_name": vault.name,
        "prompt": "Say hello from integration test.",
        "model": "test",
        "thinking": "off",
    }
    first = await chat_turn(app, data)
    assert (
        first["start_response"].status_code == 200
        and first["terminal_event"]["event"] == "done"
    )
    session = first["session_id"]
    title = app.request(
        f"/api/chat/sessions/{session}/title",
        "PATCH",
        {"vault_name": vault.name, "title": "Exported Session"},
    )
    assert title.status_code == 200
    exported = app.request(
        f"/api/chat/sessions/{session}/export", "POST", {"vault_name": vault.name}
    )
    assert exported.status_code == 200
    from pathlib import Path

    markdown = Path(exported.json()["path"]).read_text(encoding="utf-8")
    assert markdown.startswith("---\n") and 'title: "Exported Session"' in markdown
    followup = await chat_turn(
        app,
        {
            **data,
            "session_id": session,
            "prompt": "Add another thought for history depth.",
        },
    )
    assert (
        followup["start_response"].status_code == 200
        and followup["terminal_event"]["event"] == "done"
    )
    stream = await chat_turn(app, {**data, "prompt": "Stream a short response."})
    assert (
        stream["start_response"].status_code == 200
        and stream["terminal_event"]["event"] == "done"
    )
    task_id = stream["task"]["task_id"]
    replay = app.request(f"/api/chat/tasks/{task_id}/replay-snapshot")
    assert replay.status_code == 200
    events = replay.json()["events"]
    assert any(event["event"] == "done" for event in events) and not any(
        event["event"] == "error" for event in events
    )
    assert (
        app.request(
            f"/api/chat/tasks/{task_id}/events",
            params={"after_sequence": replay.json()["latest_sequence"]},
        ).status_code
        == 200
    )
    started = await app.request_async(
        "/api/chat/tasks",
        "POST",
        {
            **data,
            "session_id": "api-task-stream-session",
            "prompt": "Start a task-owned stream.",
        },
    )
    assert (
        started.status_code == 200
        and started.json()["session_id"] == "api-task-stream-session"
        and started.json()["task"]["task_id"]
    )
    await app.wait_task(started.json()["task"]["task_id"])
