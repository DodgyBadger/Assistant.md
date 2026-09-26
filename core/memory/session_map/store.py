"""Transactional persistence for append-only live session-map revisions."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from pydantic import JsonValue, TypeAdapter

from core.chat.schema import DB_NAME, ensure_chat_sessions_schema
from core.database import connect_sqlite_from_system_db
from core.tools.utils import estimate_token_count

from .models import (
    PatchOperation,
    SessionMap,
    SessionMapConflictError,
    patch_source_refs,
    session_map_entries,
)

_PATCHES_ADAPTER = TypeAdapter(tuple[PatchOperation, ...])
_JSON_OBJECT_ADAPTER = TypeAdapter(dict[str, JsonValue])


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
    decision_checked_through_sequence_index: int
    decision_checked_pending_turn_count: int
    last_decision: dict[str, JsonValue] | None
    last_authoring: dict[str, JsonValue] | None
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
        existing = connection.execute(
            """
            SELECT observed_through_sequence_index
            FROM chat_session_map_maintenance
            WHERE session_id = ? AND vault_name = ?
            """,
            (session_id, vault_name),
        ).fetchone()
        if existing is not None and through_sequence_index <= int(existing[0]):
            return
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

    def record_completed_turn(
        self,
        connection: sqlite3.Connection,
        *,
        session_id: str,
        vault_name: str,
    ) -> int:
        """Account for one completed turn and every newly observed raw message."""
        row = connection.execute(
            """
            SELECT observed_through_sequence_index
            FROM chat_session_map_maintenance
            WHERE session_id = ? AND vault_name = ?
            """,
            (session_id, vault_name),
        ).fetchone()
        after_sequence_index = int(row[0]) if row is not None else -1
        message_rows = connection.execute(
            """
            SELECT sequence_index, message_json
            FROM chat_messages
            WHERE session_id = ? AND vault_name = ? AND sequence_index > ?
            ORDER BY sequence_index ASC
            """,
            (session_id, vault_name, after_sequence_index),
        ).fetchall()
        if not message_rows:
            raise ValueError("completed turn has no newly persisted canonical messages")
        through_sequence_index = int(message_rows[-1][0])
        token_count = sum(
            estimate_token_count(str(message_row[1] or ""))
            for message_row in message_rows
        )
        self.record_pending(
            connection,
            session_id=session_id,
            vault_name=vault_name,
            through_sequence_index=through_sequence_index,
            token_count=token_count,
        )
        return through_sequence_index

    def freeze_attempt(
        self,
        session_id: str,
        vault_name: str,
        *,
        task_id: str | None = None,
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
            assert frozen.frozen_from_sequence_index is not None
            assert frozen.frozen_through_sequence_index is not None
            assert frozen.frozen_predecessor_revision is not None
            assert frozen.frozen_source_content_revision is not None
            assert frozen.frozen_pending_turn_count is not None
            assert frozen.frozen_pending_token_count is not None
            conn.execute(
                """
                INSERT INTO chat_session_map_attempts (
                    session_id, vault_name, attempt_number, task_id, status,
                    from_sequence_index, through_sequence_index,
                    predecessor_revision, source_content_revision,
                    pending_turn_count, pending_token_count
                ) VALUES (?, ?, ?, ?, 'processing', ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    vault_name,
                    frozen.attempt_count,
                    task_id,
                    frozen.frozen_from_sequence_index,
                    frozen.frozen_through_sequence_index,
                    frozen.frozen_predecessor_revision,
                    frozen.frozen_source_content_revision,
                    frozen.frozen_pending_turn_count,
                    frozen.frozen_pending_token_count,
                ),
            )
            return frozen

    def skip_attempt(
        self,
        session_id: str,
        vault_name: str,
        *,
        decision: dict[str, JsonValue],
    ) -> None:
        """Record a confident stable judgment without consuming pending evidence."""
        decision_json = _dump_json_object(decision)
        with self._transaction() as conn:
            state = self._get_maintenance_state(conn, session_id, vault_name)
            if (
                state is None
                or state.status != "processing"
                or state.frozen_through_sequence_index is None
            ):
                raise SessionMapConflictError("no frozen session-map attempt")
            conn.execute(
                """
                UPDATE chat_session_map_maintenance
                SET status = 'pending',
                    decision_checked_through_sequence_index = MAX(
                        decision_checked_through_sequence_index,
                        frozen_through_sequence_index
                    ),
                    decision_checked_pending_turn_count =
                        frozen_pending_turn_count,
                    last_decision_json = ?,
                    frozen_from_sequence_index = NULL,
                    frozen_through_sequence_index = NULL,
                    frozen_predecessor_revision = NULL,
                    frozen_source_content_revision = NULL,
                    frozen_pending_turn_count = NULL,
                    frozen_pending_token_count = NULL,
                    last_error_json = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE session_id = ? AND vault_name = ? AND status = 'processing'
                """,
                (decision_json, session_id, vault_name),
            )
            self._complete_attempt_row(
                conn,
                state=state,
                status="skipped",
                decision_json=decision_json,
            )

    def commit_revision(
        self,
        *,
        session_id: str,
        vault_name: str,
        expected_revision: int,
        session_map: SessionMap,
        operations: tuple[PatchOperation, ...],
        decision: dict[str, JsonValue] | None = None,
        authoring: dict[str, JsonValue] | None = None,
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
            decision_json = _dump_optional_json_object(decision)
            authoring_json = _dump_optional_json_object(authoring)
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
                    decision_checked_through_sequence_index = MAX(
                        decision_checked_through_sequence_index,
                        ?
                    ),
                    decision_checked_pending_turn_count = 0,
                    last_decision_json = ?,
                    last_authoring_json = ?,
                    last_error_json = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE session_id = ? AND vault_name = ?
                """,
                (
                    session_map.updated_through_sequence_index,
                    decision_json,
                    authoring_json,
                    session_id,
                    vault_name,
                ),
            )
            self._complete_attempt_row(
                conn,
                state=state,
                status="committed",
                decision_json=decision_json,
                authoring_json=authoring_json,
            )

    def fail_attempt(
        self,
        session_id: str,
        vault_name: str,
        *,
        error_type: str,
        retryable: bool,
        decision: dict[str, JsonValue] | None = None,
        authoring: dict[str, JsonValue] | None = None,
    ) -> None:
        """Return a frozen attempt to pending state with sanitized diagnostics."""
        error_json = json.dumps(
            {"error_type": error_type, "retryable": retryable}, sort_keys=True
        )
        decision_json = _dump_optional_json_object(decision)
        authoring_json = _dump_optional_json_object(authoring)
        with self._transaction() as conn:
            state = self._get_maintenance_state(conn, session_id, vault_name)
            if state is None or state.status != "processing":
                raise SessionMapConflictError("no frozen session-map attempt")
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
                    last_decision_json = COALESCE(?, last_decision_json),
                    last_authoring_json = COALESCE(?, last_authoring_json),
                    last_error_json = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE session_id = ? AND vault_name = ? AND status = 'processing'
                """,
                (
                    decision_json,
                    authoring_json,
                    error_json,
                    session_id,
                    vault_name,
                ),
            )
            if cursor.rowcount != 1:
                raise SessionMapConflictError("no frozen session-map attempt")
            self._complete_attempt_row(
                conn,
                state=state,
                status="failed",
                decision_json=decision_json,
                authoring_json=authoring_json,
                error_json=error_json,
            )

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
        error_json = json.dumps(
            {"error_type": "process_restart", "retryable": True}, sort_keys=True
        )
        with self._transaction() as conn:
            conn.execute(
                """
                UPDATE chat_session_map_attempts
                SET status = 'failed', error_json = ?,
                    completed_at = CURRENT_TIMESTAMP
                WHERE status = 'processing'
                """,
                (error_json,),
            )
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
                WHERE status = 'processing'
                """,
                (error_json,),
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
                   frozen_pending_token_count, attempt_count,
                   decision_checked_through_sequence_index,
                   decision_checked_pending_turn_count, last_decision_json,
                   last_authoring_json, last_error_json
            FROM chat_session_map_maintenance
            WHERE session_id = ? AND vault_name = ?
            """,
            (session_id, vault_name),
        ).fetchone()
        if row is None:
            return None
        decision = _JSON_OBJECT_ADAPTER.validate_json(str(row[16])) if row[16] else None
        authoring = (
            _JSON_OBJECT_ADAPTER.validate_json(str(row[17])) if row[17] else None
        )
        error = _JSON_OBJECT_ADAPTER.validate_json(str(row[18])) if row[18] else None
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
            decision_checked_through_sequence_index=int(row[14]),
            decision_checked_pending_turn_count=int(row[15]),
            last_decision=decision,
            last_authoring=authoring,
            last_error=error,
        )

    @staticmethod
    def _complete_attempt_row(
        conn: sqlite3.Connection,
        *,
        state: SessionMapMaintenanceState,
        status: str,
        decision_json: str | None = None,
        authoring_json: str | None = None,
        error_json: str | None = None,
    ) -> None:
        cursor = conn.execute(
            """
            UPDATE chat_session_map_attempts
            SET status = ?, decision_json = ?, authoring_json = ?,
                error_json = ?, completed_at = CURRENT_TIMESTAMP
            WHERE session_id = ? AND vault_name = ? AND attempt_number = ?
              AND status = 'processing'
            """,
            (
                status,
                decision_json,
                authoring_json,
                error_json,
                state.session_id,
                state.vault_name,
                state.attempt_count,
            ),
        )
        if cursor.rowcount != 1:
            raise SessionMapConflictError("frozen session-map attempt audit is missing")

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


def _dump_json_object(value: dict[str, JsonValue]) -> str:
    return _JSON_OBJECT_ADAPTER.dump_json(value).decode("utf-8")


def _dump_optional_json_object(value: dict[str, JsonValue] | None) -> str | None:
    return None if value is None else _dump_json_object(value)


def _canonical_source_role(stored_role: str, message_json: str) -> str:
    payload = json.loads(message_json)
    parts = payload.get("parts") if isinstance(payload, dict) else None
    if isinstance(parts, list) and any(
        isinstance(part, dict)
        and part.get("part_kind") in {"tool-return", "builtin-tool-return"}
        for part in parts
    ):
        return "tool"
    if (
        stored_role == "system"
        and isinstance(parts, list)
        and all(
            isinstance(part, dict) and part.get("part_kind") == "system-prompt"
            for part in parts
        )
    ):
        return "user"
    if stored_role not in {"user", "assistant"}:
        raise ValueError(f"unsupported canonical source role '{stored_role}'")
    return stored_role
