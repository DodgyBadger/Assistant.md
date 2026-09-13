"""SQLite schema for principal-owned encrypted secrets."""

from __future__ import annotations

import sqlite3

from core.access_store.schema import DB_NAME as ACCESS_DB_NAME
from core.access_store.schema import connect_access

MIGRATION_NAMESPACE = "access"
DB_NAME = ACCESS_DB_NAME


def ensure_secrets_schema(
    system_root: str | None = None, *, apply_migrations: bool = False
) -> None:
    """Create the current encrypted-secrets schema."""
    from core.access_store.schema import ensure_access_schema

    ensure_access_schema(system_root, apply_migrations=apply_migrations)


def connect_secrets(system_root: str | None = None) -> sqlite3.Connection:
    """Open the declared secrets database with row access by column name."""
    return connect_access(system_root)


def create_secrets_schema(conn: sqlite3.Connection) -> None:
    """Create secret tables on a caller-owned connection."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS encrypted_secrets (
        owner_principal_id TEXT NOT NULL, namespace TEXT NOT NULL, name TEXT NOT NULL,
        envelope_version INTEGER NOT NULL, key_version INTEGER NOT NULL,
        nonce BLOB NOT NULL, ciphertext BLOB NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (owner_principal_id, namespace, name),
        CHECK(length(owner_principal_id)>0), CHECK(length(namespace)>0),
        CHECK(length(name)>0), CHECK(envelope_version>0), CHECK(key_version>0),
        CHECK(length(nonce)=12))"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_encrypted_secrets_owner_namespace
        ON encrypted_secrets(owner_principal_id, namespace, name)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS secrets_bootstrap_migrations (
        migration_name TEXT PRIMARY KEY, phase TEXT NOT NULL,
        source_fingerprint TEXT, imported_count INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK(phase IN ('imported','complete')), CHECK(imported_count>=0))"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS secrets_bootstrap_import_items (
        migration_name TEXT NOT NULL, owner_principal_id TEXT NOT NULL,
        namespace TEXT NOT NULL, name TEXT NOT NULL,
        PRIMARY KEY(migration_name,owner_principal_id,namespace,name),
        FOREIGN KEY(migration_name) REFERENCES secrets_bootstrap_migrations(migration_name)
        ON DELETE CASCADE)"""
    )
