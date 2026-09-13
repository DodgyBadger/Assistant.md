"""SQLite schema for principal-owned MCP connection definitions."""

from __future__ import annotations

import sqlite3

from core.access_store.schema import DB_NAME as ACCESS_DB_NAME
from core.access_store.schema import connect_access

MIGRATION_NAMESPACE = "access"
DB_NAME = ACCESS_DB_NAME


def ensure_mcp_schema(
    system_root: str | None = None, *, apply_migrations: bool = False
) -> None:
    """Create the current MCP connection schema."""
    from core.access_store.schema import ensure_access_schema

    ensure_access_schema(system_root, apply_migrations=apply_migrations)


def connect_mcp(system_root: str | None = None) -> sqlite3.Connection:
    """Open the MCP database with named row access."""
    return connect_access(system_root)


def create_mcp_schema(conn: sqlite3.Connection) -> None:
    """Create current MCP tables on a caller-owned connection."""
    _create_connection_tables(conn)


def _create_connection_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mcp_connection_slugs (
            connection_id TEXT PRIMARY KEY,
            owner_principal_id TEXT NOT NULL,
            slug TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(owner_principal_id, slug),
            CHECK (length(connection_id) > 0),
            CHECK (length(owner_principal_id) > 0),
            CHECK (length(slug) > 0)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mcp_connections (
            connection_id TEXT PRIMARY KEY,
            owner_principal_id TEXT NOT NULL,
            slug TEXT NOT NULL,
            display_name TEXT NOT NULL,
            url TEXT,
            transport TEXT NOT NULL,
            auth_mode TEXT NOT NULL,
            header_name TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            allow_private_http INTEGER NOT NULL DEFAULT 0,
            allowed_tools_json TEXT,
            oauth_client_id TEXT,
            oauth_scopes_json TEXT,
            stdio_executable TEXT,
            stdio_arguments_json TEXT,
            stdio_working_directory TEXT,
            stdio_environment_json TEXT,
            stdio_roots_json TEXT,
            config_version INTEGER NOT NULL DEFAULT 1,
            oauth_fence_token TEXT NOT NULL
                DEFAULT (lower(hex(randomblob(16)))),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(owner_principal_id, slug),
            CHECK (length(connection_id) > 0),
            CHECK (length(owner_principal_id) > 0),
            CHECK (length(slug) > 0),
            CHECK (length(display_name) > 0),
            CHECK (url IS NULL OR length(url) > 0),
            CHECK (transport IN ('streamable_http', 'sse', 'advanced_shell_stdio')),
            CHECK (auth_mode IN ('none', 'bearer', 'header', 'oauth')),
            CHECK (enabled IN (0, 1)),
            CHECK (allow_private_http IN (0, 1)),
            CHECK (config_version > 0),
            CHECK (length(oauth_fence_token) = 32),
            CHECK (
                (transport IN ('streamable_http', 'sse')
                 AND url IS NOT NULL AND stdio_executable IS NULL)
                OR
                (transport = 'advanced_shell_stdio' AND url IS NULL
                 AND auth_mode = 'none' AND header_name IS NULL
                 AND allow_private_http = 0 AND oauth_client_id IS NULL
                 AND oauth_scopes_json IS NULL
                 AND stdio_executable IS NOT NULL
                 AND stdio_arguments_json IS NOT NULL
                 AND stdio_working_directory IS NOT NULL
                 AND stdio_environment_json IS NOT NULL
                 AND stdio_roots_json IS NOT NULL)
            )
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_mcp_connections_owner_enabled
        ON mcp_connections(owner_principal_id, enabled, display_name)
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO mcp_connection_slugs (
            connection_id, owner_principal_id, slug, created_at
        )
        SELECT connection_id, owner_principal_id, slug, created_at
        FROM mcp_connections
        """
    )
