"""Retirement is backed up, scoped to owned tables, and repeatable."""

import json
import sqlite3

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.session_summary_retirement import (
    canonical_snapshot,
    seed_canonical_history,
    seed_dependent,
    seed_legacy_summaries,
)

pytestmark = pytest.mark.scenario


async def test_summary_retirement_backup_scope_and_repeatability(
    app: Application,
) -> None:
    await app.start()
    from core.system_migrations import (
        get_system_migration_status,
        run_system_migrations,
    )

    root = app.root / "retirement-populated"
    db = seed_legacy_summaries(root)
    canonical = seed_canonical_history(root)
    before = get_system_migration_status(root)
    assert next(
        row for row in before.targets if row.db_name == "session_summaries"
    ).pending_versions == (4,)
    with pytest.raises(ValueError):
        run_system_migrations(root, backup=False)
    with sqlite3.connect(db) as conn:
        assert (
            conn.execute("SELECT summary FROM session_summaries").fetchone()[0]
            == "retirement fixture"
        )
    after = run_system_migrations(root)
    target = next(row for row in after.targets if row.db_name == "session_summaries")
    assert (
        target.applied_versions == (1, 2, 3, 4)
        and not target.pending_versions
        and target.backup_path
    )
    with sqlite3.connect(target.backup_path) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert (
            conn.execute("SELECT summary FROM session_summaries").fetchone()[0]
            == "retirement fixture"
        )
        assert (
            conn.execute("SELECT count(*) FROM session_summaries_fts").fetchone()[0]
            == 1
        )
    with sqlite3.connect(db) as conn:
        assert {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        } == {"schema_migrations", "user_extension"}
        assert (
            conn.execute("SELECT value FROM user_extension").fetchone()[0]
            == "preserve unknown state"
        )
    assert canonical_snapshot(root) == canonical
    repeated = run_system_migrations(root)
    assert repeated.pending_count == 0 and all(
        row.backup_path is None for row in repeated.targets
    )
    from pathlib import Path

    assert Path(target.backup_path).exists()
    fresh = app.root / "retirement-fresh"
    fresh.mkdir()
    assert run_system_migrations(fresh).pending_count == 0
    assert not (fresh / "session_summaries.db").exists()


async def test_summary_retirement_rejects_external_foreign_keys(
    app: Application,
) -> None:
    await app.start()
    from core.system_migrations import run_system_migrations

    for policy, target in (
        ("CASCADE", "session_summaries"),
        ("RESTRICT", "session_summaries"),
        ("CASCADE", "session_summaries_fts_data"),
    ):
        root = app.root / f"dependent-{policy.lower()}-{target}"
        db = seed_dependent(root, policy, target)
        with pytest.raises(RuntimeError) as error:
            run_system_migrations(root)
        assert "user_extension" in str(error.value) and target in str(error.value)
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT * FROM user_extension").fetchall() == [
                (1, "preserve dependent state")
            ]
            assert conn.execute("SELECT * FROM session_summaries").fetchall() == [
                (1, "owned summary")
            ]
            assert not conn.execute(
                "SELECT 1 FROM schema_migrations WHERE namespace='session_summaries' AND version=4"
            ).fetchone()
        backups = list(
            (root / "migration_backups").glob("session_summaries.db.backup-*")
        )
        assert len(backups) == 1
        with sqlite3.connect(backups[0]) as conn:
            assert conn.execute("SELECT * FROM user_extension").fetchall() == [
                (1, "preserve dependent state")
            ]
    activity = app.request("/api/system/activity-log", params={"limit": 1000})
    assert activity.status_code == 200
    blocked = [
        row["data"]
        for row in activity.json()["entries"]
        if row.get("data", {}).get("event") == "session_summary_data_retirement_blocked"
    ]
    assert len(blocked) == 3
    assert all(
        row["status"] == "blocked"
        and row["reason"] == "custom_foreign_key_dependency"
        and row["dependencies"]
        in (
            [["user_extension", "session_summaries"]],
            [["user_extension", "session_summaries_fts_data"]],
        )
        and row["backup_directory"].endswith("migration_backups")
        for row in blocked
    )
    assert "preserve dependent state" not in json.dumps(
        blocked
    ) and "owned summary" not in json.dumps(blocked)
