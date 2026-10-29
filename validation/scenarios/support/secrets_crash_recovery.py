"""Real killed migration processes and independent cold-start observations."""

from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

CHILD_MIGRATION = """
import json, sys
from pathlib import Path
from core.runtime.paths import set_bootstrap_roots
root = Path(sys.argv[1])
set_bootstrap_roots(root / 'data', root)
from core.secrets import EncryptedSecretsService, SecretKeyring
from core.secrets.legacy_migration import migrate_legacy_secrets_yaml
service = EncryptedSecretsService(system_root=str(root), keyring=SecretKeyring.from_environment(), initialize_schema=False)
print('migration-entered', flush=True)
result = migrate_legacy_secrets_yaml(system_root=root, service=service)
print(json.dumps({'phase': result.phase, 'imported_count': result.imported_count}), flush=True)
"""

COLD_START = """
import asyncio, json, sys
from pathlib import Path
root = Path(sys.argv[1]); mode = sys.argv[2]
from core.runtime.paths import set_bootstrap_roots
set_bootstrap_roots(root / 'data', root / 'system')
if mode == 'prewarm':
    from core.secrets import initialize_secrets_bootstrap
from core.runtime.bootstrap import bootstrap_runtime
from core.runtime.config import RuntimeConfig
from core.secrets import get_secrets_bootstrap_status, require_secrets_ready, SecretIntegrityError
from validation.support.configuration import seed_configuration, read_configuration, install_configuration
seed_configuration(root / 'system')
configuration = read_configuration(root / 'system')
configuration['settings']['default_model'] = {'value': 'test', 'source': 'custom'}
install_configuration(root / 'system', configuration)
async def main():
    runtime = await bootstrap_runtime(RuntimeConfig.for_production(root / 'data', root / 'system'))
    try:
        assert get_secrets_bootstrap_status().state == 'locked'
        try:
            require_secrets_ready()
        except SecretIntegrityError:
            pass
        else:
            raise AssertionError('Locked startup must reject secret execution')
        files = sorted(str(path.relative_to(root)) for path in root.rglob('*') if path.is_file())
        assert not (root / 'system/access.db').exists()
        print('COLD_RESULT ' + json.dumps({'state': 'locked', 'files': files, 'access_exists': False}), flush=True)
    finally:
        await runtime.shutdown()
asyncio.run(main())
"""


def process_environment(root: Path, *, locked=False):
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path.cwd())
    environment["CONTAINER_DATA_ROOT"] = str(root / "data")
    environment["CONTAINER_SYSTEM_ROOT"] = str(root / "system" if locked else root)
    environment.pop("LOGFIRE_TOKEN", None)
    if locked:
        for name in (
            "ASSISTANTMD_SECRETS_KEY",
            "ASSISTANTMD_SECRETS_KEYS",
            "ASSISTANTMD_SECRETS_ACTIVE_KEY_VERSION",
        ):
            environment.pop(name, None)
    return environment


def create_crash_installation(app, boundary):
    from core.secrets import EncryptedSecretsService, SecretKeyring

    root = app.root / f"secrets-crash-{boundary}"
    root.mkdir()
    count = 3000 if boundary == "after-import" else 2
    app.evidence.register_secret("crash-migration-value")
    source = "".join(
        f"CRASH_KEY_{index}: crash-migration-value\n" for index in range(count)
    )
    (root / "secrets.yaml").write_text(source)
    service = EncryptedSecretsService(
        system_root=str(root), keyring=SecretKeyring.from_environment()
    )
    if boundary != "after-import":
        table, event = (
            ("encrypted_secrets", "AFTER INSERT")
            if boundary == "during-import"
            else ("secrets_bootstrap_migrations", "BEFORE UPDATE")
        )
        condition = "" if boundary == "during-import" else "WHEN NEW.phase='complete'"
        with sqlite3.connect(root / "access.db") as conn:
            # A native SQL workload holds the genuine migration transaction; no
            # production callback or migration strategy is replaced.
            conn.execute(
                f"""CREATE TRIGGER migration_workload {event} ON {table} {condition}
                BEGIN SELECT sum(x) FROM (WITH RECURSIVE wait(x) AS
                    (VALUES(0) UNION ALL SELECT x+1 FROM wait WHERE x<1000000000)
                    SELECT x FROM wait); END"""
            )
    return root, service, source, count


def migration_state(root):
    with sqlite3.connect(root / "access.db", timeout=0.01) as conn:
        return {
            "secret_count": conn.execute(
                "SELECT count(*) FROM encrypted_secrets"
            ).fetchone()[0],
            "phases": conn.execute(
                "SELECT phase, imported_count FROM secrets_bootstrap_migrations"
            ).fetchall(),
            "item_count": conn.execute(
                "SELECT count(*) FROM secrets_bootstrap_import_items"
            ).fetchone()[0],
            "source_exists": (root / "secrets.yaml").exists(),
            "backup_exists": (root / "migration_backups/secrets.yaml.bak").exists(),
        }


def kill_at_boundary(app, root, boundary):
    process = subprocess.Popen(
        [sys.executable, "-c", CHILD_MIGRATION, str(root)],
        env=process_environment(root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    observed = None
    try:
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            assert (
                process.poll() is None
            ), "Migration finished before the requested crash boundary"
            state = migration_state(root)
            if boundary == "during-import":
                with sqlite3.connect(root / "access.db", timeout=0) as conn:
                    try:
                        conn.execute("BEGIN IMMEDIATE")
                    except sqlite3.OperationalError as error:
                        assert "locked" in str(error)
                        reached = True
                    else:
                        conn.rollback()
                        reached = False
                reached = reached and state["secret_count"] == 0 and not state["phases"]
            else:
                reached = (
                    state["phases"]
                    and state["phases"][0][0] == "imported"
                    and (
                        state["source_exists"]
                        if boundary == "after-import"
                        else state["backup_exists"] and not state["source_exists"]
                    )
                )
            if reached:
                observed = state
                os.kill(process.pid, signal.SIGKILL)
                break
            time.sleep(0.002)
        assert (
            observed is not None
        ), "Never observed requested durable migration boundary"
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == -signal.SIGKILL
        app.evidence.append(
            "secret-migration-crashes.jsonl",
            {
                "boundary": boundary,
                "observed": observed,
                "after_kill": migration_state(root),
                "exit_code": process.returncode,
                "stdout": stdout,
                "stderr": stderr,
            },
        )
        return observed
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def remove_workload(root):
    with sqlite3.connect(root / "access.db") as conn:
        conn.execute("DROP TRIGGER IF EXISTS migration_workload")


def cold_start_process(app, root, mode):
    working = root / "process-cwd"
    working.mkdir(parents=True)
    result = subprocess.run(
        [sys.executable, "-c", COLD_START, str(root), mode],
        cwd=working,
        env=process_environment(root, locked=True),
        capture_output=True,
        text=True,
        timeout=45,
    )
    app.evidence.append(
        "cold-secrets-startup.jsonl",
        {
            "mode": mode,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        },
    )
    assert result.returncode == 0, result.stderr
    import json

    return json.loads(
        next(
            line.removeprefix("COLD_RESULT ")
            for line in result.stdout.splitlines()
            if line.startswith("COLD_RESULT ")
        )
    )
