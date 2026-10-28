"""Production recovery actions, controlled retention inputs, and SQL evidence."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from validation.scenarios.application import Application

if TYPE_CHECKING:
    from httpx2 import Response

    from core.authoring.workflow_execution import WorkflowExecutionResult
    from core.vault_state.activity_rollback import (
        ActivityRollbackResult,
        ActivityRollbackUnavailable,
    )
    from core.vault_state.rollback import TaskRollbackResult


async def run_workflow(
    app: Application, vault: Path, workflow_name: str, *, expect_failure: bool
) -> WorkflowExecutionResult:
    from core.identity import LOCAL_USER_AUTHORITY
    from core.runtime.execution_tasks import ExecutionTaskSource
    from core.runtime.state import get_runtime_context

    try:
        result = await get_runtime_context().workflow_governor.execute_workflow(
            global_id=f"{vault.name}/{workflow_name}",
            source=ExecutionTaskSource.API,
            authority=LOCAL_USER_AUTHORITY,
            expect_failure=expect_failure,
        )
    except Exception as exc:
        app.evidence.save_json(
            "workflow-exception.json", {"type": type(exc).__name__, "message": str(exc)}
        )
        raise
    app.evidence.save_json("workflow-result.json", result.to_dict())
    return result


def query_rows(
    app: Application, query: str, parameters: tuple[Any, ...] = ()
) -> list[dict[str, Any]]:
    connection = sqlite3.connect(app.root / "system" / "vault_state.db")
    connection.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in connection.execute(query, parameters)]
    finally:
        connection.close()
    app.evidence.append(
        "recovery-rows.jsonl", {"query": query, "parameters": parameters, "rows": rows}
    )
    return rows


def snapshot_status(app: Application, task_id: str) -> str | None:
    rows = query_rows(
        app,
        "SELECT status FROM snapshot_sets WHERE task_id = ? AND purpose = 'rollback'",
        (task_id,),
    )
    return str(rows[0]["status"]) if rows else None


def activity_status(app: Application, task_id: str) -> tuple[str, str | None] | None:
    rows = query_rows(
        app,
        "SELECT status, rollback_status FROM vault_activities WHERE task_id = ?",
        (task_id,),
    )
    return (rows[0]["status"], rows[0]["rollback_status"]) if rows else None


def mutation_rows(app: Application, task_id: str) -> list[dict[str, Any]]:
    return query_rows(
        app,
        "SELECT m.id FROM vault_mutations AS m JOIN vault_activities AS a ON a.activity_id = m.activity_id WHERE a.task_id = ?",
        (task_id,),
    )


def activity_rows(app: Application) -> list[dict[str, Any]]:
    return query_rows(app, "SELECT * FROM vault_activities")


def mutation_task_ids(app: Application) -> set[str]:
    return {
        str(row["task_id"])
        for row in query_rows(
            app,
            "SELECT a.task_id FROM vault_mutations AS m JOIN vault_activities AS a ON a.activity_id = m.activity_id",
        )
    }


def snapshot_task_ids(app: Application) -> set[str]:
    return {
        str(row["task_id"])
        for row in query_rows(app, "SELECT task_id FROM snapshot_sets")
    }


def rollback_task(
    app: Application, *, task_id: str, terminal_status: str, reason: str
) -> TaskRollbackResult:
    from core.vault_state.rollback import rollback_task_file_mutations

    try:
        result = rollback_task_file_mutations(
            task_id=task_id, terminal_status=terminal_status, reason=reason
        )
    except Exception as exc:
        app.evidence.append(
            "task-rollback-results.jsonl",
            {
                "task_id": task_id,
                "exception": type(exc).__name__,
                "code": getattr(exc, "code", None),
                "message": str(exc),
            },
        )
        raise
    app.evidence.append("task-rollback-results.jsonl", asdict(result))
    return result


def record_activity(
    app: Application,
    vault: Path,
    *,
    activity_id: str,
    kind: str,
    scope: str | None,
    label: str,
    mutations: list[tuple[str, str, str]],
) -> None:
    from core.vault_state.activity import VaultActivityContext, use_vault_activity
    from core.vault_state.file_mutations import (
        replace_vault_file_content,
        write_vault_file,
    )
    from core.vault_state.service import VaultStateService

    context = VaultActivityContext(
        activity_id=activity_id, kind=kind, source="api", scope=scope, label=label
    )
    with use_vault_activity(context):
        for operation, path, content in mutations:
            if operation == "write":
                result = write_vault_file(vault_path=vault, path=path, content=content)
            else:
                result = replace_vault_file_content(
                    vault_path=vault, path=path, content=content, operation=operation
                )
            app.evidence.append("activity-input-mutations.jsonl", asdict(result))
    VaultStateService().finish_activity(activity_id=activity_id, status="completed")


def preview_rollback(
    app: Application, vault_name: str, activity_id: str
) -> dict[str, Any]:
    response = app.request(f"/api/vaults/{vault_name}/activity/{activity_id}/rollback")
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def execute_rollback(
    app: Application, vault_name: str, activity_id: str, preview: dict[str, Any]
) -> Response:
    return app.request(
        f"/api/vaults/{vault_name}/activity/{activity_id}/rollback",
        method="POST",
        data={
            "expected_states": [
                {
                    "path": item["path"],
                    "exists": item["expected_exists"],
                    "sha256": item["expected_sha256"],
                }
                for item in preview["paths"]
            ]
        },
    )


class ConflictFixtureFailure(Exception):
    """End a real tracked task before its explicit conflict rollback attempt."""


@asynccontextmanager
async def conflicting_task(app: Application, vault: Path) -> AsyncIterator[str]:
    from core.identity import LOCAL_USER_AUTHORITY
    from core.runtime.state import get_runtime_context
    from core.vault_state.file_mutations import append_vault_file, write_vault_file

    task_id = ""
    try:
        async with get_runtime_context().task_coordinator.track_current_task(
            kind="workflow",
            scope=f"workflow_vault:{vault.name}",
            source="api",
            label="Atomic task rollback conflict",
            authority=LOCAL_USER_AUTHORITY,
        ) as task:
            task_id = task.task_id
            append_vault_file(
                vault_path=vault,
                path="notes/preexisting-append.md",
                content="mutated append\n",
            )
            write_vault_file(
                vault_path=vault,
                path="notes/created-before-failure.md",
                content="created then rolled back\n",
            )
            yield task_id
            raise ConflictFixtureFailure("Recorded task fails before explicit rollback")
    except ConflictFixtureFailure:
        app.evidence.step("conflict_task_failed", task_id=task_id)


async def submit_duplicate_rollbacks(
    app: Application, vault: Path, activity_id: str
) -> list[ActivityRollbackResult | ActivityRollbackUnavailable]:
    from core.vault_state.activity_rollback import (
        ActivityRollbackUnavailable,
        execute_activity_rollback,
        preview_activity_rollback,
    )

    preview = preview_activity_rollback(vault_path=vault, activity_id=activity_id)
    states = tuple(
        (item.path, item.expected_exists, item.expected_sha256)
        for item in preview.paths
    )
    barrier = threading.Barrier(2, timeout=5)

    def execute() -> ActivityRollbackResult | ActivityRollbackUnavailable:
        barrier.wait()
        try:
            result = execute_activity_rollback(
                vault_path=vault, activity_id=activity_id, expected_states=states
            )
        except ActivityRollbackUnavailable as exc:
            app.evidence.append(
                "duplicate-rollbacks.jsonl",
                {"exception": type(exc).__name__, "message": str(exc)},
            )
            return exc
        app.evidence.append("duplicate-rollbacks.jsonl", asdict(result))
        return result

    return list(
        await asyncio.gather(asyncio.to_thread(execute), asyncio.to_thread(execute))
    )


def prepare_cleanup_state(
    app: Application, vault: Path, *, expired_at: datetime, retained_at: datetime
) -> tuple[Path, Path]:
    from core.vault_state import VaultStateService

    refresh = VaultStateService().refresh_vault(vault)
    system_root = app.root / "system"
    expired_root = system_root / "vault_snapshots" / "9001"
    retained_root = system_root / "vault_snapshots" / "9002"
    expired_file = expired_root / "files/notes/expired.md"
    retained_file = retained_root / "files/notes/retained.md"
    for path, content in [
        (expired_file, "expired snapshot\n"),
        (retained_file, "retained snapshot\n"),
    ]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    insert_cleanup_rows(
        app,
        vault_id=refresh.vault_id,
        vault_name=vault.name,
        expired_at=expired_at,
        retained_at=retained_at,
        expired_snapshot_root=expired_root,
        retained_snapshot_root=retained_root,
    )
    app.evidence.save_json(
        "cleanup-fixture.json",
        {
            "vault_id": refresh.vault_id,
            "expired_at": expired_at.isoformat(),
            "retained_at": retained_at.isoformat(),
            "expired_root": str(expired_root),
            "retained_root": str(retained_root),
        },
    )
    return expired_root, retained_file


def insert_cleanup_rows(
    app: Application,
    *,
    vault_id: str,
    vault_name: str,
    expired_at: datetime,
    retained_at: datetime,
    expired_snapshot_root: Path,
    retained_snapshot_root: Path,
) -> None:
    db_path = app.root / "system" / "vault_state.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executemany(
            """
                INSERT INTO vault_activities (
                    activity_id, vault_id, vault_name, kind, source, scope, label,
                    task_id, status, created_at, updated_at, completed_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
            [
                (
                    "task:expired-task:" + vault_id,
                    vault_id,
                    vault_name,
                    "workflow",
                    "api",
                    "workflow_vault:" + vault_name,
                    vault_name + "/expired",
                    "expired-task",
                    "completed",
                    expired_at.isoformat(),
                    expired_at.isoformat(),
                    expired_at.isoformat(),
                    expired_at.isoformat(),
                ),
                (
                    "task:retained-task:" + vault_id,
                    vault_id,
                    vault_name,
                    "workflow",
                    "api",
                    "workflow_vault:" + vault_name,
                    vault_name + "/retained",
                    "retained-task",
                    "completed",
                    retained_at.isoformat(),
                    retained_at.isoformat(),
                    retained_at.isoformat(),
                    retained_at.isoformat(),
                ),
            ],
        )
        conn.executemany(
            """
                INSERT INTO vault_mutations (
                    activity_id, operation_id, path, target_kind, operation, status,
                    before_exists, before_hash, after_exists, after_hash,
                    created_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
            [
                (
                    "task:expired-task:" + vault_id,
                    "expired-operation",
                    "notes/expired.md",
                    "file",
                    "write",
                    "completed",
                    0,
                    None,
                    1,
                    "expired-hash",
                    expired_at.isoformat(),
                    expired_at.isoformat(),
                ),
                (
                    "task:retained-task:" + vault_id,
                    "retained-operation",
                    "notes/retained.md",
                    "file",
                    "write",
                    "completed",
                    0,
                    None,
                    1,
                    "retained-hash",
                    retained_at.isoformat(),
                    retained_at.isoformat(),
                ),
            ],
        )
        conn.executemany(
            """
                INSERT INTO snapshot_sets (
                    id, task_id, task_kind, task_source, task_scope, task_label,
                    vault_id, vault_name, purpose, scope_kind, scope_id, snapshot_root,
                    status, created_at, expires_at, rolled_back_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
            [
                (
                    9001,
                    "expired-task",
                    "workflow",
                    "api",
                    "workflow_vault:" + vault_name,
                    vault_name + "/expired",
                    vault_id,
                    vault_name,
                    "rollback",
                    "task",
                    "expired-task",
                    str(expired_snapshot_root),
                    "active",
                    expired_at.isoformat(),
                    expired_at.isoformat(),
                    None,
                ),
                (
                    9002,
                    "retained-task",
                    "workflow",
                    "api",
                    "workflow_vault:" + vault_name,
                    vault_name + "/retained",
                    vault_id,
                    vault_name,
                    "rollback",
                    "task",
                    "retained-task",
                    str(retained_snapshot_root),
                    "active",
                    retained_at.isoformat(),
                    retained_at.isoformat(),
                    None,
                ),
            ],
        )
        conn.executemany(
            """
                INSERT INTO file_snapshots (
                    id, snapshot_set_id, task_id, vault_id, vault_name, path, source,
                    file_exists, content_hash, snapshot_ref, created_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
            [
                (
                    9101,
                    9001,
                    "expired-task",
                    vault_id,
                    vault_name,
                    "notes/expired.md",
                    "task_mutation_before",
                    1,
                    "expired-hash",
                    "files/notes/expired.md",
                    expired_at.isoformat(),
                    expired_at.isoformat(),
                ),
                (
                    9102,
                    9002,
                    "retained-task",
                    vault_id,
                    vault_name,
                    "notes/retained.md",
                    "task_mutation_before",
                    1,
                    "retained-hash",
                    "files/notes/retained.md",
                    retained_at.isoformat(),
                    retained_at.isoformat(),
                ),
            ],
        )
        conn.commit()
    finally:
        conn.close()


FAILING_PROBE_WORKFLOW = """---
run_type: workflow
enabled: false
description: Vault-state rollback failure probe
---

## Run

```python
await file_write(
    operation="write",
    path="notes/created-before-failure.md",
    content="created then rolled back\\n",
)
await file_write(
    operation="append",
    path="notes/preexisting-append.md",
    content="mutated append\\n",
)
await file_write(
    operation="delete",
    path="notes/preexisting-delete.md",
    confirm_path="notes/preexisting-delete.md",
)
await file_write(
    operation="move",
    path="notes/move-source.md",
    destination="notes/move-destination.md",
)
await file_write(
    operation="mkdir",
    path="notes/directory-before-failure",
)
raise RuntimeError("rollback probe failure")
```
"""
