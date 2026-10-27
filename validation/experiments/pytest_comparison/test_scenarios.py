"""Four independent pytest ports; original scenarios remain authoritative during comparison."""

from __future__ import annotations

from .application import Application


async def test_extensionless_file_edits(app: Application) -> None:
    from core.vault_state.file_operations import (
        edit_vault_line_operation,
        overwrite_vault_file_operation,
        replace_text_vault_file_operation,
    )

    vault = app.vault("FileOpsExtensionlessTextMutationsVault")
    app.write(vault, "notes/edit-target.md", "first\nreplace me\nlast\n")
    app.write(vault, "notes/no-extension", "alpha\n")
    app.write(vault, "notes/crlf-target.md", "first\r\nreplace me\r\nlast\r\n")
    await app.start()
    edited = edit_vault_line_operation(
        vault_path=str(vault),
        path="notes/edit-target",
        line_number=2,
        old_text="replace me",
        new_text="changed",
    )
    assert (
        edited.metadata.get("status") == "completed"
    ), "Extensionless edit_line should resolve to the existing markdown file"
    assert (
        edited.metadata.get("path") == "notes/edit-target.md"
    ), "edit_line metadata should report the effective markdown path"
    assert (
        edited.metadata.get("requested_path") == "notes/edit-target"
    ), "edit_line metadata should preserve the requested extensionless path"
    assert (vault / "notes" / "edit-target.md").read_text(
        encoding="utf-8"
    ) == "first\nchanged\nlast\n", (
        "edit_line should mutate the markdown file selected by extensionless resolution"
    )
    crlf_edit = edit_vault_line_operation(
        vault_path=str(vault),
        path="notes/crlf-target.md",
        line_number=2,
        old_text="replace me",
        new_text="changed",
    )
    assert (
        crlf_edit.metadata.get("status") == "completed"
    ), "edit_line should compare CRLF lines without requiring a trailing carriage return"
    assert (
        vault / "notes" / "crlf-target.md"
    ).read_bytes() == b"first\r\nchanged\r\nlast\r\n", (
        "edit_line should preserve CRLF line endings when replacing a line"
    )
    replaced = replace_text_vault_file_operation(
        vault_path=str(vault),
        path="notes/edit-target",
        old_text="changed",
        new_text="done",
        count=1,
    )
    assert (
        replaced.metadata.get("path") == "notes/edit-target.md"
    ), "replace_text metadata should report the effective markdown path"
    assert (vault / "notes" / "edit-target.md").read_text(
        encoding="utf-8"
    ) == "first\ndone\nlast\n", "replace_text should mutate the markdown file selected by extensionless resolution"
    truncated = overwrite_vault_file_operation(
        vault_path=str(vault), path="notes/edit-target", content=""
    )
    assert (
        truncated.metadata.get("path") == "notes/edit-target.md"
    ), "truncate metadata should report the effective markdown path"
    assert (vault / "notes" / "edit-target.md").read_text(
        encoding="utf-8"
    ) == "", (
        "truncate should clear the markdown file selected by extensionless resolution"
    )
    fallback = replace_text_vault_file_operation(
        vault_path=str(vault),
        path="notes/no-extension",
        old_text="alpha",
        new_text="beta",
        count=1,
    )
    assert (
        fallback.metadata.get("path") == "notes/no-extension"
    ), "Extensionless fallback should still allow an existing no-extension target when no .md file exists"
    assert (vault / "notes" / "no-extension").read_text(
        encoding="utf-8"
    ) == "beta\n", "Extensionless fallback should mutate the original target when no markdown sibling exists"


