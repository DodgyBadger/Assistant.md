"""Real global-reset conflicts and interrupted/contended legacy imports."""

import json
import sqlite3
from threading import Thread

import pytest

from validation.scenarios.support.settings import run_settings_process
from validation.scenarios.support.settings_commit_barrier import (
    pause_next_settings_save,
)

pytestmark = pytest.mark.scenario


async def test_global_reset_public_request_preserves_a_post_backup_edit(app):
    await app.start()
    key = "default_api_timeout"
    assert (
        app.request(
            f"/api/system/settings/general/{key}", "PUT", {"value": "11"}
        ).status_code
        == 200
    )
    original = app.configuration()["settings"][key]
    root = app.root / "system"
    before = set(root.rglob("settings.db.bak*"))
    errors = []
    with pause_next_settings_save() as gate:

        def edit():
            try:
                assert gate.entered.wait(10)
                # A real independent writer commits after the reset's backup.
                with sqlite3.connect(root / "settings.db") as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    conn.execute(
                        "UPDATE configuration_settings SET payload=? WHERE name=?",
                        (
                            json.dumps({**original, "value": 12, "source": "custom"}),
                            key,
                        ),
                    )
                    conn.execute(
                        "UPDATE configuration_state SET revision=revision+1 WHERE singleton=1"
                    )
            except BaseException as error:
                errors.append(error)
            finally:
                gate.release.set()

        writer = Thread(target=edit, daemon=True)
        writer.start()
        try:
            rejected = await app.request_async("/api/system/settings/defaults", "POST")
        finally:
            gate.release.set()
            writer.join(timeout=10)
    assert not errors and not writer.is_alive() and not gate.timed_out.is_set()
    assert (
        rejected.status_code == 400
        and rejected.json()["error"] == "InvalidSystemSetting"
    )
    assert app.configuration()["settings"][key]["value"] == 12
    backups = set(root.rglob("settings.db.bak*")) - before
    assert len(backups) == 1
    with sqlite3.connect(next(iter(backups))) as conn:
        saved = json.loads(
            conn.execute(
                "SELECT payload FROM configuration_settings WHERE name=?", (key,)
            ).fetchone()[0]
        )
    assert saved["value"] == 11


async def test_first_import_transaction_rolls_back_partial_records_and_retries(app):
    await app.start()
    root = app.root / "interrupted-import"
    root.mkdir()
    legacy = root / "settings.yaml"
    legacy.write_text("settings:\n  debug: {value: true}\n  logfire: {value: false}\n")
    run_settings_process(
        app,
        root,
        """
import json
from pathlib import Path
from core.settings import repository, store
root=Path("""
        + repr(str(root))
        + """)
with repository.connect(root) as conn:
    conn.execute("CREATE TRIGGER interrupt_import BEFORE INSERT ON configuration_settings WHEN NEW.name = 'logfire' BEGIN SELECT RAISE(ABORT, 'controlled import interruption'); END")
try:
    store.load_settings()
except Exception as exc:
    failure=type(exc).__name__
else:
    raise AssertionError("The real transaction must fail")
with repository.connect(root) as conn:
    assert repository.revision(conn) is None
    assert conn.execute("SELECT count(*) FROM configuration_settings").fetchone()[0] == 0
    conn.execute("DROP TRIGGER interrupt_import")
store.refresh_settings_cache()
recovered=store.load_settings()
assert recovered.settings["debug"].value is True
assert recovered.settings["logfire"].value is False
(root/"observed.json").write_text(json.dumps({"failure":failure,"recovered":True,"partial_records":0}))
""",
    )
    result = json.loads((root / "observed.json").read_text())
    assert result["recovered"] and result["partial_records"] == 0
    app.evidence.save_json("interrupted-import.json", result)


async def test_legacy_backup_contains_the_snapshot_read_before_source_changes(app):
    await app.start()
    root = app.root / "changing-import"
    root.mkdir()
    original = "settings:\n  debug: {value: true}\n"
    (root / "settings.yaml").write_text(original)
    run_settings_process(
        app,
        root,
        """
import json,sys
from pathlib import Path
from core.settings import store
from core.migration_backups import prepare_migration_backup_path
root=Path("""
        + repr(str(root))
        + """)
changed=[]
def observe(frame,event,arg):
    if event == "call" and frame.f_code is prepare_migration_backup_path.__code__ and not changed:
        # Observe the real backup allocation boundary after the source read.
        # An independent file edit changes the environment; original load and
        # backup implementations execute unchanged and must use captured bytes.
        changed.append(True)
        (root/"settings.yaml").write_text('settings:\\n  debug: {value: false}\\n')
assert sys.getprofile() is None
sys.setprofile(observe)
try:
    loaded=store.load_settings()
finally:
    sys.setprofile(None)
assert changed and loaded.settings["debug"].value is True
backups=list((root/"migration_backups").glob("settings.yaml.bak*"))
assert len(backups)==1
(root/"observed.json").write_text(json.dumps({"backup":backups[0].read_text(),"current_source":(root/"settings.yaml").read_text(),"loaded":loaded.settings["debug"].value}))
""",
    )
    result = json.loads((root / "observed.json").read_text())
    assert (
        result["backup"] == original
        and result["current_source"] != original
        and result["loaded"] is True
    )
    app.evidence.save_json("legacy-source-backup.json", result)
