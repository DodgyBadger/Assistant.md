"""Actual SQLite constraints, process exits and public invalidation callbacks for MCP."""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from contextlib import contextmanager

from validation.scenarios.application import Application


@contextmanager
def reject_access_write(app: Application, *, deletion=False):
    name = "reject_credential_delete" if deletion else "reject_metadata_update"
    statement = (
        "CREATE TRIGGER reject_credential_delete BEFORE DELETE ON encrypted_secrets WHEN OLD.name='credential' BEGIN SELECT RAISE(ABORT, 'controlled deletion failure'); END"
        if deletion
        else "CREATE TRIGGER reject_metadata_update BEFORE UPDATE ON mcp_connections BEGIN SELECT RAISE(ABORT, 'controlled metadata failure'); END"
    )
    with sqlite3.connect(app.root / "system/access.db") as conn:
        conn.execute(statement)
    try:
        yield
    finally:
        with sqlite3.connect(app.root / "system/access.db") as conn:
            conn.execute(f"DROP TRIGGER {name}")


class FailingInvalidation:
    def __init__(self, system_root):
        self.system_root = system_root
        self.observed_versions = []

    def __call__(self, _principal, connection_id):
        from core.access_store import write_transaction

        with write_transaction(str(self.system_root)) as conn:
            row = conn.execute(
                "SELECT config_version FROM mcp_connections WHERE connection_id=?",
                (connection_id,),
            ).fetchone()
            self.observed_versions.append(row[0])
        raise RuntimeError("fixture notification failure")


async def exit_during_mutation(app: Application, connection_id: str, phase: str) -> int:
    script = """
import os, sys
from pathlib import Path
from core.runtime.paths import set_bootstrap_roots
set_bootstrap_roots(Path(sys.argv[1]),Path(sys.argv[2]))
from core.access_store import write_transaction
from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
from core.mcp import MCPConnectionService
from core.secrets import EncryptedSecretsService, SecretKeyring
secrets=EncryptedSecretsService(system_root=sys.argv[2],keyring=SecretKeyring.from_environment())
service=MCPConnectionService(system_root=sys.argv[2],secrets=secrets,on_change=lambda _owner,_id: os._exit(74))
with use_execution_authority(LOCAL_USER_AUTHORITY):
    if sys.argv[4]=='before':
        # Explicit shared-transaction input proves abrupt-death atomicity. It
        # does not locate the death inside the public composed service mutation.
        with write_transaction(sys.argv[2]) as conn:
            secrets.set_for_authority_on_connection(conn,LOCAL_USER_AUTHORITY,'mcp.connection.'+sys.argv[3],'credential','uncommitted-secret')
            conn.execute('UPDATE mcp_connections SET config_version=config_version+1 WHERE connection_id=?',(sys.argv[3],))
            os._exit(73)
    service.set_credential(sys.argv[3],'committed-secret')
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        str(app.root / "data"),
        str(app.root / "system"),
        connection_id,
        phase,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
    except TimeoutError:
        process.kill()
        await process.communicate()
        raise
    app.evidence.append(
        "mcp-mutation-process.jsonl",
        {
            "phase": phase,
            "exit_code": process.returncode,
            "stdout": stdout.decode(),
            "stderr": stderr.decode(),
        },
    )
    return process.returncode