async def test_settings_editors(app) -> None:
    import json

    vault = app.vault("SettingsEditorsVault")
    custom_context = vault / "AssistantMD" / "Authoring" / "custom-context.md"
    custom_context.parent.mkdir(parents=True, exist_ok=True)
    custom_context.write_text(
        "---\nrun_type: context\n---\nCustom context\n", encoding="utf-8"
    )
    await app.start()
    strategy_response = app.request(
        "/api/system/settings/general/compaction_strategy",
        method="PUT",
        data={"value": "recovery_card"},
    )
    assert strategy_response.status_code == 200, strategy_response.text

    def settings():
        response = app.request("/api/system/settings/general")
        assert response.status_code == 200, response.text
        return {entry["key"]: entry for entry in response.json()}

    def update(key, value, status=200):
        response = app.request(
            f"/api/system/settings/general/{key}", method="PUT", data={"value": value}
        )
        assert response.status_code == status, response.text
        return response

    baseline = settings()
    assert baseline["debug"]["editor"]["kind"] == "boolean"
    assert baseline["max_concurrent_delegates"]["editor"]["maximum"] == 32
    assert baseline["default_model"]["editor"]["kind"] == "select"
    assert "sonnet" in [
        choice["value"] for choice in baseline["default_model"]["editor"]["choices"]
    ]
    assert baseline["disabled_tools"]["editor"]["kind"] == "multiselect"
    assert (
        baseline["ingestion_pdf_default_strategies"]["editor"]["kind"] == "ordered_list"
    )
    assert baseline["vault_state_excluded_patterns"]["editor"]["kind"] == "string_list"
    assert all(not entry["issues"] for entry in baseline.values()), baseline
    update("default_context_script", "")
    assert not settings()["default_context_script"]["issues"]
    update("default_context_script", "custom-context.md")
    assert not settings()["default_context_script"]["issues"]
    custom_context.unlink()
    assert settings()["default_context_script"]["issues"]
    update("default_context_script", "default.md")
    restart = update("debug", "false").json()
    assert restart["restart_required"] is True
    assert restart["editor"]["kind"] == "boolean"
    update("compaction_high_watermark_tokens", "1")
    update("compaction_strategy", "session_map", 400)
    update("compaction_high_watermark_tokens", "80000")
    update("compaction_strategy", "session_map")
    for key, raw in [
        ("chat_tool_calls_limit", "1.5"),
        ("chat_tool_calls_limit", "-1"),
        ("model_stream_retry_base_delay_seconds", "NaN"),
        ("model_stream_retry_max_delay_seconds", "Infinity"),
        ("model_stream_retries", "6"),
        ("default_chat_mode", "typo"),
        ("default_model", "removed-model"),
        ("default_model", "jev"),
        ("compaction_author_model", "jev"),
        ("disabled_tools", '["does-not-exist"]'),
        ("disabled_tools", '["file_read","file_read"]'),
        ("vault_state_excluded_patterns", "[42]"),
        ("ingestion_pdf_default_strategies", "[]"),
        ("ingestion_pdf_default_strategies", '["typo"]'),
        ("compaction_low_watermark_tokens", "90000"),
    ]:
        before = app.configuration()
        update(key, raw, 400)
        assert app.configuration() == before
    update("persist_model_reasoning_parts", "true")
    update("model_stream_retry_base_delay_seconds", "0.5")
    update("disabled_tools", '["file_read"]')
    update("disabled_tools", "[]")
    update("ingestion_pdf_default_strategies", '["pdf_text","pdf_ocr"]')
    update("compaction_author_model", "none")
    persisted = app.configuration()
    assert persisted["settings"]["persist_model_reasoning_parts"]["value"] is True
    assert (
        persisted["settings"]["model_stream_retry_base_delay_seconds"]["value"] == 0.5
    )
    assert persisted["settings"]["ingestion_pdf_default_strategies"]["value"] == [
        "pdf_text",
        "pdf_ocr",
    ]
    persisted["settings"]["default_model"]["value"] = "retired-alias"
    persisted["settings"]["max_concurrent_delegates"]["value"] = "broken"
    persisted["settings"]["default_chat_mode"]["value"] = "typo"
    persisted["settings"]["vault_state_excluded_patterns"]["value"] = [42]
    app.install_configuration(persisted)
    invalid = settings()
    for key in (
        "default_model",
        "max_concurrent_delegates",
        "default_chat_mode",
        "vault_state_excluded_patterns",
    ):
        assert invalid[key]["issues"], key
    assert not invalid["compaction_author_model"]["issues"]
    update("chat_tool_calls_limit", "2")
    assert settings()["default_model"]["value"] == "retired-alias"
    update("max_concurrent_delegates", "4")
    assert not settings()["max_concurrent_delegates"]["issues"]
    update("default_model", "sonnet")
    update("default_chat_mode", "normal")
    update("vault_state_excluded_patterns", json.dumps(["**/custom/**"]))
    assert all(not entry["issues"] for entry in settings().values())
    persisted = app.configuration()
    persisted["settings"]["compaction_low_watermark_tokens"]["value"] = 100000
    app.install_configuration(persisted)
    invalid = settings()
    for key in ("compaction_low_watermark_tokens", "compaction_high_watermark_tokens"):
        assert any(issue["code"] == "relationship" for issue in invalid[key]["issues"])
    update("chat_tool_calls_limit", "3")
    update("compaction_low_watermark_tokens", "90000")
    update("compaction_low_watermark_tokens", "110000", 400)
    update("compaction_high_watermark_tokens", "120000")
    update("compaction_low_watermark_tokens", "20000")
    update("compaction_high_watermark_tokens", "80000")
    persisted = app.configuration()
    persisted["models"]["sonnet"]["model_string"] = "upgraded-model-id"
    app.install_configuration(persisted)
    assert not settings()["default_model"]["issues"]
    saved_model = persisted["models"].pop("sonnet")
    app.install_configuration(persisted)
    assert settings()["default_model"]["issues"]
    update("default_model", "sonnet", 400)
    persisted["models"]["sonnet"] = saved_model
    app.install_configuration(persisted)
    assert not settings()["default_model"]["issues"]
    persisted["models"] = {}
    app.install_configuration(persisted)
    empty_models = settings()
    assert empty_models["default_model"]["issues"]
    assert [
        choice["value"] for choice in empty_models["default_model"]["editor"]["choices"]
    ] == ["none"]
    assert not empty_models["compaction_author_model"]["issues"]
    persisted["tools"].pop("file_read")
    app.install_configuration(persisted)
    tool_choices = settings()["disabled_tools"]["editor"]["choices"]
    assert "file_read" in [choice["value"] for choice in tool_choices]
    update("disabled_tools", '["file_read"]')
    assert not settings()["disabled_tools"]["issues"]


