"""Startup upgrades legacy databases before runtime services use them."""

from __future__ import annotations

import sqlite3

import pytest

from core.system_migrations import get_system_migration_status
from validation.scenarios.application import Application
from validation.scenarios.support.startup import (
    create_goal_ops_v1_db,
    create_legacy_chat_sessions_db,
    create_legacy_session_summaries_db,
    migration_versions,
    table_columns,
    table_exists,
)

pytestmark = pytest.mark.scenario


async def test_startup_migrates_system_databases(app: Application) -> None:
    app.vault("StartupMigrationVault")
    system_root = app.root / "system"
    system_root.mkdir(parents=True, exist_ok=True)
    create_legacy_chat_sessions_db(system_root / "chat_sessions.db")
    create_legacy_session_summaries_db(system_root / "session_summaries.db")
    create_goal_ops_v1_db(system_root / "goal_ops.db")
    before = get_system_migration_status(system_root)
    assert (
        before.pending_count > 0
    ), "Legacy startup databases should have pending migrations before bootstrap"
    await app.start()
    after = get_system_migration_status(system_root)
    assert (
        after.pending_count == 0
    ), "Startup should apply all system database migrations"
    with sqlite3.connect(system_root / "chat_sessions.db") as conn:
        assert table_exists(
            conn, "chat_compaction_checkpoints"
        ), "Startup should migrate chat compaction checkpoint storage"
        assert migration_versions(conn, "chat_sessions") == [
            1,
            2,
            3,
            4,
            5,
            6,
            7,
            8,
            9,
            10,
        ], "Startup should record chat migration versions"
        assert not table_exists(
            conn, "chat_session_map_revisions"
        ), "Startup should remove retired session-map revisions"
        assert not table_exists(
            conn, "chat_session_map_maintenance"
        ), "Startup should remove retired session-map maintenance state"
        assert not table_exists(
            conn, "chat_session_map_attempts"
        ), "Startup should remove retired session-map attempt audits"
    with sqlite3.connect(system_root / "session_summaries.db") as conn:
        assert not table_exists(
            conn, "session_summaries"
        ), "Startup should retire legacy summary data"
        assert not table_exists(
            conn, "session_summaries_fts"
        ), "Startup should retire legacy summary indexes"
        assert migration_versions(conn, "session_summaries") == [
            4
        ], "Startup should record summary retirement without recreating prior schemas"
    backups = list(
        (system_root / "migration_backups").glob("session_summaries.db.backup-*")
    )
    assert len(backups) == 1, "Startup should back up legacy summaries"
    with sqlite3.connect(backups[0]) as conn:
        assert table_exists(
            conn, "session_summaries"
        ), "The automatic backup should preserve the retired schema"
    with sqlite3.connect(system_root / "goal_ops.db") as conn:
        goal_columns = table_columns(conn, "goals")
        assert (
            "source_type" in goal_columns
        ), "Startup should migrate goal source provenance"
        assert "source_id" in goal_columns, "Startup should migrate goal source ids"
        assert (
            "source_task_id" in goal_columns
        ), "Startup should migrate goal source task ids"
        assert (
            "source_label" in goal_columns
        ), "Startup should migrate goal source labels"
        assert "plan_json" in goal_columns, "Startup should migrate goal plan snapshots"
        assert migration_versions(conn, "goal_ops") == [
            1,
            2,
            3,
        ], "Startup should preserve existing goal migration history and apply pending versions"
    with sqlite3.connect(system_root / "vault_state.db") as conn:
        assert migration_versions(conn, "vault_state") == [
            1
        ], "Startup should record the vault-state migration version"
    with sqlite3.connect(system_root / "workflow_runs.db") as conn:
        assert table_exists(
            conn, "workflow_runs"
        ), "Startup should create durable workflow run history"
        assert migration_versions(conn, "workflow_runs") == [
            1,
            2,
            3,
        ], "Startup should record the workflow-run migration versions"
