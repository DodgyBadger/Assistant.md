"""Real workflow queueing and timeout outcomes through the application governor."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.workflow_runtime import (
    WAITING_WORKFLOW,
    configure_workflow_limit,
    execute_workflow,
    install_workflow_timeout,
    workflow_pair,
)

pytestmark = pytest.mark.scenario


@pytest.mark.parametrize(
    ("same_vault", "global_limit", "expected_active"),
    [(True, 0, 1), (False, 0, 2), (False, 1, 1)],
    ids=["same-vault-serial", "different-vaults-concurrent", "global-limit-serial"],
)
async def test_workflow_queue_capacity(
    app: Application, same_vault: bool, global_limit: int, expected_active: int
) -> None:
    vault_a = app.vault("VaultA")
    vault_b = app.vault("VaultB")
    for vault, name in ((vault_a, "first"), (vault_a, "second"), (vault_b, "first")):
        app.write(vault, f"AssistantMD/Authoring/{name}.md", WAITING_WORKFLOW)
    await app.start()
    configure_workflow_limit(app, "workflow_task_timeout_seconds", "0")
    configure_workflow_limit(app, "max_concurrent_workflows", str(global_limit))
    global_ids = ("VaultA/first", "VaultA/second" if same_vault else "VaultB/first")
    report = await workflow_pair(app, global_ids)
    assert report["statuses"] == ["completed", "completed"]
    assert report["max_active"] == expected_active
    if same_vault:
        assert report["timeline"].index("start:VaultA/second") > report[
            "timeline"
        ].index("end:VaultA/first")


async def test_workflow_timeout_metadata_and_history(app: Application) -> None:
    vault = app.vault("TimeoutVault")
    app.write(vault, "AssistantMD/Authoring/slow_probe.md", WAITING_WORKFLOW)
    await app.start()
    configure_workflow_limit(app, "max_concurrent_workflows", "0")
    install_workflow_timeout(app, seconds=0.01)
    result = await execute_workflow(app, "TimeoutVault/slow_probe")
    assert result["status"] == "timed_out"
    history = app.request(
        "/api/workflows/runs", params={"global_id": "TimeoutVault/slow_probe"}
    )
    assert history.status_code == 200
    assert history.json()["runs"][0]["status"] == "timed_out"
    tasks = app.request("/api/workflows/tasks", params={"vault_name": vault.name})
    assert tasks.status_code == 200
    task = tasks.json()["tasks"][0]
    assert task["status"] == "timed_out"
    assert task["terminal_reason"] == "workflow_task_timeout:0.01s"
    metadata = task["metadata"]
    assert isinstance(metadata["workflow_result"], dict)
    assert metadata["workflow_result"]["status"] == "timed_out"
    assert isinstance(metadata["workflow_failure"], dict)
    assert metadata["workflow_failure"]["failure_kind"] == "workflow_timeout"
    assert metadata["workflow_failure"]["retryable"] is False