async def test_vault_relative_workflows(app: Application) -> None:
    from pydantic_ai.messages import ToolReturn

    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.runtime.state import get_runtime_context
    from core.tools.workflow_run import WorkflowRun

    vault = app.vault("VaultRelativeWorkflowVault")
    workflow_path = "Research/Forest/automation/process-library.md"
    app.write(vault, workflow_path, PROJECT_WORKFLOW)
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
    history = get_runtime_context().workflow_run_store.list_runs(workflow_id)
    assert (
        history[0].workflow_name if history else None
    ) == f"@path:{workflow_path}", (
        "Durable workflow history should retain the explicit source path"
    )
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
    scheduler_job_ids = {
        str(job.id) for job in get_runtime_context().scheduler.get_jobs()
    }
    assert all(
        "@path:" not in job_id for job_id in scheduler_job_ids
    ), "Explicit vault-relative workflows should not become scheduled jobs"


PROJECT_WORKFLOW = '---\nrun_type: workflow\nenabled: false\ndescription: Project-local research workflow\n---\n```python\nawait file_write(\n    operation="write",\n    path="Research/Forest/library-processed.md",\n    content="processed\\n",\n)\nawait finish(status="completed", reason="project-workflow-complete")\n```\n'
CONTEXT_TEMPLATE = '---\nrun_type: context\ndescription: Context template that must not run by path\n---\n```python\nawait assemble_context(instructions=["not executable as a workflow"])\n```\n'
ORDINARY_MARKDOWN = '# Example\n\nThis note contains Python documentation but is not a workflow.\n\n```python\nprint("example")\n```\n'
FAILING_WORKFLOW = '---\nrun_type: workflow\ndescription: Project-local workflow that fails deterministically\n---\n```python\nraise RuntimeError("expected project workflow failure")\n```\n'


