"""Integration scenario for vault-state task mutation recording."""

import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.chat.schema import ensure_chat_sessions_schema
from core.identity import LOCAL_USER_AUTHORITY, LOCAL_USER_PRINCIPAL_ID
from core.runtime.execution_tasks import (
    ExecutionTaskKind,
    ExecutionTaskSource,
    workflow_vault_scope,
)
from core.runtime.state import get_runtime_context
from core.vault_state.file_mutations import mutate_vault_file
from core.vault_state.identity import resolve_or_create_vault_identity
from validation.support.base_scenario import BaseScenario


class VaultStateMutationRecorderScenario(BaseScenario):
    """Validate file_write records task-scoped vault mutations."""

    async def test_scenario(self):
        vault = self.create_vault("VaultStateMutationVault")
        self.create_file(vault, "notes/preexisting-append.md", "Original line\n")
        self.create_file(vault, "notes/preexisting-delete.md", "Delete original\n")
        self.create_file(vault, "assets/delete-target.pdf", "Delete attachment\n")
        self.create_file(vault, "assets/move-source.pdf", "Move attachment\n")
        self.create_file(
            vault, "assets/overwrite-source.pdf", "Overwrite attachment source\n"
        )
        self.create_file(
            vault,
            "assets/overwrite-destination.pdf",
            "Overwrite attachment destination\n",
        )
        self.create_file(
            vault, "AssistantMD/Authoring/write_probe.md", WRITE_PROBE_WORKFLOW
        )

        await self.start_system()

        checkpoint = self.event_checkpoint()
        result = await self.run_workflow(vault, "write_probe")
        events = self.events_since(checkpoint)

        self.soft_assert_equal(
            result.status, "completed", "Workflow write probe should complete"
        )
        mutation_event = self.assert_event_contains(
            events,
            name="vault_mutation_recorded",
            expected={
                "vault_name": vault.name,
                "path": "notes/created-by-workflow.md",
                "operation": "write",
                "before_exists": False,
                "after_exists": True,
            },
        )
        task_id = mutation_event["data"]["task_id"]
        vault_id = mutation_event["data"]["vault_id"]
        self.assert_event_contains(
            events,
            name="snapshot_set_created",
            expected={
                "task_id": task_id,
                "vault_id": vault_id,
                "vault_name": vault.name,
                "purpose": "rollback",
            },
        )
        self.assert_event_contains(
            events,
            name="vault_file_snapshot_recorded",
            expected={
                "task_id": task_id,
                "vault_id": vault_id,
                "path": "notes/created-by-workflow.md",
                "before_exists": False,
            },
        )

        rows = self._mutation_rows(task_id)
        self.soft_assert(rows, "Mutation rows should be persisted")
        current_vault_id = resolve_or_create_vault_identity(vault).vault_id
        operations_by_path = {(row["operation"], row["path"]) for row in rows}
        expected_operations = {
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
        self.soft_assert_equal(
            expected_operations - operations_by_path,
            set(),
            "Mutation rows should cover routed file_write operations",
        )
        row = next(
            (item for item in rows if item["path"] == "notes/created-by-workflow.md"),
            None,
        )
        if row is not None:
            self.soft_assert_equal(
                row["task_id"], task_id, "Mutation row task_id should match event"
            )
            self.soft_assert_equal(
                row["task_kind"], "workflow", "Mutation row should persist task kind"
            )
            self.soft_assert_equal(
                row["task_source"], "api", "Mutation row should persist task source"
            )
            self.soft_assert_equal(
                row["task_label"],
                f"{vault.name}/write_probe",
                "Mutation row should persist task label",
            )
            self.soft_assert_equal(
                row["vault_id"], vault_id, "Mutation row vault_id should match event"
            )
            self.soft_assert_equal(
                row["path"],
                "notes/created-by-workflow.md",
                "Mutation path should match",
            )
            self.soft_assert_equal(
                row["before_exists"], 0, "Mutation before_exists should be false"
            )
            self.soft_assert_equal(
                row["after_exists"], 1, "Mutation after_exists should be true"
            )
            self.soft_assert(row["after_hash"], "Mutation should capture after hash")
            self.soft_assert(
                row["event_sequence"] is not None, "Mutation should link vault event"
            )
            self.soft_assert(
                row["expires_at"], "Mutation should have retention expiration"
            )
            self.soft_assert(
                row["before_snapshot_id"],
                "Create-file absence should have a file snapshot row",
            )
            self.soft_assert_equal(
                row["snapshot_ref"],
                None,
                "Create-file snapshot should record absence without a file snapshot ref",
            )

        append_row = next(
            (item for item in rows if item["operation"] == "append"), None
        )
        if append_row is not None:
            self.soft_assert_equal(
                append_row["before_exists"],
                1,
                "Append should capture existing before state",
            )
            self.soft_assert(
                append_row["before_snapshot_id"],
                "Append should retain file snapshot id",
            )
            self.soft_assert(
                append_row["snapshot_ref"],
                "Append should retain pre-mutation snapshot ref",
            )
        delete_row = next(
            (item for item in rows if item["operation"] == "delete"), None
        )
        if delete_row is not None:
            self.soft_assert_equal(
                delete_row["after_exists"],
                0,
                "Delete should record missing after state",
            )
            self.soft_assert(
                delete_row["snapshot_ref"],
                "Delete should retain pre-mutation snapshot ref",
            )
        attachment_delete_row = next(
            (item for item in rows if item["path"] == "assets/delete-target.pdf"), None
        )
        if attachment_delete_row is not None:
            self.soft_assert_equal(
                attachment_delete_row["after_exists"],
                0,
                "Delete should support non-markdown vault files",
            )
        expected_move_related_paths = {
            "notes/move-source.md": "notes/move-destination.md",
            "notes/move-destination.md": "notes/move-source.md",
            "notes/overwrite-source.md": "notes/overwrite-destination.md",
            "notes/overwrite-destination.md": "notes/overwrite-source.md",
            "assets/move-source.pdf": "assets/move-destination.pdf",
            "assets/move-destination.pdf": "assets/move-source.pdf",
            "assets/overwrite-source.pdf": "assets/overwrite-destination.pdf",
            "assets/overwrite-destination.pdf": "assets/overwrite-source.pdf",
        }
        for path, related_path in expected_move_related_paths.items():
            move_row = next(
                (
                    item
                    for item in rows
                    if item["operation"] == "move" and item["path"] == path
                ),
                None,
            )
            if move_row is not None:
                self.soft_assert_equal(
                    move_row["related_path"],
                    related_path,
                    f"Move row for {path} should point at paired path",
                )
        non_move_related_paths = [
            item["related_path"] for item in rows if item["operation"] != "move"
        ]
        self.soft_assert(
            all(related_path is None for related_path in non_move_related_paths),
            "Non-move mutations should not carry related paths",
        )
        self.soft_assert(
            not (vault / "assets" / "delete-target.pdf").exists(),
            "Non-markdown delete should remove the source file",
        )
        self.soft_assert(
            not (vault / "assets" / "move-source.pdf").exists(),
            "Non-markdown safe move should remove the source file",
        )
        self.soft_assert(
            (vault / "assets" / "move-destination.pdf").exists(),
            "Non-markdown safe move should create the destination file",
        )
        self.soft_assert(
            not (vault / "assets" / "overwrite-source.pdf").exists(),
            "Non-markdown move overwrite should remove the source file",
        )
        self.soft_assert_equal(
            (vault / "assets" / "overwrite-destination.pdf").read_text(
                encoding="utf-8"
            ),
            "Overwrite attachment source\n",
            "Non-markdown move overwrite should replace destination content",
        )

        response = self.call_api(
            f"/api/vaults/{vault.name}/activity",
            params={"limit": 5},
        )
        self.soft_assert_equal(
            response.status_code, 200, "Task mutation activity API should respond"
        )
        payload = response.json()
        groups = payload.get("groups", [])
        api_group = next(
            (group for group in groups if group.get("task_id") == task_id), None
        )
        self.soft_assert(
            api_group is not None,
            "Task mutation activity API should include workflow task",
        )
        if api_group is not None:
            self.soft_assert_equal(
                api_group.get("status"),
                "completed",
                "Workflow activity should complete",
            )
            self.soft_assert_equal(
                api_group.get("activity_kind"),
                "workflow",
                "Workflow activity kind should match",
            )
            self.soft_assert_equal(
                api_group.get("task_kind"), "workflow", "API should expose task kind"
            )
            self.soft_assert_equal(
                api_group.get("task_source"), "api", "API should expose task source"
            )
            self.soft_assert_equal(
                api_group.get("task_label"),
                f"{vault.name}/write_probe",
                "API should expose task label",
            )
            self.soft_assert_equal(
                api_group.get("mutation_count"),
                len(rows),
                "API should group routed mutations",
            )
            mutations = api_group.get("mutations", [])
            self.soft_assert_equal(
                len(mutations), len(rows), "API group should include mutation rows"
            )
            if mutations:
                api_operations = {
                    (mutation.get("operation"), mutation.get("path"))
                    for mutation in mutations
                }
                self.soft_assert_equal(
                    expected_operations - api_operations,
                    set(),
                    "API mutations should include routed operations",
                )
                self.soft_assert(
                    all(
                        mutation.get("event_sequence") is not None
                        for mutation in mutations
                    ),
                    "API mutations should include event sequences",
                )
                api_related_paths = {
                    mutation.get("path"): mutation.get("related_path")
                    for mutation in mutations
                    if mutation.get("operation") == "move"
                }
                self.soft_assert_equal(
                    api_related_paths,
                    expected_move_related_paths,
                    "API move mutations should expose paired related paths",
                )

        self._insert_chat_session(vault_name=vault.name)
        self._insert_chat_mutation_rows(
            vault_id=current_vault_id, vault_name=vault.name
        )
        chat_response = self.call_api(
            f"/api/vaults/{vault.name}/activity",
            params={"limit": 5},
        )
        self.soft_assert_equal(
            chat_response.status_code, 200, "Chat activity API should respond"
        )
        chat_groups = chat_response.json().get("groups", [])
        session_groups = [
            group
            for group in chat_groups
            if group.get("chat_session_id") == "validation-session"
        ]
        self.soft_assert_equal(
            len(session_groups), 2, "Chat turns should remain separate activities"
        )
        for chat_group in session_groups:
            self.soft_assert_equal(
                chat_group.get("activity_kind"),
                "chat",
                "Chat activity kind should match",
            )
            self.soft_assert_equal(
                chat_group.get("chat_session_id"),
                "validation-session",
                "Chat group should expose session id",
            )
            self.soft_assert_equal(
                chat_group.get("chat_session_title"),
                "Validation Chat Title",
                "Chat group should expose session title",
            )
            self.soft_assert_equal(
                chat_group.get("mutation_count"),
                1,
                "Chat turn should include its mutation",
            )

        manifest = self._manifest_row(current_vault_id, "notes/created-by-workflow.md")
        self.soft_assert(
            manifest is not None, "Manifest should update immediately after write"
        )
        if manifest is not None:
            self.soft_assert_equal(
                manifest["deleted_at"], None, "Created file should be active"
            )
            self.soft_assert_equal(
                manifest["artifact_class"], "user_content", "Created note class"
            )

        failure_checkpoint = self.event_checkpoint()
        caught = None
        runtime = get_runtime_context()
        async with runtime.task_coordinator.track_current_task(
            authority=LOCAL_USER_AUTHORITY,
            kind=ExecutionTaskKind.WORKFLOW.value,
            scope=workflow_vault_scope(vault.name),
            source=ExecutionTaskSource.API.value,
            label=f"{vault.name}/mutation_failure_probe",
            metadata={"vault": vault.name},
        ):
            try:
                mutate_vault_file(
                    vault_path=vault,
                    path="notes/failing-write.md",
                    operation="write",
                    mutator=_raise_forced_mutation_failure,
                    create_parent=True,
                )
            except RuntimeError as exc:
                caught = exc
        self.soft_assert(caught is not None, "Forced mutation failure should propagate")
        failure_events = self.events_since(failure_checkpoint)
        self.assert_event_contains(
            failure_events,
            name="vault_state_mutation_failed",
            expected={
                "vault_id": current_vault_id,
                "vault_name": vault.name,
                "path": "notes/failing-write.md",
                "operation": "write",
                "stage": "mutate",
                "before_exists": False,
                "error_type": "RuntimeError",
            },
        )

        await self.stop_system()
        self.teardown_scenario()
        self.assert_no_failures()

    def _mutation_rows(self, task_id: str) -> list[dict]:
        db_path = self._get_system_controller()._system_root / "vault_state.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT a.task_id, a.kind AS task_kind, a.source AS task_source,
                       a.scope AS task_scope, a.label AS task_label,
                       a.vault_id, a.vault_name, m.path, m.operation,
                       m.related_path, m.event_sequence, m.before_exists, m.before_hash,
                       m.before_snapshot_id, m.after_exists, m.after_hash,
                       m.snapshot_ref, m.expires_at
                FROM vault_mutations AS m
                JOIN vault_activities AS a ON a.activity_id = m.activity_id
                WHERE a.task_id = ?
                ORDER BY m.id ASC
                """,
                (task_id,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def _insert_chat_session(self, *, vault_name: str) -> None:
        system_root = self._get_system_controller()._system_root
        ensure_chat_sessions_schema(str(system_root))
        db_path = system_root / "chat_sessions.db"
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO chat_sessions (
                    session_id, vault_name, owner_principal_id,
                    created_at, last_activity_at, title, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "validation-session",
                    vault_name,
                    LOCAL_USER_PRINCIPAL_ID,
                    "2026-05-06 17:00:00",
                    "2026-05-06 17:05:00",
                    "Validation Chat Title",
                    None,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _insert_chat_mutation_rows(self, *, vault_id: str, vault_name: str) -> None:
        db_path = self._get_system_controller()._system_root / "vault_state.db"
        now = datetime.now(UTC)
        rows = [
            (
                f"task:chat-task-1:{vault_id}",
                "chat-task-1",
                "chat",
                "api",
                "chat_session:validation-session",
                "chat:validation-session",
                vault_id,
                vault_name,
                "notes/chat-one.md",
                "write",
                None,
                0,
                None,
                1,
                "chat-after-hash-1",
                None,
                now.isoformat(),
                (now + timedelta(days=365)).isoformat(),
            ),
            (
                f"task:chat-task-2:{vault_id}",
                "chat-task-2",
                "chat",
                "api",
                "chat_session:validation-session",
                "chat:validation-session",
                vault_id,
                vault_name,
                "notes/chat-two.md",
                "write",
                None,
                0,
                None,
                1,
                "chat-after-hash-2",
                None,
                (now + timedelta(seconds=1)).isoformat(),
                (now + timedelta(days=365)).isoformat(),
            ),
        ]
        conn = sqlite3.connect(db_path)
        try:
            conn.executemany(
                """
                INSERT INTO vault_activities (
                    activity_id, task_id, kind, source, scope, label,
                    vault_id, vault_name, status, rollback_status,
                    goal_id, step_id, created_at, updated_at, completed_at,
                    expires_at, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'completed', NULL, NULL, NULL,
                        ?, ?, ?, ?, NULL)
                """,
                [
                    (
                        row[0],
                        row[1],
                        row[2],
                        row[3],
                        row[4],
                        row[5],
                        row[6],
                        row[7],
                        row[16],
                        row[16],
                        row[16],
                        row[17],
                    )
                    for row in rows
                ],
            )
            conn.executemany(
                """
                INSERT INTO vault_mutations (
                    activity_id, operation_id, path, related_path,
                    target_kind, operation, status,
                    event_sequence, before_exists, before_hash,
                    before_snapshot_id, after_exists, after_hash,
                    after_snapshot_id, snapshot_ref, created_at, expires_at,
                    metadata_json
                )
                VALUES (?, ?, ?, NULL, 'file', ?, 'completed', ?, ?, ?,
                        NULL, ?, ?, NULL, ?, ?, ?, NULL)
                """,
                [
                    (
                        row[0],
                        f"operation:{row[1]}",
                        row[8],
                        row[9],
                        row[10],
                        row[11],
                        row[12],
                        row[13],
                        row[14],
                        row[15],
                        row[16],
                        row[17],
                    )
                    for row in rows
                ],
            )
            conn.commit()
        finally:
            conn.close()

    def _manifest_row(self, vault_id: str, path: str) -> dict | None:
        db_path = self._get_system_controller()._system_root / "vault_state.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                """
                SELECT vault_id, path, artifact_class, deleted_at
                FROM vault_files
                WHERE vault_id = ? AND path = ?
                """,
                (vault_id, path),
            ).fetchone()
            return dict(row) if row is not None else None
        finally:
            conn.close()


WRITE_PROBE_WORKFLOW = """---
run_type: workflow
enabled: false
description: Vault-state mutation recorder probe
---

## Run

```python
await file_write(
    operation="write",
    path="notes/created-by-workflow.md",
    content="Created by workflow\\n",
)
await file_write(
    operation="append",
    path="notes/preexisting-append.md",
    content="Second line\\n",
)
await file_write(
    operation="write",
    path="notes/edit-target.md",
    content="alpha\\nbeta\\n",
)
await file_write(
    operation="edit_line",
    path="notes/edit-target.md",
    line_number=2,
    old_text="beta",
    new_text="gamma",
)
await file_write(
    operation="write",
    path="notes/replace-target.md",
    content="before text\\n",
)
await file_write(
    operation="replace_text",
    path="notes/replace-target.md",
    old_text="before",
    new_text="after",
    count=1,
)
await file_write(
    operation="write",
    path="notes/truncate-target.md",
    content="remove me\\n",
)
await file_write(
    operation="write",
    path="notes/truncate-target.md",
    content="",
    overwrite=True,
)
await file_write(
    operation="delete",
    path="notes/preexisting-delete.md",
    confirm_path="notes/preexisting-delete.md",
)
await file_write(
    operation="write",
    path="notes/move-source.md",
    content="move me\\n",
)
await file_write(
    operation="move",
    path="notes/move-source.md",
    destination="notes/move-destination.md",
)
await file_write(
    operation="write",
    path="notes/overwrite-source.md",
    content="overwrite source\\n",
)
await file_write(
    operation="write",
    path="notes/overwrite-destination.md",
    content="overwrite destination\\n",
)
await file_write(
    operation="move",
    path="notes/overwrite-source.md",
    destination="notes/overwrite-destination.md",
    overwrite=True,
)
await file_write(
    operation="delete",
    path="assets/delete-target.pdf",
    confirm_path="assets/delete-target.pdf",
)
await file_write(
    operation="move",
    path="assets/move-source.pdf",
    destination="assets/move-destination.pdf",
)
await file_write(
    operation="move",
    path="assets/overwrite-source.pdf",
    destination="assets/overwrite-destination.pdf",
    overwrite=True,
)
await finish(status="completed", reason="write-probe-done")
```
"""


def _raise_forced_mutation_failure(_path: Path) -> None:
    raise RuntimeError("forced mutation failure")
