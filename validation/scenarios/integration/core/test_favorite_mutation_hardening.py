"""Bookmark persistence failure stays inside mutation recovery and visible lifecycle logs."""

import json
import sqlite3

import pytest

from validation.support.application import Application
from validation.support.vault.vault_recovery import execute_rollback, preview_rollback

pytestmark = pytest.mark.scenario


async def test_bookmark_cleanup_failure_compensates_files_and_durable_mutation(
    app: Application,
):
    vault = app.vault("FavoriteRecovery")
    app.write(vault, "Retain.md", "private retained bytes")
    await app.start()
    favorites = f"/api/vaults/{vault.name}/favorites"
    assert (
        app.request(
            favorites, "PATCH", {"paths": ["Retain.md"], "favorite": True}
        ).status_code
        == 200
    )
    database = app.root / "system/vault_state.db"
    # A real database constraint fails bookmark cleanup; mutation services stay real.
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TRIGGER reject_bookmark_cleanup BEFORE DELETE ON vault_favorites BEGIN SELECT RAISE(ABORT, 'controlled bookmark cleanup failure'); END"
        )
    try:
        response = app.request(
            f"/api/vaults/{vault.name}/paths/delete-batch",
            "POST",
            {"sources": ["Retain.md"], "confirmed_sources": ["Retain.md"]},
        )
        assert response.status_code != 200
        assert (vault / "Retain.md").read_text() == "private retained bytes"
        assert app.request(favorites).json()["paths"] == ["Retain.md"]
        with sqlite3.connect(database) as connection:
            assert (
                connection.execute(
                    "SELECT count(*) FROM vault_mutations WHERE path='Retain.md' AND operation='delete'"
                ).fetchone()[0]
                == 0
            )
    finally:
        with sqlite3.connect(database) as connection:
            connection.execute("DROP TRIGGER reject_bookmark_cleanup")
    retry = app.request(
        f"/api/vaults/{vault.name}/paths/delete-batch",
        "POST",
        {"sources": ["Retain.md"], "confirmed_sources": ["Retain.md"]},
    )
    assert retry.status_code == 200, retry.text
    assert not (vault / "Retain.md").exists()
    assert app.request(favorites).json()["paths"] == []
    activity_id = retry.json()["activity_id"]
    plan = preview_rollback(app, vault.name, activity_id)
    assert plan["can_rollback"] is True
    assert execute_rollback(app, vault.name, activity_id, plan).status_code == 200
    assert (vault / "Retain.md").read_text() == "private retained bytes"


async def test_delete_lifecycle_is_correlated_and_file_detail_stays_out_of_system_activity(
    app: Application,
):
    vault = app.vault("DeletionDiagnostics")
    app.write(vault, "Delete.md", "PRIVATE_FILE_CONTENT_SENTINEL")
    await app.start()
    assert (
        app.request(
            f"/api/vaults/{vault.name}/favorites",
            "PATCH",
            {"paths": ["Delete.md"], "favorite": True},
        ).status_code
        == 200
    )
    url = f"/api/vaults/{vault.name}/paths/delete-batch"
    for _ in range(2):
        rejected = app.request(
            url, "POST", {"sources": ["Delete.md"], "confirmed_sources": ["Other.md"]}
        )
        assert rejected.status_code == 400
        assert (vault / "Delete.md").exists()
    success = app.request(
        url, "POST", {"sources": ["Delete.md"], "confirmed_sources": ["Delete.md"]}
    )
    assert success.status_code == 200
    entries = app.request("/api/system/activity-log", params={"limit": 1000}).json()[
        "entries"
    ]
    events = [
        entry["data"]
        for entry in entries
        if entry.get("data", {}).get("event", "").startswith("vault_explorer_delete_")
    ]
    started = [
        event for event in events if event["event"] == "vault_explorer_delete_started"
    ]
    rejected = [
        event for event in events if event["event"] == "vault_explorer_delete_rejected"
    ]
    completed = [
        event for event in events if event["event"] == "vault_explorer_delete_completed"
    ]
    assert len(started) == 3 and len(rejected) == 2 and len(completed) == 1
    assert {event["activity_id"] for event in started} == {
        event["activity_id"] for event in rejected + completed
    }
    assert all(
        event["vault_name"] == vault.name and event["status"] for event in events
    )
    assert len({event["issue"] for event in rejected}) == 2
    assert all(event["error_type"] and event["error"] for event in rejected)
    assert completed[0]["activity_id"] == success.json()["activity_id"]
    assert not any(
        entry.get("data", {}).get("event") == "vault_mutation_recorded"
        for entry in entries
    )
    assert any(
        entry.get("data", {}).get("event") == "vault_file_favorites_updated"
        and entry["data"]["status"] == "completed"
        for entry in entries
    )
    assert "PRIVATE_FILE_CONTENT_SENTINEL" not in json.dumps(entries)
    app.evidence.save_json("deletion-system-activity.json", events)


async def test_completion_record_failure_leaves_deletion_recoverable_and_logs_terminal(
    app: Application,
):
    vault = app.vault("DeletionFinalization")
    app.write(vault, "Recover.md", "restore after finalization failure")
    await app.start()
    database = app.root / "system/vault_state.db"
    # A real SQLite failure rejects only the completed lifecycle transition.
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TRIGGER reject_activity_completion BEFORE UPDATE ON vault_activities WHEN NEW.status='completed' BEGIN SELECT RAISE(ABORT, 'controlled activity completion failure'); END"
        )
    try:
        response = app.request(
            f"/api/vaults/{vault.name}/paths/delete-batch",
            "POST",
            {"sources": ["Recover.md"], "confirmed_sources": ["Recover.md"]},
        )
        assert response.status_code != 200
        assert not (vault / "Recover.md").exists()
        with sqlite3.connect(database) as connection:
            activity_id, status = connection.execute(
                "SELECT activity_id, status FROM vault_activities WHERE vault_name=? ORDER BY created_at DESC LIMIT 1",
                (vault.name,),
            ).fetchone()
        assert status == "failed"
    finally:
        with sqlite3.connect(database) as connection:
            connection.execute("DROP TRIGGER reject_activity_completion")
    plan = preview_rollback(app, vault.name, activity_id)
    assert plan["can_rollback"] is True, plan
    assert execute_rollback(app, vault.name, activity_id, plan).status_code == 200
    assert (vault / "Recover.md").read_text() == "restore after finalization failure"
    rows = app.request("/api/system/activity-log", params={"limit": 1000}).json()[
        "entries"
    ]
    terminals = [
        row["data"]
        for row in rows
        if row.get("data", {}).get("event") == "vault_explorer_delete_failed"
        and row["data"].get("activity_id") == activity_id
    ]
    assert len(terminals) == 1
    assert terminals[0]["status"] == "failed"
    assert terminals[0]["error_type"] and terminals[0]["error"]
