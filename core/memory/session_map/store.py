"""Transactional persistence for append-only live session-map revisions."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from pydantic import JsonValue, TypeAdapter
from pydantic_ai.messages import ModelMessage, NativeToolReturnPart, ToolReturnPart

from core.chat.schema import DB_NAME, ensure_chat_sessions_schema
from core.database import connect_sqlite_from_system_db

from .models import (
    PatchOperation,
    SessionMap,
    SessionMapConflictError,
    patch_source_refs,
    session_map_entries,
)

_PATCHES_ADAPTER = TypeAdapter(tuple[PatchOperation, ...])
_MESSAGE_ADAPTER: TypeAdapter[ModelMessage] = TypeAdapter(ModelMessage)
_ERROR_ADAPTER = TypeAdapter(dict[str, JsonValue])


@dataclass(frozen=True)
class SessionMapMaintenanceState:
    """Durable scheduling facts recoverable independently of process tasks."""

    session_id: str
    vault_name: str
    observed_through_sequence_index: int
    observed_source_content_revision: int
    pending_turn_count: int
    pending_token_count: int
    status: str
    frozen_from_sequence_index: int | None
    frozen_through_sequence_index: int | None
    frozen_predecessor_revision: int | None
    frozen_source_content_revision: int | None
    frozen_pending_turn_count: int | None
    frozen_pending_token_count: int | None
    attempt_count: int
    last_error: dict[str, JsonValue] | None


class SessionMapStore:
    """Own session-map revisions inside the canonical chat database."""

    def __init__(self, system_root: str | None = None):
        self.system_root = system_root
        ensure_chat_sessions_schema(system_root)

    def record_pending(
        self,
        connection: sqlite3.Connection,
        *,
        session_id: str,
        vault_name: str,
        through_sequence_index: int,
        token_count: int,
    ) -> None:
        """Advance pending state inside the caller's successful-turn transaction."""
        if through_sequence_index < 0:
            raise ValueError("pending high-water mark must be non-negative")
        if token_count < 0:
            raise ValueError("pending token count must be non-negative")
        row = connection.execute(
            """
            SELECT COALESCE(MAX(sequence_index), -1)
            FROM chat_messages
            WHERE session_id = ? AND vault_name = ?
            """,
            (session_id, vault_name),
        ).fetchone()
        canonical_high_water = int(row[0] if row else -1)
        if through_sequence_index != canonical_high_water:
            raise ValueError(
                "pending high-water mark must equal the canonical message high-water mark"
            )
        connection.execute(
            """
            INSERT INTO chat_session_map_maintenance (
                session_id, vault_name, observed_through_sequence_index,
                observed_source_content_revision, pending_turn_count,
                pending_token_count, status
            ) VALUES (?, ?, ?, 1, 1, ?, 'pending')
            ON CONFLICT(session_id, vault_name) DO UPDATE SET
                observed_through_sequence_index = MAX(
                    observed_through_sequence_index,
                    excluded.observed_through_sequence_index
                ),
                observed_source_content_revision =
                    observed_source_content_revision + 1,
                pending_turn_count = pending_turn_count + 1,
                pending_token_count = pending_token_count + excluded.pending_token_count,
                status = CASE
                    WHEN status = 'processing' THEN status
                    ELSE 'pending'
                END,
                updated_at = CURRENT_TIMESTAMP
            """,
            (session_id, vault_name, through_sequence_index, token_count),
        )

    def freeze_attempt(
        self, session_id: str, vault_name: str
    ) -> SessionMapMaintenanceState:
        """Freeze one canonical source range for deterministic reconciliation."""
        with self._transaction() as conn:
            state = self._get_maintenance_state(conn, session_id, vault_name)
            if state is None or state.pending_turn_count == 0:
                raise ValueError("no pending session-map work")
            if state.status == "processing":
                raise SessionMapConflictError(
                    "session-map attempt is already processing"
                )
            predecessor = self._latest_revision_number(conn, session_id, vault_name)
            latest_map = self._latest_revision(conn, session_id, vault_name)
            covered = (
                latest_map.updated_through_sequence_index
                if latest_map is not None
                else -1
            )
            conn.execute(
                """
                UPDATE chat_session_map_maintenance
                SET status = 'processing',
                    frozen_from_sequence_index = ?,
                    frozen_through_sequence_index = observed_through_sequence_index,
                    frozen_predecessor_revision = ?,
                    frozen_source_content_revision = observed_source_content_revision,
                    frozen_pending_turn_count = pending_turn_count,
                    frozen_pending_token_count = pending_token_count,
                    attempt_count = attempt_count + 1,
                    last_error_json = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE session_id = ? AND vault_name = ?
                """,
                (covered + 1, predecessor, session_id, vault_name),
            )
            frozen = self._get_maintenance_state(conn, session_id, vault_name)
            if frozen is None:  # pragma: no cover - transaction consistency guard
                raise RuntimeError("failed to freeze session-map attempt")
            return frozen

    def commit_revision(
        self,
        *,
        session_id: str,
        vault_name: str,
        expected_revision: int,
        session_map: SessionMap,
        operations: tuple[PatchOperation, ...],
    ) -> None:
        """Append a map revision only when its frozen predecessor still matches."""
        with self._transaction() as conn:
            found_revision = self._latest_revision_number(conn, session_id, vault_name)
            if found_revision != expected_revision:
                raise SessionMapConflictError(
                    f"expected revision {expected_revision}, found {found_revision}"
                )
            if session_map.session_id != session_id:
                raise ValueError("map session ID does not match storage key")
            if session_map.revision != expected_revision + 1:
                raise ValueError("map revision must advance its predecessor by one")
            if not operations:
                raise ValueError("committed revision requires a patch audit")
            state = self._get_maintenance_state(conn, session_id, vault_name)
            if state is None or state.status != "processing":
                raise SessionMapConflictError("no frozen session-map attempt")
            if state.frozen_predecessor_revision != expected_revision:
                raise SessionMapConflictError("frozen predecessor no longer matches")
            if (
                state.frozen_through_sequence_index
                != session_map.updated_through_sequence_index
                or state.frozen_source_content_revision
                != session_map.observed_source_content_revision
            ):
                raise SessionMapConflictError(
                    "map does not cover the frozen source view"
                )
            self._validate_canonical_sources(
                conn,
                session_id=session_id,
                vault_name=vault_name,
                session_map=session_map,
                operations=operations,
            )
            conn.execute(
                """
                INSERT INTO chat_session_map_revisions (
                    session_id, vault_name, revision, predecessor_revision,
                    updated_through_sequence_index,
                    observed_source_content_revision, map_json, operations_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    vault_name,
                    session_map.revision,
                    expected_revision,
                    session_map.updated_through_sequence_index,
                    session_map.observed_source_content_revision,
                    session_map.model_dump_json(),
                    _PATCHES_ADAPTER.dump_json(operations).decode("utf-8"),
                    session_map.updated_at.isoformat(),
                ),
            )
            conn.execute(
                """
                UPDATE chat_session_map_maintenance
                SET pending_turn_count = MAX(
                        0, pending_turn_count - frozen_pending_turn_count
                    ),
                    pending_token_count = MAX(
                        0, pending_token_count - frozen_pending_token_count
                    ),
                    status = CASE
                        WHEN pending_turn_count > frozen_pending_turn_count
                        THEN 'pending' ELSE 'idle'
                    END,
                    frozen_from_sequence_index = NULL,
                    frozen_through_sequence_index = NULL,
                    frozen_predecessor_revision = NULL,
                    frozen_source_content_revision = NULL,
                    frozen_pending_turn_count = NULL,
                    frozen_pending_token_count = NULL,
                    last_error_json = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE session_id = ? AND vault_name = ?
                """,
                (session_id, vault_name),
            )

    def fail_attempt(
        self,
        session_id: str,
        vault_name: str,
        *,
        error_type: str,
        retryable: bool,
    ) -> None:
        """Return a frozen attempt to pending state with sanitized diagnostics."""
        error_json = json.dumps(
            {"error_type": error_type, "retryable": retryable}, sort_keys=True
        )
        with self._transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE chat_session_map_maintenance
                SET status = 'pending',
                    frozen_from_sequence_index = NULL,
                    frozen_through_sequence_index = NULL,
                    frozen_predecessor_revision = NULL,
                    frozen_source_content_revision = NULL,
                    frozen_pending_turn_count = NULL,
                    frozen_pending_token_count = NULL,
                    last_error_json = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE session_id = ? AND vault_name = ? AND status = 'processing'
                """,
                (error_json, session_id, vault_name),
            )
            if cursor.rowcount != 1:
                raise SessionMapConflictError("no frozen session-map attempt")

    def get_latest_revision(
        self, session_id: str, vault_name: str
    ) -> SessionMap | None:
        """Return the highest committed map revision for one session."""
        conn = self._connect()
        try:
            return self._latest_revision(conn, session_id, vault_name)
        finally:
            conn.close()

    def get_revision_operations(
        self,
        session_id: str,
        vault_name: str,
        *,
        revision: int,
    ) -> tuple[PatchOperation, ...] | None:
        """Return the immutable patch audit stored with one revision."""
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT operations_json
                FROM chat_session_map_revisions
                WHERE session_id = ? AND vault_name = ? AND revision = ?
                """,
                (session_id, vault_name, revision),
            ).fetchone()
            if row is None:
                return None
            return _PATCHES_ADAPTER.validate_json(str(row[0]))
        finally:
            conn.close()

    def get_maintenance_state(
        self, session_id: str, vault_name: str
    ) -> SessionMapMaintenanceState | None:
        """Return durable pending and frozen-attempt state."""
        conn = self._connect()
        try:
            return self._get_maintenance_state(conn, session_id, vault_name)
        finally:
            conn.close()

    def recover_interrupted_attempts(self) -> int:
        """Return process-interrupted frozen attempts to pending state."""
        with self._transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE chat_session_map_maintenance
                SET status = 'pending',
                    frozen_from_sequence_index = NULL,
                    frozen_through_sequence_index = NULL,
                    frozen_predecessor_revision = NULL,
                    frozen_source_content_revision = NULL,
                    frozen_pending_turn_count = NULL,
                    frozen_pending_token_count = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'processing'
                """
            )
            return cursor.rowcount

    def _connect(self) -> sqlite3.Connection:
        ensure_chat_sessions_schema(self.system_root)
        return connect_sqlite_from_system_db(DB_NAME, self.system_root)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _latest_revision(
        conn: sqlite3.Connection, session_id: str, vault_name: str
    ) -> SessionMap | None:
        row = conn.execute(
            """
            SELECT map_json
            FROM chat_session_map_revisions
            WHERE session_id = ? AND vault_name = ?
            ORDER BY revision DESC
            LIMIT 1
            """,
            (session_id, vault_name),
        ).fetchone()
        return None if row is None else SessionMap.model_validate_json(str(row[0]))

    @staticmethod
    def _latest_revision_number(
        conn: sqlite3.Connection, session_id: str, vault_name: str
    ) -> int:
        row = conn.execute(
            """
            SELECT COALESCE(MAX(revision), 0)
            FROM chat_session_map_revisions
            WHERE session_id = ? AND vault_name = ?
            """,
            (session_id, vault_name),
        ).fetchone()
        return int(row[0] if row else 0)

    @staticmethod
    def _get_maintenance_state(
        conn: sqlite3.Connection, session_id: str, vault_name: str
    ) -> SessionMapMaintenanceState | None:
        row = conn.execute(
            """
            SELECT session_id, vault_name, observed_through_sequence_index,
                   observed_source_content_revision, pending_turn_count,
                   pending_token_count, status, frozen_from_sequence_index,
                   frozen_through_sequence_index, frozen_predecessor_revision,
                   frozen_source_content_revision, frozen_pending_turn_count,
                   frozen_pending_token_count, attempt_count, last_error_json
            FROM chat_session_map_maintenance
            WHERE session_id = ? AND vault_name = ?
            """,
            (session_id, vault_name),
        ).fetchone()
        if row is None:
            return None
        error = _ERROR_ADAPTER.validate_json(str(row[14])) if row[14] else None
        return SessionMapMaintenanceState(
            session_id=str(row[0]),
            vault_name=str(row[1]),
            observed_through_sequence_index=int(row[2]),
            observed_source_content_revision=int(row[3]),
            pending_turn_count=int(row[4]),
            pending_token_count=int(row[5]),
            status=str(row[6]),
            frozen_from_sequence_index=_optional_int(row[7]),
            frozen_through_sequence_index=_optional_int(row[8]),
            frozen_predecessor_revision=_optional_int(row[9]),
            frozen_source_content_revision=_optional_int(row[10]),
            frozen_pending_turn_count=_optional_int(row[11]),
            frozen_pending_token_count=_optional_int(row[12]),
            attempt_count=int(row[13]),
            last_error=error,
        )

    @staticmethod
    def _validate_canonical_sources(
        conn: sqlite3.Connection,
        *,
        session_id: str,
        vault_name: str,
        session_map: SessionMap,
        operations: tuple[PatchOperation, ...],
    ) -> None:
        refs = {
            (ref.sequence_index, ref.role)
            for entry in session_map_entries(session_map)
            for ref in (*entry.source_refs, *entry.state_source_refs)
        }
        refs.update(
            (ref.sequence_index, ref.role)
            for operation in operations
            for ref in patch_source_refs(operation)
        )
        if not refs:
            return
        rows = conn.execute(
            """
            SELECT sequence_index, role, message_json
            FROM chat_messages
            WHERE session_id = ? AND vault_name = ?
              AND sequence_index <= ?
            """,
            (
                session_id,
                vault_name,
                session_map.updated_through_sequence_index,
            ),
        ).fetchall()
        canonical_roles = {
            int(row[0]): _canonical_source_role(str(row[1]), str(row[2]))
            for row in rows
        }
        for sequence_index, role in refs:
            canonical_role = canonical_roles.get(sequence_index)
            if canonical_role is None:
                raise ValueError(
                    f"source reference {sequence_index} is not a canonical message"
                )
            if canonical_role != role:
                raise ValueError(
                    f"source reference {sequence_index} role does not match canonical message"
                )


def _optional_int(value: object) -> int | None:
    return None if value is None else int(str(value))


def _canonical_source_role(stored_role: str, message_json: str) -> str:
    message = _MESSAGE_ADAPTER.validate_json(message_json)
    if any(
        isinstance(part, ToolReturnPart | NativeToolReturnPart)
        for part in getattr(message, "parts", ()) or ()
    ):
        return "tool"
    if stored_role not in {"user", "assistant"}:
        raise ValueError(f"unsupported canonical source role '{stored_role}'")
    return stored_role
