"""SIGKILL recovery preserves exactly one durable legacy-secret import."""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.secrets_crash_recovery import (
    cold_start_process,
    create_crash_installation,
    kill_at_boundary,
    migration_state,
    remove_workload,
)

pytestmark = pytest.mark.scenario


@pytest.mark.parametrize("boundary", ["during-import", "after-import", "after-rename"])
async def test_legacy_secret_import_recovers_after_process_death(
    app: Application, boundary
):
    await app.start()
    from core.identity import LOCAL_USER_AUTHORITY
    from core.secrets.legacy_migration import migrate_legacy_secrets_yaml

    root, service, source, count = create_crash_installation(app, boundary)
    await asyncio.to_thread(kill_at_boundary, app, root, boundary)
    killed = migration_state(root)
    if boundary == "during-import":
        assert killed == {
            "secret_count": 0,
            "phases": [],
            "item_count": 0,
            "source_exists": True,
            "backup_exists": False,
        }
    else:
        assert killed["secret_count"] == count and killed["item_count"] == count
        assert killed["phases"] == [("imported", count)]
        assert killed["source_exists"] is (boundary == "after-import")
        assert killed["backup_exists"] is (boundary == "after-rename")
    remove_workload(root)
    recovered = migrate_legacy_secrets_yaml(system_root=root, service=service)
    assert (
        recovered.phase == "complete"
        and recovered.imported_count == count
        and recovered.source_retired
    )
    assert migration_state(root) == {
        "secret_count": count,
        "phases": [("complete", count)],
        "item_count": count,
        "source_exists": False,
        "backup_exists": True,
    }
    assert (root / "migration_backups/secrets.yaml.bak").read_text() == source
    for index in (0, count - 1):
        assert (
            service.get_for_authority(
                LOCAL_USER_AUTHORITY, "configuration", f"CRASH_KEY_{index}"
            )
            == "crash-migration-value"
        )
    service.verify_all()
    with sqlite3.connect(root / "access.db") as conn:
        ciphertext = conn.execute(
            "SELECT name,ciphertext FROM encrypted_secrets ORDER BY name"
        ).fetchall()
    assert {name for name, _ in ciphertext} == {
        f"CRASH_KEY_{index}" for index in range(count)
    }
    assert all(b"crash-migration-value" not in value for _, value in ciphertext)
    repeated = migrate_legacy_secrets_yaml(system_root=root, service=service)
    assert repeated == recovered
    with sqlite3.connect(root / "access.db") as conn:
        assert (
            conn.execute(
                "SELECT name,ciphertext FROM encrypted_secrets ORDER BY name"
            ).fetchall()
            == ciphertext
        )
    assert len(list((root / "migration_backups").iterdir())) == 1


@pytest.mark.parametrize("mode", ["cold", "prewarm"])
async def test_locked_production_bootstrap_keeps_managed_writes_in_configured_roots(
    app: Application, mode
):
    await app.start()
    root = app.root / f"locked-{mode}"
    observed = await asyncio.to_thread(cold_start_process, app, root, mode)
    assert observed["state"] == "locked" and observed["access_exists"] is False
    assert not (root / "system/access.db").exists()
    assert "system/chat_sessions.db" in observed["files"]
    assert any(
        name.startswith("system/migration_backups/chat_sessions.db.backup-")
        for name in observed["files"]
    )
    assert all(name.startswith(("system/", "data/")) for name in observed["files"])
