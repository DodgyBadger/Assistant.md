"""SQLite schema for principal-owned built-in connections."""

from __future__ import annotations

import sqlite3

from core.access_store.schema import DB_NAME as ACCESS_DB_NAME
from core.access_store.schema import connect_access

MIGRATION_NAMESPACE = "access"
DB_NAME = ACCESS_DB_NAME


def ensure_connections_schema(
    system_root: str | None = None, *, apply_migrations: bool = False
) -> None:
    """Create the current built-in connections schema."""
    from core.access_store.schema import ensure_access_schema

    ensure_access_schema(system_root, apply_migrations=apply_migrations)


def connect_connections(system_root: str | None = None) -> sqlite3.Connection:
    """Open the built-in connections database with named row access."""
    return connect_access(system_root)


def create_connections_schema(conn: sqlite3.Connection) -> None:
    """Create current native-connection tables on a caller-owned connection."""
    _create_current_google_connection_table(conn)


def _create_current_google_connection_table(conn: sqlite3.Connection) -> None:
    """Create the latest table directly for a fresh database."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS google_connections (
            owner_principal_id TEXT NOT NULL,
            connection_id TEXT NOT NULL,
            slug TEXT NOT NULL,
            display_name TEXT NOT NULL,
            client_id TEXT NOT NULL,
            is_default INTEGER NOT NULL DEFAULT 0,
            gmail_search_default_results INTEGER NOT NULL DEFAULT 20,
            gmail_search_max_results INTEGER NOT NULL DEFAULT 100,
            gmail_message_max_characters INTEGER NOT NULL DEFAULT 50000,
            gmail_thread_max_messages INTEGER NOT NULL DEFAULT 25,
            gmail_attachment_download_enabled INTEGER NOT NULL DEFAULT 0 CHECK (gmail_attachment_download_enabled IN (0, 1)),
            gmail_attachment_max_mb INTEGER NOT NULL DEFAULT 25 CHECK (gmail_attachment_max_mb BETWEEN 1 AND 100),
            gmail_draft_creation_enabled INTEGER NOT NULL DEFAULT 0 CHECK (gmail_draft_creation_enabled IN (0, 1)),
            gmail_draft_max_characters INTEGER NOT NULL DEFAULT 50000 CHECK (gmail_draft_max_characters BETWEEN 1 AND 250000),
            config_version INTEGER NOT NULL DEFAULT 1,
            oauth_generation INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (owner_principal_id, connection_id),
            UNIQUE (owner_principal_id, slug),
            CHECK (length(owner_principal_id) > 0),
            CHECK (length(connection_id) > 0),
            CHECK (length(slug) > 0),
            CHECK (length(display_name) > 0),
            CHECK (length(client_id) > 0),
            CHECK (is_default IN (0, 1)),
            CHECK (gmail_search_default_results BETWEEN 1 AND 500),
            CHECK (gmail_search_max_results BETWEEN 1 AND 500),
            CHECK (gmail_search_default_results <= gmail_search_max_results),
            CHECK (gmail_message_max_characters BETWEEN 1 AND 250000),
            CHECK (gmail_thread_max_messages BETWEEN 1 AND 100),
            CHECK (config_version > 0),
            CHECK (oauth_generation > 0)
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS google_connections_owner_name_unique
        ON google_connections(owner_principal_id, lower(display_name))
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS google_connections_one_default
        ON google_connections(owner_principal_id) WHERE is_default = 1
        """
    )

    conn.execute(
        """CREATE TABLE IF NOT EXISTS google_connection_slugs (
            connection_id TEXT PRIMARY KEY,
            owner_principal_id TEXT NOT NULL,
            slug TEXT NOT NULL,
            UNIQUE (owner_principal_id, slug)
        )"""
    )
