"""Workflow file mutations persist task snapshots, manifests and activity."""

from __future__ import annotations

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.vault_mutation_inputs import (
    WRITE_PROBE_WORKFLOW,
    seed_chat_history,
)
from validation.pytest_scenarios.support.vault_recovery import query_rows, run_workflow

pytestmark = pytest.mark.scenario


async def test_workflow_mutations_record_snapshots_and_activity(
    app: Application,
) -> None:
    vault = app.vault("VaultStateMutationVault")
    for path, content in {
        "notes/preexisting-append.md": "Original line\n",
        "notes/preexisting-delete.md": "Delete original\n",
        "assets/delete-target.pdf": "Delete attachment\n",
        "assets/move-source.pdf": "Move attachment\n",
        "assets/overwrite-source.pdf": "Overwrite attachment source\n",
        "assets/overwrite-destination.pdf": "Overwrite attachment destination\n",
        "AssistantMD/Authoring/write_probe.md": WRITE_PROBE_WORKFLOW,
    }.items():
        app.write(vault, path, content)
    await app.start()
    checkpoint = app.events.checkpoint()
    result = await run_workflow(app, vault, "write_probe", expect_failure=False)
    assert result.status == "completed"
    event = app.events.require(
        after=checkpoint,
        name="vault_mutation_recorded",
        fields={
            "vault_name": vault.name,
            "path": "notes/created-by-workflow.md",
            "operation": "write",
            "before_exists": False,
            "after_exists": True,
        },
    )
    task_id, vault_id = event["data"]["task_id"], event["data"]["vault_id"]
    app.events.require(
        after=checkpoint,
        name="snapshot_set_created",
        fields={
            "task_id": task_id,
            "vault_id": vault_id,
            "vault_name": vault.name,
            "purpose": "rollback",
        },
    )
    app.events.require(
        after=checkpoint,
        name="vault_file_snapshot_recorded",
        fields={
            "task_id": task_id,
            "vault_id": vault_id,
            "path": "notes/created-by-workflow.md",
            "before_exists": False,
        },
    )
    rows = query_rows(
        app,
        "SELECT a.task_id, a.kind AS task_kind, a.source AS task_source, a.label AS task_label, a.vault_id, m.path, m.operation, m.related_path, m.event_sequence, m.before_exists, m.before_snapshot_id, m.after_exists, m.after_hash, m.snapshot_ref, m.expires_at FROM vault_mutations AS m JOIN vault_activities AS a ON a.activity_id = m.activity_id WHERE a.task_id = ? ORDER BY m.id",
        (task_id,),
    )
    expected = {
        ("write", "notes/created-by-workflow.md"),
        ("append", "notes/preexisting-append.md"),
        ("edit_line", "notes/edit-target.md"),
        ("replace_text", "notes/replace-target.md"),
        ("write", "notes/truncate-target.md"),
        ("delete", "notes/preexisting-delete.md"),
        ("move", "notes/move-source.md"),
        ("move", "notes/move-destination.md"),
        ("move", "notes/overwrite-source.md"),
        ("move", "notes/overwrite-destination.md"),
        ("delete", "assets/delete-target.pdf"),
        ("move", "assets/move-source.pdf"),
        ("move", "assets/move-destination.pdf"),
        ("move", "assets/overwrite-source.pdf"),
        ("move", "assets/overwrite-destination.pdf"),
    }
    assert expected <= {(row["operation"], row["path"]) for row in rows}
    created = next(row for row in rows if row["path"] == "notes/created-by-workflow.md")
    assert (
        created["task_id"],
        created["task_kind"],
        created["task_source"],
        created["task_label"],
        created["vault_id"],
        created["before_exists"],
        created["after_exists"],
        created["snapshot_ref"],
    ) == (task_id, "workflow", "api", f"{vault.name}/write_probe", vault_id, 0, 1, None)
    assert all(
        created[key]
        for key in ("after_hash", "event_sequence", "expires_at", "before_snapshot_id")
    )
    append = next(row for row in rows if row["operation"] == "append")
    assert (
        append["before_exists"] == 1
        and append["before_snapshot_id"]
        and append["snapshot_ref"]
    )
    deleted = next(row for row in rows if row["path"] == "notes/preexisting-delete.md")
    assert deleted["after_exists"] == 0 and deleted["snapshot_ref"]
    assert (
        next(row for row in rows if row["path"] == "assets/delete-target.pdf")[
            "after_exists"
        ]
        == 0
    )
    pairs = {
        f"{directory}/{source}.{ext}": f"{directory}/{destination}.{ext}"
        for directory, ext in (("notes", "md"), ("assets", "pdf"))
        for source, destination in (
            ("move-source", "move-destination"),
            ("move-destination", "move-source"),
            ("overwrite-source", "overwrite-destination"),
            ("overwrite-destination", "overwrite-source"),
        )
    }
    assert {
        row["path"]: row["related_path"] for row in rows if row["operation"] == "move"
    } == pairs
    assert all(
        row["related_path"] is None for row in rows if row["operation"] != "move"
    )
    assert not (vault / "assets/delete-target.pdf").exists()
    assert (
        not (vault / "assets/move-source.pdf").exists()
        and (vault / "assets/move-destination.pdf").exists()
    )
    assert not (vault / "assets/overwrite-source.pdf").exists()
    assert (
        vault / "assets/overwrite-destination.pdf"
    ).read_text() == "Overwrite attachment source\n"
    response = app.request(f"/api/vaults/{vault.name}/activity", params={"limit": 5})
    assert response.status_code == 200
    group = next(
        group for group in response.json()["groups"] if group["task_id"] == task_id
    )
    assert (
        group["status"],
        group["activity_kind"],
        group["task_kind"],
        group["task_source"],
        group["task_label"],
        group["mutation_count"],
    ) == (
        "completed",
        "workflow",
        "workflow",
        "api",
        f"{vault.name}/write_probe",
        len(rows),
    )
    mutations = group["mutations"]
    assert len(mutations) == len(rows) and expected <= {
        (m["operation"], m["path"]) for m in mutations
    }
    assert all(m["event_sequence"] is not None for m in mutations)
    assert {
        m["path"]: m["related_path"] for m in mutations if m["operation"] == "move"
    } == pairs
    manifest = query_rows(
        app,
        "SELECT artifact_class, deleted_at FROM vault_files WHERE vault_id = ? AND path = ?",
        (vault_id, "notes/created-by-workflow.md"),
    )
    assert manifest == [{"artifact_class": "user_content", "deleted_at": None}]

    seed_chat_history(app, vault_id, vault.name)
    chat_response = app.request(
        f"/api/vaults/{vault.name}/activity", params={"limit": 5}
    )
    assert chat_response.status_code == 200
    groups = [
        group
        for group in chat_response.json()["groups"]
        if group.get("chat_session_id") == "validation-session"
    ]
    assert len(groups) == 2
    for group in groups:
        assert (
            group["activity_kind"],
            group["chat_session_id"],
            group["chat_session_title"],
            group["mutation_count"],
        ) == ("chat", "validation-session", "Validation Chat Title", 1)

    from core.identity import LOCAL_USER_AUTHORITY
    from core.runtime.execution_tasks import (
        ExecutionTaskKind,
        ExecutionTaskSource,
        workflow_vault_scope,
    )
    from core.runtime.state import get_runtime_context
    from core.vault_state.file_mutations import mutate_vault_file

    checkpoint = app.events.checkpoint()
    async with get_runtime_context().task_coordinator.track_current_task(
        authority=LOCAL_USER_AUTHORITY,
        kind=ExecutionTaskKind.WORKFLOW.value,
        scope=workflow_vault_scope(vault.name),
        source=ExecutionTaskSource.API.value,
        label=f"{vault.name}/mutation_failure_probe",
        metadata={"vault": vault.name},
    ):
        with pytest.raises(IsADirectoryError):
            mutate_vault_file(
                vault_path=vault,
                path="notes/failing-write.md",
                operation="write",
                mutator=lambda path: path.parent.write_text(
                    "failure", encoding="utf-8"
                ),
                create_parent=True,
            )
    app.events.require(
        after=checkpoint,
        name="vault_state_mutation_failed",
        fields={
            "vault_id": vault_id,
            "vault_name": vault.name,
            "path": "notes/failing-write.md",
            "operation": "write",
            "stage": "mutate",
            "before_exists": False,
            "error_type": "IsADirectoryError",
        },
    )
