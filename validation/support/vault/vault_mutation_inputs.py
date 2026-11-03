"""Workflow input exercising routed vault mutations."""

WRITE_PROBE_WORKFLOW = '---\nrun_type: workflow\nenabled: false\ndescription: Vault-state mutation recorder probe\n---\n\n## Run\n\n```python\nawait file_write(\n    operation="write",\n    path="notes/created-by-workflow.md",\n    content="Created by workflow\\n",\n)\nawait file_write(\n    operation="append",\n    path="notes/preexisting-append.md",\n    content="Second line\\n",\n)\nawait file_write(\n    operation="write",\n    path="notes/edit-target.md",\n    content="alpha\\nbeta\\n",\n)\nawait file_write(\n    operation="edit_line",\n    path="notes/edit-target.md",\n    line_number=2,\n    old_text="beta",\n    new_text="gamma",\n)\nawait file_write(\n    operation="write",\n    path="notes/replace-target.md",\n    content="before text\\n",\n)\nawait file_write(\n    operation="replace_text",\n    path="notes/replace-target.md",\n    old_text="before",\n    new_text="after",\n    count=1,\n)\nawait file_write(\n    operation="write",\n    path="notes/truncate-target.md",\n    content="remove me\\n",\n)\nawait file_write(\n    operation="write",\n    path="notes/truncate-target.md",\n    content="",\n    overwrite=True,\n)\nawait file_write(\n    operation="delete",\n    path="notes/preexisting-delete.md",\n    confirm_path="notes/preexisting-delete.md",\n)\nawait file_write(\n    operation="write",\n    path="notes/move-source.md",\n    content="move me\\n",\n)\nawait file_write(\n    operation="move",\n    path="notes/move-source.md",\n    destination="notes/move-destination.md",\n)\nawait file_write(\n    operation="write",\n    path="notes/overwrite-source.md",\n    content="overwrite source\\n",\n)\nawait file_write(\n    operation="write",\n    path="notes/overwrite-destination.md",\n    content="overwrite destination\\n",\n)\nawait file_write(\n    operation="move",\n    path="notes/overwrite-source.md",\n    destination="notes/overwrite-destination.md",\n    overwrite=True,\n)\nawait file_write(\n    operation="delete",\n    path="assets/delete-target.pdf",\n    confirm_path="assets/delete-target.pdf",\n)\nawait file_write(\n    operation="move",\n    path="assets/move-source.pdf",\n    destination="assets/move-destination.pdf",\n)\nawait file_write(\n    operation="move",\n    path="assets/overwrite-source.pdf",\n    destination="assets/overwrite-destination.pdf",\n    overwrite=True,\n)\nawait finish(status="completed", reason="write-probe-done")\n```\n'


def seed_chat_history(app, vault_id: str, vault_name: str) -> None:
    """Seed historical chat records to test the activity API projection."""
    import sqlite3
    from datetime import UTC, datetime, timedelta

    from core.chat.schema import ensure_chat_sessions_schema
    from core.identity import LOCAL_USER_PRINCIPAL_ID

    system = app.root / "system"
    ensure_chat_sessions_schema(str(system))
    with sqlite3.connect(system / "chat_sessions.db") as conn:
        conn.execute(
            "INSERT INTO chat_sessions (session_id, vault_name, owner_principal_id, created_at, last_activity_at, title, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
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
    now = datetime.now(UTC)
    with sqlite3.connect(system / "vault_state.db") as conn:
        for number in (1, 2):
            task_id = f"chat-task-{number}"
            activity_id = f"task:{task_id}:{vault_id}"
            created = (now + timedelta(seconds=number)).isoformat()
            expires = (now + timedelta(days=365)).isoformat()
            conn.execute(
                "INSERT INTO vault_activities (activity_id, task_id, kind, source, scope, label, vault_id, vault_name, status, rollback_status, goal_id, step_id, created_at, updated_at, completed_at, expires_at, metadata_json) VALUES (?, ?, 'chat', 'api', 'chat_session:validation-session', 'chat:validation-session', ?, ?, 'completed', NULL, NULL, NULL, ?, ?, ?, ?, NULL)",
                (
                    activity_id,
                    task_id,
                    vault_id,
                    vault_name,
                    created,
                    created,
                    created,
                    expires,
                ),
            )
            conn.execute(
                "INSERT INTO vault_mutations (activity_id, operation_id, path, related_path, target_kind, operation, status, event_sequence, before_exists, before_hash, before_snapshot_id, after_exists, after_hash, after_snapshot_id, snapshot_ref, created_at, expires_at, metadata_json) VALUES (?, ?, ?, NULL, 'file', 'write', 'completed', NULL, 0, NULL, NULL, 1, ?, NULL, NULL, ?, ?, NULL)",
                (
                    activity_id,
                    f"operation:{task_id}",
                    f"notes/chat-{number}.md",
                    f"chat-after-hash-{number}",
                    created,
                    expires,
                ),
            )


def preload_scan_interval(app, seconds: int) -> None:
    """Install configuration in the isolated system root before bootstrap."""
    app.controller.prepare()
    from validation.support.configuration import (
        install_configuration,
        read_configuration,
        validate_configuration,
    )

    root = app.root / "system"
    config = read_configuration(root)
    config["settings"]["vault_scan_interval_seconds"]["value"] = seconds
    validate_configuration(config)
    install_configuration(root, config)
