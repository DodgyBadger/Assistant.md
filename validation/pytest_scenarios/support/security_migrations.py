"""Synthetic security inputs, isolated process execution and legacy database fixtures."""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from core.connections import (
    BuiltInConnectionService,
    GmailPreferences,
    GoogleConnection,
    GoogleConnectionCreate,
    GoogleConnectionUpdate,
)
from core.identity import LOCAL_USER_AUTHORITY
from core.secrets import SecretIntegrityError
from validation.pytest_scenarios.application import Application


def register_synthetic_secrets(app: Application) -> None:
    for value in (
        "owner-value",
        "gmail-value",
        "other-value",
        "legacy-value",
        "canonical-value",
        "new-value",
        "old-value",
        "new-token",
        "old-token",
        "owner-token",
        "owner-pending",
        "child-token",
        "other-token",
        "fence-v1",
        "guarded-token",
        "stale-fence",
        "stale-overwrite",
        "copy-value",
        "keep-value",
        "rollback-value",
        "fixture-secret",
        base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("="),
        base64.urlsafe_b64encode(b"x" * 32).decode(),
    ):
        app.evidence.register_secret(value)


def isolated_process(
    app: Application, source: str, *arguments: str, key: str = ""
) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "ASSISTANTMD_SECRETS_KEY": key,
            "ASSISTANTMD_SECRETS_KEYS": "",
            "ASSISTANTMD_SECRETS_ACTIVE_KEY_VERSION": "",
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", source, *arguments],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    app.evidence.append(
        "security-processes.jsonl",
        {
            "arguments": arguments,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        },
    )
    assert result.returncode == 0, result.stderr


def create_legacy_chat_sessions_db(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
                CREATE TABLE chat_sessions (
                    session_id TEXT NOT NULL,
                    vault_name TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_activity_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    title TEXT,
                    metadata_json TEXT,
                    PRIMARY KEY (session_id, vault_name)
                )
                """)
        conn.execute("""
                INSERT INTO chat_sessions (session_id, vault_name)
                VALUES ('legacy', 'MigrationVault')
                """)


def create_pre_retirement_chat_sessions_db(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
                CREATE TABLE schema_migrations (
                    namespace TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (namespace, version)
                )
                """)
        conn.executemany(
            """
                INSERT INTO schema_migrations (namespace, version, name)
                VALUES ('chat_sessions', ?, ?)
                """,
            (
                (1, "add_compaction_checkpoints"),
                (2, "add_session_owner_principal"),
                (3, "add_live_session_map_storage"),
                (4, "add_live_session_map_attempt_audit"),
            ),
        )
        for table_name in (
            "chat_session_map_revisions",
            "chat_session_map_maintenance",
            "chat_session_map_attempts",
        ):
            conn.execute(
                f"CREATE TABLE {table_name} (id INTEGER PRIMARY KEY, sentinel TEXT NOT NULL)"
            )
            conn.execute(
                f"INSERT INTO {table_name} (id, sentinel) VALUES (1, ?)",
                (f"retired:{table_name}",),
            )


def create_legacy_ingestion_jobs_db(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
                CREATE TABLE ingestion_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_uri VARCHAR NOT NULL,
                    vault VARCHAR,
                    source_type VARCHAR NOT NULL,
                    mime_hint VARCHAR,
                    options JSON,
                    status VARCHAR NOT NULL,
                    error TEXT,
                    outputs JSON,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """)


def migration_versions(conn: sqlite3.Connection, namespace: str) -> list[int]:
    return [
        int(row[0])
        for row in conn.execute(
            """
                SELECT version
                FROM schema_migrations
                WHERE namespace = ?
                ORDER BY version
                """,
            (namespace,),
        ).fetchall()
    ]


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table'
              AND name = ?
            LIMIT 1
            """,
        (table_name,),
    ).fetchone()
    return row is not None


@contextmanager
def migration_bootstrap_roots(data: Path, system: Path) -> Iterator[None]:
    from core.runtime.paths import (
        restore_bootstrap_roots,
        set_bootstrap_roots,
        snapshot_bootstrap_roots,
    )

    roots = snapshot_bootstrap_roots()
    set_bootstrap_roots(data, system)
    try:
        yield
    finally:
        restore_bootstrap_roots(roots)


def activity_data(root: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)["data"]
        for line in (root / "activity.log").read_text().splitlines()
        if "data" in json.loads(line)
    ]


def assert_operation_pairs(rows: list[dict[str, Any]]) -> None:
    for operation_id in {row["operation_id"] for row in rows}:
        operation = [row for row in rows if row["operation_id"] == operation_id]
        assert len([row for row in operation if row["status"] == "started"]) == 1
        assert (
            len([row for row in operation if row["status"] in {"completed", "failed"}])
            == 1
        )


