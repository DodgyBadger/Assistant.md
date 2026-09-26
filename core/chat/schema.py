"""SQLite schema helpers for durable chat sessions."""

from __future__ import annotations

import sqlite3

from core.database import connect_sqlite_from_system_db
from core.database_migrations import SQLiteMigration, apply_sqlite_migrations
from core.identity import LOCAL_USER_PRINCIPAL_ID

DB_NAME = "chat_sessions"
MIGRATION_NAMESPACE = "chat_sessions"

CHAT_SESSION_MIGRATIONS = (
    SQLiteMigration(
        version=1,
        name="add_compaction_checkpoints",
        apply=lambda conn: _migrate_compaction_checkpoints(conn),
    ),
    SQLiteMigration(
        version=2,
        name="add_session_owner_principal",
        apply=lambda conn: _migrate_session_owners(conn),
    ),
    SQLiteMigration(
        version=3,
        name="add_live_session_map_storage",
        apply=lambda conn: _migrate_live_session_map_storage(conn),
    ),
    SQLiteMigration(
        version=4,
        name="add_live_session_map_attempt_audit",
        apply=lambda conn: _migrate_live_session_map_attempt_audit(conn),
    ),
)


def ensure_chat_sessions_schema(
    system_root: str | None = None,
    *,
    apply_migrations: bool = False,
) -> None:
    """Create chat-session tables when they do not already exist."""
    conn = connect_sqlite_from_system_db(DB_NAME, system_root)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_sessions (
                session_id TEXT NOT NULL,
                vault_name TEXT NOT NULL,
                owner_principal_id TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_activity_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                title TEXT,
                metadata_json TEXT,
                PRIMARY KEY (session_id, vault_name)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                vault_name TEXT NOT NULL,
                sequence_index INTEGER NOT NULL,
                direction TEXT NOT NULL,
                message_type TEXT NOT NULL,
                role TEXT NOT NULL,
                content_text TEXT,
                message_json TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (session_id, vault_name, sequence_index),
                FOREIGN KEY (session_id, vault_name)
                    REFERENCES chat_sessions(session_id, vault_name)
                    ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_chat_messages_session_sequence
            ON chat_messages(session_id, vault_name, sequence_index)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_chat_messages_session_created
            ON chat_messages(session_id, vault_name, created_at)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_tool_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                vault_name TEXT NOT NULL,
                tool_call_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                event_type TEXT NOT NULL,
                args_json TEXT,
                result_text TEXT,
                result_metadata_json TEXT,
                artifact_ref TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id, vault_name)
                    REFERENCES chat_sessions(session_id, vault_name)
                    ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_chat_tool_events_session_created
            ON chat_tool_events(session_id, vault_name, created_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_chat_tool_events_call_id
            ON chat_tool_events(session_id, vault_name, tool_call_id)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_edit_proposals (
                artifact_ref TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                vault_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                proposal_json TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                applied_at DATETIME,
                FOREIGN KEY (session_id, vault_name)
                    REFERENCES chat_sessions(session_id, vault_name)
                    ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_chat_edit_proposals_session
            ON chat_edit_proposals(session_id, vault_name, created_at)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_deferred_reviews (
                artifact_ref TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                vault_name TEXT NOT NULL,
                originating_task_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                requests_json TEXT NOT NULL,
                resume_messages_json TEXT NOT NULL,
                resume_config_json TEXT,
                review_context_json TEXT,
                result_json TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                submitted_at DATETIME,
                resumed_task_id TEXT,
                error_json TEXT,
                FOREIGN KEY (session_id, vault_name)
                    REFERENCES chat_sessions(session_id, vault_name)
                    ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_chat_deferred_reviews_session
            ON chat_deferred_reviews(session_id, vault_name, created_at)
            """
        )
        _ensure_column(
            conn,
            "chat_deferred_reviews",
            "resume_config_json",
            "TEXT",
        )
        _ensure_column(
            conn,
            "chat_deferred_reviews",
            "review_context_json",
            "TEXT",
        )
        _deduplicate_session_ids(conn)
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_sessions_session_id_unique
            ON chat_sessions(session_id)
            """
        )
        _migrate_compaction_checkpoints(conn)
        _migrate_session_owners(conn)
        _migrate_live_session_map_storage(conn)
        _migrate_live_session_map_attempt_audit(conn)
        conn.commit()
        if apply_migrations:
            apply_sqlite_migrations(
                conn, namespace=MIGRATION_NAMESPACE, migrations=CHAT_SESSION_MIGRATIONS
            )
            conn.commit()
    finally:
        conn.close()


def _migrate_compaction_checkpoints(conn: sqlite3.Connection) -> None:
    """Add append-only chat compaction checkpoint storage."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_compaction_checkpoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            checkpoint_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            vault_name TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            source TEXT NOT NULL,
            message_count_before INTEGER NOT NULL,
            last_message_sequence_index INTEGER NOT NULL,
            summary_message_json TEXT NOT NULL,
            replacement_history_json TEXT NOT NULL,
            metadata_json TEXT,
            UNIQUE (checkpoint_id),
            FOREIGN KEY (session_id, vault_name)
                REFERENCES chat_sessions(session_id, vault_name)
                ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_chat_compaction_checkpoints_session_id
        ON chat_compaction_checkpoints(session_id, vault_name, id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_chat_compaction_checkpoints_session_sequence
        ON chat_compaction_checkpoints(session_id, vault_name, last_message_sequence_index)
        """
    )


def _ensure_column(
    conn: sqlite3.Connection,
    table_name: str,
    column_name: str,
    definition: str,
) -> None:
    """Add a column to an existing SQLite table when it is missing."""
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    existing = {str(row[1]) for row in rows}
    if column_name not in existing:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def _migrate_session_owners(conn: sqlite3.Connection) -> None:
    """Assign legacy sessions to the implicit local user."""
    _ensure_column(
        conn,
        "chat_sessions",
        "owner_principal_id",
        f"TEXT NOT NULL DEFAULT '{LOCAL_USER_PRINCIPAL_ID}'",
    )
    conn.execute(
        """
        UPDATE chat_sessions
        SET owner_principal_id = ?
        WHERE owner_principal_id IS NULL OR TRIM(owner_principal_id) = ''
        """,
        (LOCAL_USER_PRINCIPAL_ID,),
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_chat_sessions_owner_activity
        ON chat_sessions(owner_principal_id, last_activity_at)
        """
    )


def _migrate_live_session_map_storage(conn: sqlite3.Connection) -> None:
    """Add source-linked session-map revisions and maintenance state."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_session_map_revisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            vault_name TEXT NOT NULL,
            revision INTEGER NOT NULL,
            predecessor_revision INTEGER NOT NULL,
            updated_through_sequence_index INTEGER NOT NULL,
            observed_source_content_revision INTEGER NOT NULL,
            map_json TEXT NOT NULL,
            operations_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (session_id, vault_name, revision),
            FOREIGN KEY (session_id, vault_name)
                REFERENCES chat_sessions(session_id, vault_name)
                ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_chat_session_map_latest
        ON chat_session_map_revisions(session_id, vault_name, revision DESC)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_session_map_maintenance (
            session_id TEXT NOT NULL,
            vault_name TEXT NOT NULL,
            observed_through_sequence_index INTEGER NOT NULL DEFAULT -1,
            observed_source_content_revision INTEGER NOT NULL DEFAULT 0,
            pending_turn_count INTEGER NOT NULL DEFAULT 0,
            pending_token_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'idle',
            frozen_from_sequence_index INTEGER,
            frozen_through_sequence_index INTEGER,
            frozen_predecessor_revision INTEGER,
            frozen_source_content_revision INTEGER,
            frozen_pending_turn_count INTEGER,
            frozen_pending_token_count INTEGER,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_error_json TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (session_id, vault_name),
            FOREIGN KEY (session_id, vault_name)
                REFERENCES chat_sessions(session_id, vault_name)
                ON DELETE CASCADE
        )
        """
    )


def _migrate_live_session_map_attempt_audit(conn: sqlite3.Connection) -> None:
    """Add durable classifier and author diagnostics for shadow maintenance."""
    _ensure_column(
        conn,
        "chat_session_map_maintenance",
        "decision_checked_through_sequence_index",
        "INTEGER NOT NULL DEFAULT -1",
    )
    _ensure_column(
        conn,
        "chat_session_map_maintenance",
        "decision_checked_pending_turn_count",
        "INTEGER NOT NULL DEFAULT 0",
    )
    _ensure_column(
        conn,
        "chat_session_map_maintenance",
        "last_decision_json",
        "TEXT",
    )
    _ensure_column(
        conn,
        "chat_session_map_maintenance",
        "last_authoring_json",
        "TEXT",
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_session_map_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            vault_name TEXT NOT NULL,
            attempt_number INTEGER NOT NULL,
            task_id TEXT,
            status TEXT NOT NULL,
            from_sequence_index INTEGER NOT NULL,
            through_sequence_index INTEGER NOT NULL,
            predecessor_revision INTEGER NOT NULL,
            source_content_revision INTEGER NOT NULL,
            pending_turn_count INTEGER NOT NULL,
            pending_token_count INTEGER NOT NULL,
            decision_json TEXT,
            authoring_json TEXT,
            error_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT,
            UNIQUE (session_id, vault_name, attempt_number),
            FOREIGN KEY (session_id, vault_name)
                REFERENCES chat_sessions(session_id, vault_name)
                ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_chat_session_map_attempts_session
        ON chat_session_map_attempts(session_id, vault_name, attempt_number DESC)
        """
    )


def _deduplicate_session_ids(conn: sqlite3.Connection) -> None:
    """Ensure historical composite-key sessions have globally unique IDs."""
    duplicate_rows = conn.execute(
        """
        SELECT session_id
        FROM chat_sessions
        GROUP BY session_id
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    for (session_id,) in duplicate_rows:
        sessions = conn.execute(
            """
            SELECT rowid, vault_name
            FROM chat_sessions
            WHERE session_id = ?
            ORDER BY created_at ASC, rowid ASC
            """,
            (session_id,),
        ).fetchall()
        for index, (rowid, vault_name) in enumerate(sessions[1:], start=1):
            new_session_id = _deduplicated_session_id(
                conn,
                session_id=str(session_id),
                vault_name=str(vault_name),
                index=index,
            )
            conn.execute(
                """
                UPDATE chat_messages
                SET session_id = ?
                WHERE session_id = ? AND vault_name = ?
                """,
                (new_session_id, session_id, vault_name),
            )
            conn.execute(
                """
                UPDATE chat_tool_events
                SET session_id = ?
                WHERE session_id = ? AND vault_name = ?
                """,
                (new_session_id, session_id, vault_name),
            )
            conn.execute(
                """
                UPDATE chat_sessions
                SET session_id = ?
                WHERE rowid = ?
                """,
                (new_session_id, rowid),
            )


def _deduplicated_session_id(
    conn: sqlite3.Connection, *, session_id: str, vault_name: str, index: int
) -> str:
    vault_part = (
        vault_name.strip().replace(" ", "_").replace("/", "_").replace("\\", "_")
    )
    base = f"{session_id}__{vault_part or 'vault'}"
    candidate = base if index == 1 else f"{base}_{index}"
    suffix = index
    while conn.execute(
        "SELECT 1 FROM chat_sessions WHERE session_id = ? LIMIT 1",
        (candidate,),
    ).fetchone():
        suffix += 1
        candidate = f"{base}_{suffix}"
    return candidate
