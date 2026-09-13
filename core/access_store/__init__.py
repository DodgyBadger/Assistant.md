"""Shared SQLite boundary for principal-owned access state."""

from .schema import (
    ACCESS_MIGRATIONS,
    DB_NAME,
    MIGRATION_NAMESPACE,
    connect_access,
    ensure_access_schema,
    write_transaction,
)

__all__ = [
    "ACCESS_MIGRATIONS",
    "DB_NAME",
    "MIGRATION_NAMESPACE",
    "connect_access",
    "ensure_access_schema",
    "write_transaction",
]
