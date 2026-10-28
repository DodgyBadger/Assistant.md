"""Durable real workflow failures, scheduler outcomes, retention and dashboard projections."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.workflow_history_inputs import (
    DOMAIN_FAILURE_WORKFLOW,
    RAISED_FAILURE_WORKFLOW,
    TEMPLATE_INPUT,
    scheduler_failure_and_miss,
    seed_nonempty_output_path,
)

pytestmark = pytest.mark.scenario


async def test_workflow_run_history_matches_real_domain_and_scheduler_outcomes(
    app: Application,
) -> None:
    vault = app.vault("HistoryVault")
    app.write(
        vault, "AssistantMD/Authoring/nightly_cleanup.md", DOMAIN_FAILURE_WORKFLOW
    )
    app.write(vault, "AssistantMD/Authoring/raised_failure.md", RAISED_FAILURE_WORKFLOW)
    await app.start()
    from core.identity import LOCAL_USER_AUTHORITY
    from core.runtime.execution_tasks import ExecutionTaskSource
    from core.runtime.state import get_runtime_context
    from core.workflow_runs import WorkflowRunStore

    runtime = get_runtime_context()
    workflow_id = f"{vault.name}/nightly_cleanup"
    with pytest.raises(RuntimeError):
        await runtime.workflow_governor.execute_workflow(
            global_id=f"{vault.name}/missing_owner",
            source=ExecutionTaskSource.SCHEDULER,
        )
    checkpoint = app.events.checkpoint()
    result = await runtime.workflow_governor.execute_workflow(
        global_id=workflow_id,
        source=ExecutionTaskSource.SCHEDULER,
        authority=LOCAL_USER_AUTHORITY,
    )
    assert result.status == "failed"
    assert result.success is False
    tasks = [
        task
        for task in await runtime.task_coordinator.list_tasks(kind="workflow")
        if task.metadata.get("workflow_id") == workflow_id
    ]
    assert len(tasks) == 1 and tasks[0].status == "failed"
    store = WorkflowRunStore(str(app.root / "system"))
    latest = store.get_latest_run(workflow_id)
    assert latest is not None
    assert latest.owner_principal_id == "local-user"
    assert latest.status == "failed"
    assert latest.reason == "script used a removed tool"
    assert len(store.list_runs(workflow_id)) == 1
    app.events.require(
        "execution_task_failed",
        after=checkpoint,
        task_id=tasks[0].task_id,
        status="failed",
    )

    await scheduler_failure_and_miss(app, vault.name)
    raised = store.list_runs(f"{vault.name}/raised_failure")
    assert len(raised) == 1
    assert raised[0].status == "failed"
    after_miss = store.list_runs(workflow_id)
    assert [run.status for run in after_miss] == ["missed", "failed"]
    assert {run.owner_principal_id for run in after_miss} == {"local-user"}
    status = app.request("/api/status")
    assert status.status_code == 200, status.text
    details = [
        job
        for job in status.json()["scheduler"]["job_details"]
        if job["id"] == f"{vault.name}__nightly_cleanup"
    ]
    assert len(details) == 1
    assert details[0]["last_status"] == "missed"
    assert details[0]["last_run_source"] == "scheduler"
    history = app.request("/api/workflows/runs", params={"global_id": workflow_id})
    assert history.status_code == 200, history.text
    assert [row["status"] for row in history.json()["runs"]] == ["missed", "failed"]
    app.events.require("scheduler_job_missed", workflow_id=workflow_id, status="missed")
    app.events.require(
        "scheduler_job_error",
        workflow_id=f"{vault.name}/raised_failure",
        status="error",
    )

    # Production workflow execution currently returns no output paths. Exercise
    # the store's nonempty input separately without claiming governor propagation.
    output_input = seed_nonempty_output_path(store, vault.name)
    reloaded = WorkflowRunStore(str(app.root / "system")).get_run(output_input.run_id)
    assert reloaded is not None and reloaded.output_files == ("output.md",)
    app.evidence.save_json(
        "workflow-output-path-scope.json",
        {
            "path_input": list(reloaded.output_files),
            "parity_gap": "Nonempty governor result propagation remains unproved because actual workflow execution always returns output_files=[]",
        },
    )


async def test_workflow_history_retention_and_cross_vault_template_projection(
    app: Application,
) -> None:
    app.vault("RetentionVault")
    app.vault("FirstVault")
    app.vault("SecondVault")
    # These records and the template are explicit historical inputs. The test
    # proves retention/query/projection, not how earlier executions generated them.
    template_path = app.root / "system/Authoring/nightly-session-summarization.md"
    template_path.parent.mkdir(parents=True, exist_ok=True)
    template_path.write_text(TEMPLATE_INPUT, encoding="utf-8")
    await app.start()
    from core.runtime.state import get_runtime_context

    store = get_runtime_context().workflow_run_store
    old = store.record_terminal_run(
        workflow_id="RetentionVault/quiet_workflow",
        workflow_name="quiet_workflow",
        vault_name="RetentionVault",
        source="scheduler",
        owner_principal_id="local-user",
        status="completed",
        completed_at=datetime.now(UTC) - timedelta(days=120),
    )
    assert store.get_run(old.run_id) is not None
    store.record_terminal_run(
        workflow_id="RetentionVault/quiet_workflow",
        workflow_name="quiet_workflow",
        vault_name="RetentionVault",
        source="scheduler",
        owner_principal_id="local-user",
        status="completed",
    )
    assert store.get_run(old.run_id) is None
    store.record_terminal_run(
        workflow_id="FirstVault/system/nightly-session-summarization",
        workflow_name="system/nightly-session-summarization",
        vault_name="FirstVault",
        source="api",
        owner_principal_id="local-user",
        status="completed",
        completed_at=datetime.now(UTC) - timedelta(minutes=2),
    )
    store.record_terminal_run(
        workflow_id="SecondVault/system/nightly-session-summarization",
        workflow_name="system/nightly-session-summarization",
        vault_name="SecondVault",
        source="api",
        owner_principal_id="local-user",
        status="skipped",
        reason="no sessions pending",
    )
    template_history = store.list_runs_by_workflow_name(
        "system/nightly-session-summarization"
    )
    assert [run.vault_name for run in template_history] == ["SecondVault", "FirstVault"]
    status = app.request("/api/status")
    assert status.status_code == 200, status.text
    latest = status.json()["workflow_runs"]["system/nightly-session-summarization"]
    assert latest["status"] == "skipped"
    assert latest["vault_name"] == "SecondVault"
