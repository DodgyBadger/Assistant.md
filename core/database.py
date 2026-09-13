"""Centralized system database definitions and helpers."""

import os
import sqlite3
from dataclasses import dataclass

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.schema import Table

from core.runtime.paths import get_system_root
from core.runtime.state import get_runtime_context, has_runtime_context


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    pass


@dataclass(frozen=True)
class SystemDatabaseDefinition:
    """Declared ownership metadata for one system DB file."""

    name: str
    owner: str
    description: str


SYSTEM_DATABASES: dict[str, SystemDatabaseDefinition] = {
    "chat_sessions": SystemDatabaseDefinition(
        name="chat_sessions",
        owner="core.chat.chat_store",
        description="Durable structured chat sessions and provider-native messages.",
    ),
    "cache": SystemDatabaseDefinition(
        name="cache",
        owner="core.context.store",
        description="Context template cache, sessions, and summaries.",
    ),
    "file_state": SystemDatabaseDefinition(
        name="file_state",
        owner="core.utils.file_state",
        description="Processed file tracking for pending workflow inputs.",
    ),
    "vault_state": SystemDatabaseDefinition(
        name="vault_state",
        owner="core.vault_state",
        description="Rebuildable vault manifest, change feed, and attributed activity ledger.",
    ),
    "session_summaries": SystemDatabaseDefinition(
        name="session_summaries",
        owner="core.memory",
        description="Session summary records, fields, and artifacts.",
    ),
    "goal_ops": SystemDatabaseDefinition(
        name="goal_ops",
        owner="core.goals",
        description="Durable goal_ops goals, steps, events, and checkpoints.",
    ),
    "ingestion_jobs": SystemDatabaseDefinition(
        name="ingestion_jobs",
        owner="core.ingestion.jobs",
        description="Persistent ingestion job queue and outputs.",
    ),
    "scheduler_jobs": SystemDatabaseDefinition(
        name="scheduler_jobs",
        owner="core.scheduling.database",
        description="APScheduler persistent job store.",
    ),
    "workflow_runs": SystemDatabaseDefinition(
        name="workflow_runs",
        owner="core.workflow_runs",
        description="Durable workflow attempts, outcomes, and failure history.",
    ),
    "access": SystemDatabaseDefinition(
        name="access",
        owner="core.access_store",
        description=(
            "Principal-owned encrypted credentials and MCP/native connection state."
        ),
    ),
}


def get_system_database_definition(db_name: str) -> SystemDatabaseDefinition:
    """Return declared metadata for a known system DB."""
    definition = SYSTEM_DATABASES.get(db_name)
    if definition is None:
        available = ", ".join(sorted(SYSTEM_DATABASES))
        raise ValueError(
            f"Unknown system database '{db_name}'. Known databases: {available}"
        )
    return definition


def get_system_database_path(db_name: str, system_root: str | None = None) -> str:
    """Get the full path for a system database file.

    Args:
        db_name: Name of the database file (without .db extension)
        system_root: Optional override for system data directory

    Returns:
        Full path to the database file in the system directory
    """
    get_system_database_definition(db_name)
    if system_root is None:
        system_root = str(get_system_root())

    os.makedirs(system_root, exist_ok=True)
    return os.path.join(system_root, f"{db_name}.db")


def create_engine_from_system_db(
    db_name: str,
    system_root: str | None = None,
) -> Engine:
    """Create SQLAlchemy engine for a system database with runtime context support.

    Automatically uses the system_root from runtime context when available,
    enabling proper isolation for validation scenarios. Falls back to environment
    variable or default constant when runtime context is not available.

    Args:
        db_name: Name of the database file (without .db extension)
        system_root: Optional explicit system directory override

    Returns:
        SQLAlchemy engine
    """
    get_system_database_definition(db_name)
    # Check if runtime context is available (validation or production)
    if system_root is not None:
        database_path = get_system_database_path(db_name, system_root)
    elif has_runtime_context():
        runtime = get_runtime_context()
        system_root = str(runtime.config.system_root)
        database_path = get_system_database_path(db_name, system_root)
    else:
        # Fallback for code running outside runtime context
        database_path = get_system_database_path(db_name)

    database_url = f"sqlite:///{database_path}"
    return create_engine(database_url)


def connect_sqlite_from_system_db(
    db_name: str, system_root: str | None = None
) -> sqlite3.Connection:
    """Open a raw sqlite3 connection for a declared system DB.

    Mirrors `create_engine_from_system_db(...)` by honoring the active runtime
    system root when no explicit override is provided.
    """
    if system_root is None and has_runtime_context():
        runtime = get_runtime_context()
        system_root = str(runtime.config.system_root)
    database_path = get_system_database_path(db_name, system_root)
    return sqlite3.connect(database_path)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create SQLAlchemy session factory for an engine.

    Args:
        engine: SQLAlchemy engine instance

    Returns:
        SQLAlchemy sessionmaker class
    """
    return sessionmaker(bind=engine)


def create_tables(engine: Engine, *tables: Table) -> None:
    """Create only the explicitly declared tables for a system DB."""
    if not tables:
        raise ValueError("create_tables requires at least one table")
    Base.metadata.create_all(engine, tables=list(tables))
