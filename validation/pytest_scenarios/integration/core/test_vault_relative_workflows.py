"""Real application behavior staged for the pytest migration."""

from __future__ import annotations

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.workflows import (
    CONTEXT_TEMPLATE,
    FAILING_WORKFLOW,
    ORDINARY_MARKDOWN,
    PROJECT_WORKFLOW,
    SCHEDULED_CONTROL_WORKFLOW,
)

pytestmark = pytest.mark.scenario


async def test_vault_relative_workflows(app: Application) -> None:
    from pydantic_ai.messages import ToolReturn

    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.tools.workflow_run import WorkflowRun

    vault = app.vault("VaultRelativeWorkflowVault")
    workflow_path = "Research/Forest/automation/process-library.md"
    app.write(vault, workflow_path, PROJECT_WORKFLOW)
    app.write(
        vault, "AssistantMD/Authoring/scheduled_control.md", SCHEDULED_CONTROL_WORKFLOW
    )
    app.write(vault, "Research/Forest/automation/context.md", CONTEXT_TEMPLATE)
    app.write(vault, "Research/Forest/notes/code-example.md", ORDINARY_MARKDOWN)
    failing_workflow_path = "Research/Forest/automation/failing-workflow.md"
    app.write(vault, failing_workflow_path, FAILING_WORKFLOW)
    outside_workflow = app.root / "outside-workflow.md"
    outside_workflow.write_text(PROJECT_WORKFLOW, encoding="utf-8")
    (vault / "Research/escape.md").symlink_to(outside_workflow)
    await app.start()
    tool = WorkflowRun.get_tool(str(vault))
    listing = await tool.function(operation="list")
    assert (
        workflow_path not in listing
    ), "Vault-relative workflows should not enter managed discovery"
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        run_out = await tool.function(operation="run", workflow_path=workflow_path)
    assert (
        "success: True" in run_out
    ), "An explicit vault-relative workflow should run successfully"
    assert (
        f"workflow_path: {workflow_path}" in run_out
    ), "The workflow result should identify its vault-relative source path"
    assert (
        vault / "Research/Forest/library-processed.md"
    ).is_file(), "The vault-relative workflow should retain normal tool capabilities"
    app.events.require("workflow_task_completed", **{"workflow_path": workflow_path})
    workflow_id = f"{vault.name}/@path:{workflow_path}"
    history_response = await app.request_async(
        "/api/workflows/runs", params={"global_id": workflow_id}
    )
    assert history_response.status_code == 200, history_response.text
    history = history_response.json()["runs"]
    assert (
        history and history[0]["workflow_name"] == f"@path:{workflow_path}"
    ), "Durable workflow history should retain the explicit source path"
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        start_out = await tool.function(operation="start", workflow_path=workflow_path)
    start_data = app.parse_result(start_out)
    task_id = start_data.get("task_id", "")
    assert bool(task_id), "Path-based start should return a task id"
    task = await app.wait_task(task_id)
    assert (
        task.get("status") == "completed"
    ), "A path-based background workflow should complete normally"
    assert (
        task.get("metadata", {}).get("workflow_path") == workflow_path
    ), "Execution task metadata should retain the workflow source path"
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        failing_start_out = await tool.function(
            operation="start", workflow_path=failing_workflow_path
        )
    failing_task_id = app.parse_result(failing_start_out).get("task_id", "")
    assert bool(
        failing_task_id
    ), "A failing path-based background workflow should return a task id"
    failing_task = await app.wait_task(failing_task_id)
    assert (
        failing_task.get("metadata", {})
        .get("workflow_failure", {})
        .get("workflow_path")
        == failing_workflow_path
    ), "Structured workflow failures should retain the workflow source path"
    app.events.require(
        "workflow_task_failed", **{"workflow_path": failing_workflow_path}
    )
    invalid_requests = (
        (
            {"workflow_name": "managed", "workflow_path": workflow_path},
            "either workflow_name or workflow_path",
        ),
        ({"workflow_path": "../outside-workflow.md"}, "Path traversal"),
        ({"workflow_path": "Research/escape.md"}, "Path escapes vault boundaries"),
        (
            {"workflow_path": "Research/Forest/automation/context.md"},
            "run_type: workflow",
        ),
        (
            {"workflow_path": "Research/Forest/notes/code-example.md"},
            "run_type: workflow",
        ),
    )
    for request, expected_message in invalid_requests:
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            result = await tool.function(operation="run", **request)
        assert isinstance(
            result, ToolReturn
        ), f"Invalid path request should return a structured failure: {request}"
        assert (
            expected_message in result.return_value
        ), f"Invalid path request should explain its boundary: {request}"
    unsupported = await tool.function(operation="list", workflow_path=workflow_path)
    assert (
        isinstance(unsupported, ToolReturn)
        and "only supported for run and start" in unsupported.return_value
    ), "Non-execution operations should reject workflow_path explicitly"
    status_response = await app.request_async("/api/status")
    assert status_response.status_code == 200, status_response.text
    scheduler = status_response.json()["scheduler"]
    assert scheduler["running"] is True
    assert scheduler["total_jobs"] == len(scheduler["job_details"])
    scheduler_job_ids = {job["id"] for job in scheduler["job_details"]}
    assert (
        f"{vault.name}__scheduled_control" in scheduler_job_ids
    ), "The scheduler API must expose the real scheduled positive control"
    assert all(
        "@path:" not in job_id for job_id in scheduler_job_ids
    ), "Explicit vault-relative workflows should not become scheduled jobs"