def assert_locked_metadata(service: BuiltInConnectionService, kind: str) -> None:
    authority = LOCAL_USER_AUTHORITY
    request = GoogleConnectionCreate(display_name="Blocked", client_id="blocked")
    existing = GoogleConnection(
        connection_id="blocked",
        slug="blocked",
        display_name="Blocked",
        client_id="blocked",
        is_default=True,
        gmail=GmailPreferences(),
        config_version=1,
        oauth_generation=1,
        created_at="",
        updated_at="",
    )
    with closing(sqlite3.connect(":memory:")) as conn:
        actions = (
            service.list_google_connections,
            lambda: service.list_google_connections_for_authority(authority),
            service.get_google_connection,
            lambda: service.get_google_connection_for_authority(authority),
            lambda: service.get_google_connection_by_slug_for_authority(
                authority, "blocked"
            ),
            lambda: service.create_google_connection(request),
            lambda: service.create_google_connection_for_authority(authority, request),
            lambda: service.require_google_connection_on_connection(
                conn, authority, "blocked"
            ),
            lambda: service.update_google_connection_on_connection(
                conn, authority, existing, GoogleConnectionUpdate(client_id="changed")
            ),
        )
        for action in actions:
            try:
                action()
            except SecretIntegrityError:
                pass
            else:
                raise AssertionError(
                    f"{kind} direct metadata entrypoints must reject while locked"
                )


def prepare_locked_database(root: Path, kind: str) -> bytes | None:
    from core.secrets import EncryptedSecretsService, SecretKeyring

    root.mkdir(parents=True, exist_ok=True)
    path = root / "access.db"
    if kind == "wrong-key":
        service = EncryptedSecretsService(
            system_root=str(root),
            keyring=SecretKeyring(keys={1: bytes(range(32))}, active_version=1),
        )
        service.set_for_authority(
            LOCAL_USER_AUTHORITY, "configuration", "OPENAI_API_KEY", "fixture-secret"
        )
    elif kind == "corrupt-file":
        path.write_bytes(b"not a sqlite database")
    elif kind == "malformed-schema":
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE encrypted_secrets (unrelated TEXT)")
    return path.read_bytes() if path.exists() else None


async def exercise_locked_startup(root: Path, kind: str) -> None:
    """Bootstrap every real runtime service and inspect public locked entrypoints."""
    from api.exceptions import APIException
    from api.models import GoogleConnectionCreateRequest
    from api.services.google_connections import (
        create_google_connection,
        list_google_connections,
    )
    from api.services.maintenance import get_system_database_migration_status
    from core.identity import use_execution_authority
    from core.runtime.bootstrap import bootstrap_runtime
    from core.runtime.config import RuntimeConfig
    from core.runtime.state import has_runtime_context
    from core.secrets import (
        get_secrets_bootstrap_status,
        reset_secrets_bootstrap_status,
    )
    from validation.support.configuration import (
        install_configuration,
        read_configuration,
        seed_configuration,
    )

    config = RuntimeConfig.for_validation(root, root / "data")
    seed_configuration(config.system_root)
    configured = read_configuration(config.system_root)
    configured["settings"]["default_model"] = {"value": "test", "source": "custom"}
    install_configuration(config.system_root, configured)
    runtime = None
    try:
        runtime = await bootstrap_runtime(config)
        status = get_secrets_bootstrap_status()
        assert status is not None and (not status.ready)
        assert runtime.google_connection is None and runtime.mcp_connections is None
        access = next(
            target
            for target in get_system_database_migration_status().targets
            if target.db_name == "access"
        )
        assert access.inspection_error
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            assert_locked_metadata(runtime.built_in_connections, kind)
            for action in (
                list_google_connections,
                lambda: create_google_connection(
                    GoogleConnectionCreateRequest(
                        display_name="Blocked", client_id="blocked-client"
                    )
                ),
            ):
                try:
                    action()
                except APIException:
                    pass
                else:
                    raise AssertionError("Locked Google API must reject access")
        print(
            json.dumps(
                {
                    "kind": kind,
                    "state": status.state,
                    "inspection_error": access.inspection_error,
                    "credential_services_disabled": runtime.google_connection is None
                    and runtime.mcp_connections is None,
                }
            )
        )
    finally:
        if runtime is not None:
            await runtime.shutdown()
        reset_secrets_bootstrap_status()
        assert not has_runtime_context()


def create_probe_table(connection: sqlite3.Connection) -> None:
    connection.execute("CREATE TABLE probe (value TEXT NOT NULL)")


def insert_probe_row(connection: sqlite3.Connection) -> None:
    connection.execute("INSERT INTO probe (value) VALUES ('applied')")
