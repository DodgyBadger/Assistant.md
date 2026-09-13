#!/usr/bin/env python3
"""One-shot dev migration from system/memory.db to system/session_summaries.db."""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.migration_backups import prepare_migration_backup_path  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate AssistantMD dev session-summary data out of memory.db."
    )
    parser.add_argument(
        "--system-root",
        default=str(Path(__file__).resolve().parents[1] / "system"),
        help="Path to the AssistantMD system directory. Defaults to ./system.",
    )
    parser.add_argument(
        "--keep-old",
        action="store_true",
        help="Leave memory.db in place after migration. By default it is renamed after a backup is created.",
    )
    args = parser.parse_args()

    system_root = Path(args.system_root).expanduser().resolve()
    old_db = system_root / "memory.db"
    new_db = system_root / "session_summaries.db"
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    migrated_old_db = system_root / f"memory.db.migrated-{timestamp}"

    if not old_db.exists():
        raise SystemExit(f"Old database not found: {old_db}")
    if new_db.exists():
        raise SystemExit(
            f"Refusing to overwrite existing destination database: {new_db}"
        )

    system_root.mkdir(parents=True, exist_ok=True)
    backup_db = prepare_migration_backup_path(
        system_root, f"memory.db.backup-{timestamp}"
    )
    shutil.copy2(old_db, backup_db)

    conn = sqlite3.connect(new_db)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        _create_schema(conn)
        conn.execute("ATTACH DATABASE ? AS old", (str(old_db),))
        _copy_session_summaries(conn)
        _copy_artifacts(conn)
        _copy_vectors(conn)
        _rebuild_fts(conn)
        conn.commit()
        conn.execute("DETACH DATABASE old")
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        if new_db.exists():
            new_db.unlink()
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if not args.keep_old:
        old_db.rename(migrated_old_db)

    print(f"Migrated session summaries to: {new_db}")
    print(f"Backup created at: {backup_db}")
    if args.keep_old:
        print(f"Original left in place: {old_db}")
    else:
        print(f"Original renamed to: {migrated_old_db}")
    return 0


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE session_summaries (
            session_id TEXT NOT NULL,
            vault_name TEXT NOT NULL,
            title TEXT,
            summary TEXT,
            domain TEXT,
            work_product TEXT,
            user_intent TEXT,
            named_entities TEXT,
            source_summary TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            metadata_json TEXT,
            PRIMARY KEY (session_id, vault_name)
        );
        CREATE INDEX idx_session_summaries_vault_updated
            ON session_summaries(vault_name, updated_at);
        CREATE INDEX idx_session_summaries_vault_domain
            ON session_summaries(vault_name, domain);

        CREATE TABLE session_summary_artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            vault_name TEXT NOT NULL,
            path TEXT NOT NULL,
            artifact_role TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            metadata_json TEXT,
            FOREIGN KEY (session_id, vault_name)
                REFERENCES session_summaries(session_id, vault_name)
                ON DELETE CASCADE,
            UNIQUE (session_id, vault_name, path, artifact_role)
        );
        CREATE INDEX idx_session_summary_artifacts_path
            ON session_summary_artifacts(vault_name, path);

        CREATE VIRTUAL TABLE session_summaries_fts USING fts5(
            session_id UNINDEXED,
            vault_name UNINDEXED,
            title,
            summary,
            domain,
            work_product,
            user_intent,
            named_entities,
            tokenize = 'unicode61'
        );

        CREATE TABLE session_summary_field_vectors (
            namespace TEXT NOT NULL,
            item_id TEXT NOT NULL,
            input_text TEXT NOT NULL,
            input_fingerprint TEXT NOT NULL,
            embedding_space_id TEXT NOT NULL,
            dimensions INTEGER NOT NULL,
            model_alias TEXT NOT NULL,
            provider_name TEXT NOT NULL,
            model_name TEXT NOT NULL,
            vector_json TEXT NOT NULL,
            metadata_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (namespace, item_id, embedding_space_id)
        );
        CREATE INDEX idx_session_summary_field_vectors_space
            ON session_summary_field_vectors(namespace, embedding_space_id, dimensions);
        """
    )


def _copy_session_summaries(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "old", "session_memories"):
        return
    source_summary_expr = (
        "source_summary"
        if _column_exists(conn, "old", "session_memories", "source_summary")
        else "NULL AS source_summary"
    )
    conn.execute(
        f"""
        INSERT INTO session_summaries (
            session_id, vault_name, title, summary, domain, work_product,
            user_intent, named_entities, source_summary, created_at, updated_at,
            metadata_json
        )
        SELECT
            session_id, vault_name, title, summary, domain, work_product,
            user_intent, named_entities, {source_summary_expr}, created_at, updated_at,
            metadata_json
        FROM old.session_memories
        """
    )


def _copy_artifacts(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "old", "session_memory_artifacts"):
        return
    conn.execute(
        """
        INSERT INTO session_summary_artifacts (
            session_id, vault_name, path, artifact_role, created_at, metadata_json
        )
        SELECT session_id, vault_name, path, artifact_role, created_at, metadata_json
        FROM old.session_memory_artifacts
        """
    )


def _copy_vectors(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "old", "session_memory_field_vectors"):
        return
    conn.execute(
        """
        INSERT INTO session_summary_field_vectors (
            namespace, item_id, input_text, input_fingerprint, embedding_space_id,
            dimensions, model_alias, provider_name, model_name, vector_json,
            metadata_json, created_at, updated_at
        )
        SELECT
            replace(namespace, 'session_memory_fields', 'session_summary_fields'),
            item_id, input_text, input_fingerprint, embedding_space_id,
            dimensions, model_alias, provider_name, model_name, vector_json,
            metadata_json, created_at, updated_at
        FROM old.session_memory_field_vectors
        """
    )


def _rebuild_fts(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM session_summaries_fts")
    conn.execute(
        """
        INSERT INTO session_summaries_fts (
            session_id, vault_name, title, summary, domain,
            work_product, user_intent, named_entities
        )
        SELECT
            session_id, vault_name, COALESCE(title, ''), COALESCE(summary, ''),
            COALESCE(domain, ''), COALESCE(work_product, ''),
            COALESCE(user_intent, ''), COALESCE(named_entities, '')
        FROM session_summaries
        """
    )


def _table_exists(conn: sqlite3.Connection, schema: str, table_name: str) -> bool:
    row = conn.execute(
        f"""
        SELECT 1 FROM {schema}.sqlite_master
        WHERE type IN ('table', 'virtual table') AND name = ?
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def _column_exists(
    conn: sqlite3.Connection,
    schema: str,
    table_name: str,
    column_name: str,
) -> bool:
    rows = conn.execute(f"PRAGMA {schema}.table_info({table_name})").fetchall()
    return any(str(row[1]) == column_name for row in rows)


if __name__ == "__main__":
    raise SystemExit(main())