async def test_async_cancellation_rollback(app: Application) -> None:
    import asyncio
    from dataclasses import replace
    from datetime import UTC, datetime, timedelta
    from pathlib import Path

    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.runtime.state import get_runtime_context
    from core.tools.workflow_run import WorkflowRun

    vault = app.vault("WorkflowRunAsyncVault")
    app.write(vault, "AssistantMD/Authoring/async_probe.md", ASYNC_PROBE_WORKFLOW)
    await app.start()
    tool = WorkflowRun.get_tool(str(vault))
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        start_out = await tool.function(operation="start", workflow_name="async_probe")
    start_data = app.parse_result(start_out)
    task_id = start_data.get("task_id", "")
    assert start_data.get("success") == "True", "workflow_run start should succeed"
    assert bool(task_id), "workflow_run start should return a task id"
    created_path = Path(vault) / "notes/workflow-run-async-write.md"
    for _ in range(50):
        if created_path.exists():
            break
        await asyncio.sleep(0.05)
    assert (
        created_path.exists()
    ), "Started workflow should write a file before cancellation"
    status_out = await tool.function(operation="status", task_id=task_id)
    status_data = app.parse_result(status_out)
    assert status_data.get("success") == "True", "workflow_run status should succeed"
    assert (
        status_data.get("task_id") == task_id
    ), "workflow_run status should report the same task"
    assert (
        status_data.get("heartbeat_status") == "workflow_running"
    ), "workflow_run status should expose the latest workflow heartbeat"
    assert (
        status_data.get("heartbeat_age_seconds") != ""
    ), "workflow_run status should expose heartbeat age"
    assert (
        status_data.get("heartbeat_stale") == "False"
    ), "freshly running workflow should not be marked stale"
    app.events.require(
        "execution_task_heartbeat",
        **{"task_id": task_id, "heartbeat_status": "workflow_running"},
    )
    stale_status_out = WorkflowRun._format_task_status(
        replace(
            await get_runtime_context().task_coordinator.get_task(task_id),
            last_heartbeat_at=datetime.now(UTC) - timedelta(seconds=120),
        )
    )
    stale_status_data = app.parse_result(stale_status_out)
    assert (
        stale_status_data.get("heartbeat_stale") == "True"
    ), "workflow_run status should mark old heartbeat data stale"
    checkpoint = app.events.checkpoint()
    cancel_out = await tool.function(operation="cancel", task_id=task_id)
    cancel_data = app.parse_result(cancel_out)
    assert (
        cancel_data.get("success") == "True"
    ), "workflow_run cancel should be effective"
    assert (
        cancel_data.get("task_id") == task_id
    ), "workflow_run cancel should report the same task"
    task = await app.wait_task(task_id)
    assert task.get("status") == "cancelled", "Workflow task should be cancelled"
    assert (
        not created_path.exists()
    ), "Cancelled workflow_run task should rollback files created before cancellation"
    app.events.require(
        "task_rollback_completed",
        after=checkpoint,
        **{"task_id": task_id, "terminal_status": "cancelled"},
    )


ASYNC_PROBE_WORKFLOW = '---\nrun_type: workflow\nenabled: false\ndescription: Async workflow_run probe\n---\n```python\nawait file_write(\n    operation="write",\n    path="notes/workflow-run-async-write.md",\n    content="created before cancellation\\n",\n)\nwhile True:\n    await file_read(operation="list", path=".")\n```\n'
