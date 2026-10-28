"""Startup input fixtures and real scheduler/database observations."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from validation.scenarios.application import Application


async def restart(app: Application) -> None:
    await app.controller.restart_system()
    app.evidence.step("application_restarted")


async def trigger_scheduled_job(app: Application, workflow_id: str) -> None:
    app.controller.trigger_job_manually(workflow_id)
    assert await app.controller.wait_for_scheduled_run(workflow_id, timeout_seconds=60)
    app.evidence.step("scheduled_job_completed", workflow_id=workflow_id)


def scheduler_record(app: Application, workflow_id: str, after: int) -> dict[str, Any]:
    for event in reversed(
        app.events.select("workflow_scheduler_sync_completed", after=after)
    ):
        for record in event.get("data", {}).get("scheduled_workflows", []):
            if record.get("workflow_id") == workflow_id:
                return dict(record)
    raise AssertionError(f"Missing scheduler record for {workflow_id}")


def create_legacy_chat_sessions_db(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "\n                CREATE TABLE chat_sessions (\n                    session_id TEXT NOT NULL,\n                    vault_name TEXT NOT NULL,\n                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,\n                    last_activity_at DATETIME DEFAULT CURRENT_TIMESTAMP,\n                    title TEXT,\n                    metadata_json TEXT,\n                    PRIMARY KEY (session_id, vault_name)\n                )\n                "
        )


def create_legacy_session_summaries_db(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "\n                CREATE TABLE session_summaries (\n                    session_id TEXT NOT NULL,\n                    vault_name TEXT NOT NULL,\n                    title TEXT,\n                    summary TEXT,\n                    domain TEXT,\n                    work_product TEXT,\n                    user_intent TEXT,\n                    named_entities TEXT,\n                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,\n                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,\n                    metadata_json TEXT,\n                    PRIMARY KEY (session_id, vault_name)\n                )\n                "
        )


def create_goal_ops_v1_db(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "\n                CREATE TABLE goals (\n                    goal_id TEXT PRIMARY KEY,\n                    vault_name TEXT NOT NULL,\n                    workspace_path_hint TEXT,\n                    title TEXT NOT NULL,\n                    objective TEXT NOT NULL,\n                    status TEXT NOT NULL,\n                    success_criteria_json TEXT NOT NULL DEFAULT '[]',\n                    metadata_json TEXT,\n                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,\n                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP\n                )\n                "
        )
        conn.execute(
            "\n                CREATE TABLE schema_migrations (\n                    namespace TEXT NOT NULL,\n                    version INTEGER NOT NULL,\n                    name TEXT NOT NULL,\n                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,\n                    PRIMARY KEY (namespace, version)\n                )\n                "
        )
        conn.execute(
            "\n                INSERT INTO schema_migrations (namespace, version, name)\n                VALUES ('goal_ops', 1, 'create_goal_ops_tables')\n                "
        )


def migration_versions(conn: sqlite3.Connection, namespace: str) -> list[int]:
    return [
        int(row[0])
        for row in conn.execute(
            "\n                SELECT version\n                FROM schema_migrations\n                WHERE namespace = ?\n                ORDER BY version\n                ",
            (namespace,),
        ).fetchall()
    ]


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "\n            SELECT 1\n            FROM sqlite_master\n            WHERE type IN ('table', 'virtual table')\n              AND name = ?\n            LIMIT 1\n            ",
        (table_name,),
    ).fetchone()
    return row is not None


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {
        str(row[1])
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }


QUICK_JOB_WORKFLOW = '---\nschedule: cron: */1 * * * *\nrun_type: workflow\nenabled: true\ndescription: Quick job for persistence testing\n---\n\n## Run\n\n```python\nawait file_write(operation="write", path=f"results/{date.today()}.md", content="ok")\nawait finish(status="completed", reason="quick-job-done")\n```\n'


QUICK_JOB_2_WORKFLOW = '---\nschedule: cron: */2 * * * *\nrun_type: workflow\nenabled: true\ndescription: Second quick job for subfolder testing\n---\n\n## Run\n\n```python\nawait file_write(operation="write", path=f"results/{date.today()}.md", content="ok")\nawait finish(status="completed", reason="quick-job-2-done")\n```\n'


QUICK_JOB_UPDATED_WORKFLOW = '---\nschedule: cron: */2 * * * *\nrun_type: workflow\nenabled: true\ndescription: Updated schedule for persistence testing\n---\n\n## Run\n\n```python\nawait file_write(operation="write", path=f"results/{date.today()}.md", content="ok")\nawait finish(status="completed", reason="quick-job-updated-done")\n```\n'


MALFORMED_SCHEDULE_WORKFLOW = '---\nschedule: every 1d at 9am\nrun_type: workflow\nenabled: true\ndescription: Malformed workflow with invalid old schedule syntax\n---\n\n## Run\n\n```python\nawait finish(status="completed", reason="should-not-reach")\n```\n'
