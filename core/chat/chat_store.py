"""Durable SQLite-backed chat session store."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, fields, is_dataclass, replace
from typing import Any, Literal, cast

from pydantic import TypeAdapter, ValidationError
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    NativeToolReturnPart,
    RetryPromptPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_core import to_jsonable_python

from core.chat.tool_history import (
    ToolHistoryInvocation,
    ToolHistoryProtocolState,
    model_message_tool_invocations,
)
from core.database import connect_sqlite_from_system_db
from core.identity import normalize_principal_id
from core.logger import UnifiedLogger
from core.settings import get_persist_model_reasoning_parts
from core.utils.messages import extract_role_and_text

from .schema import DB_NAME, ensure_chat_sessions_schema

logger = UnifiedLogger(tag="chat-store")

_MODEL_MESSAGE_ADAPTER: TypeAdapter[ModelMessage] = TypeAdapter(ModelMessage)
_MODEL_MESSAGE_LIST_ADAPTER: TypeAdapter[list[ModelMessage]] = TypeAdapter(
    list[ModelMessage]
)
HistoryMode = Literal["effective", "raw"]
ContextCheckpointKind = Literal["recovery_card", "session_map"]


class ChatHistoryCorruptionError(RuntimeError):
    """A persisted chat record cannot safely become canonical or effective history."""

    def __init__(
        self,
        *,
        session_id: str,
        vault_name: str,
        sequence_index: int | None = None,
        checkpoint_id: str | None = None,
    ) -> None:
        self.session_id = session_id
        self.vault_name = vault_name
        self.sequence_index = sequence_index
        self.checkpoint_id = checkpoint_id
        record = (
            f"canonical message {sequence_index}"
            if sequence_index is not None
            else f"context checkpoint '{checkpoint_id}'"
        )
        super().__init__(
            f"Unable to deserialize {record} for session '{session_id}' "
            f"in vault '{vault_name}'."
        )


def _json_dumps(value: Any) -> str:
    """Serialize diagnostic values without rejecting valid Python data types."""
    return json.dumps(
        to_jsonable_python(value, serialize_unknown=True),
        ensure_ascii=False,
        sort_keys=True,
    )


@dataclass(frozen=True)
class StoredChatMessage:
    """One stored provider-native chat message."""

    sequence_index: int
    fork_sequence_index: int | None
    direction: str
    message_type: str
    role: str
    content_text: str
    created_at: str
    message_json: str
    message: ModelMessage

    @property
    def tool_call_ids(self) -> tuple[str, ...]:
        """Tool call IDs declared by this provider-native message."""
        return _ordered_tool_call_ids_from_message(self.message)

    @property
    def tool_return_ids(self) -> tuple[str, ...]:
        """Tool return IDs declared by this provider-native message."""
        return _ordered_tool_return_ids_from_message(self.message)


@dataclass(frozen=True)
class StoredContextCheckpoint:
    """One append-only effective-history checkpoint."""

    id: int
    checkpoint_id: str
    session_id: str
    vault_name: str
    created_at: str
    source: str
    checkpoint_kind: ContextCheckpointKind
    message_count_before: int
    last_message_sequence_index: int
    summary_message_json: str
    replacement_history_json: str
    replacement_source_sequence_indexes_json: str | None = None
    metadata_json: str | None = None

    @property
    def observed_through_sequence_index(self) -> int:
        """Return the validated canonical cutoff required for safe inheritance."""
        return _checkpoint_observation_boundary(self)


StoredCompactionCheckpoint = StoredContextCheckpoint


@dataclass(frozen=True)
class StoredChatHistoryStructure:
    """Content-free history shape used by list and eligibility projections."""

    message_count: int
    group_count: int
    tool_history_ok: bool


@dataclass(frozen=True)
class StoredChatToolEvent:
    """One stored structured chat tool event."""

    tool_call_id: str
    tool_name: str
    event_type: str
    created_at: str
    args_json: str | None = None
    result_text: str | None = None
    result_metadata_json: str | None = None
    artifact_ref: str | None = None


def tool_call_events_are_unambiguous(events: Sequence[StoredChatToolEvent]) -> bool:
    """Accept one call and at most one terminal event with the same identity."""
    return (
        bool(events)
        and events[0].event_type == "call"
        and (
            len(events) == 1
            or (
                len(events) == 2
                and events[1].tool_call_id == events[0].tool_call_id
                and events[1].tool_name == events[0].tool_name
                and events[1].event_type in {"result", "overflow_cached"}
            )
        )
    )


def _stored_tool_event_from_row(row: Any) -> StoredChatToolEvent:
    """Convert a chat-tool-event query row through one stable mapping."""
    return StoredChatToolEvent(
        tool_call_id=str(row[0]),
        tool_name=str(row[1]),
        event_type=str(row[2]),
        created_at=str(row[3] or ""),
        args_json=None if row[4] is None else str(row[4]),
        result_text=None if row[5] is None else str(row[5]),
        result_metadata_json=None if row[6] is None else str(row[6]),
        artifact_ref=None if row[7] is None else str(row[7]),
    )


@dataclass(frozen=True)
class StoredChatSession:
    """One stored chat session summary."""

    session_id: str
    vault_name: str
    owner_principal_id: str
    created_at: str
    last_activity_at: str
    title: str | None = None
    metadata_json: str | None = None


class ChatStore:
    """Persistent structured chat session store."""

    def __init__(self, system_root: str | None = None):
        self.system_root = system_root
        ensure_chat_sessions_schema(system_root)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Own one atomic mutation spanning related chat-session artifacts."""
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

    def get_history(
        self,
        session_id: str,
        vault_name: str,
        *,
        mode: HistoryMode = "effective",
    ) -> list[ModelMessage] | None:
        """Return provider-native message history for one session."""
        rows = self._fetch_messages(
            session_id=session_id,
            vault_name=vault_name,
            mode=mode,
        )
        if not rows:
            return None
        persist_reasoning = get_persist_model_reasoning_parts()
        return [
            _message_for_model_history(
                row.message,
                persist_reasoning_parts=persist_reasoning,
            )
            for row in rows
        ]

    def get_stored_messages(
        self,
        session_id: str,
        vault_name: str,
        *,
        limit: int | None = None,
        mode: HistoryMode = "effective",
    ) -> list[StoredChatMessage]:
        """Return stored chat messages with persistence metadata."""
        return self._fetch_messages(
            session_id=session_id,
            vault_name=vault_name,
            limit=limit,
            mode=mode,
        )

    def get_stored_messages_range(
        self,
        session_id: str,
        vault_name: str,
        *,
        after_sequence_index: int,
        through_sequence_index: int,
        connection: sqlite3.Connection | None = None,
    ) -> list[StoredChatMessage]:
        """Return one canonical raw-message interval ``(after, through]``."""
        if through_sequence_index <= after_sequence_index:
            return []
        if connection is None:
            conn = self._connect()
            try:
                return self._fetch_raw_messages_from_conn(
                    conn,
                    session_id=session_id,
                    vault_name=vault_name,
                    after_sequence_index=after_sequence_index,
                    through_sequence_index=through_sequence_index,
                )
            finally:
                conn.close()
        return self._fetch_raw_messages_from_conn(
            connection,
            session_id=session_id,
            vault_name=vault_name,
            after_sequence_index=after_sequence_index,
            through_sequence_index=through_sequence_index,
        )

    def get_canonical_display_row_page(
        self,
        session_id: str,
        vault_name: str,
        *,
        through_sequence_index: int,
        limit: int,
        offset: int,
    ) -> tuple[int, list[tuple[int, int, str]]]:
        """Count display rows and return one page of raw sequence boundaries.

        SQLite scans compact message metadata to count collapsed tool runs. Only
        the selected boundaries cross into Python; message JSON is hydrated by
        the caller for that page alone.
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                WITH classified AS (
                    SELECT sequence_index, role, message_type,
                           CASE WHEN (
                               substr(ltrim(coalesce(content_text, '')), 1, 1) = '['
                               AND instr(ltrim(coalesce(content_text, '')), ']') > 0
                           ) OR EXISTS (
                               SELECT 1 FROM json_each(message_json, '$.parts') part
                               WHERE json_extract(part.value, '$.part_kind')
                                   IN ('tool-call', 'tool-return')
                                 AND coalesce(json_extract(part.value, '$.tool_call_id'), '') != ''
                           ) THEN 1 ELSE 0 END AS is_tool,
                           CASE WHEN EXISTS (
                               SELECT 1 FROM json_each(message_json, '$.parts') part
                               WHERE json_extract(part.value, '$.part_kind') = 'text'
                                 AND trim(coalesce(json_extract(part.value, '$.content'), '')) != ''
                           ) OR (
                               NOT EXISTS (
                                   SELECT 1 FROM json_each(message_json, '$.parts') part
                                   WHERE json_extract(part.value, '$.part_kind') = 'tool-call'
                                     AND coalesce(json_extract(part.value, '$.tool_call_id'), '') != ''
                               ) AND trim(coalesce(content_text, '')) != ''
                           ) THEN 1 ELSE 0 END AS has_text
                    FROM chat_messages
                    WHERE session_id = ? AND vault_name = ?
                      AND sequence_index <= ?
                ), pieces AS (
                    SELECT sequence_index, 0 AS piece_order,
                           CASE WHEN is_tool = 0 AND role NOT IN ('user', 'assistant')
                                THEN 'separator' ELSE 'message' END AS kind
                    FROM classified WHERE is_tool = 0
                    UNION ALL
                    SELECT sequence_index, 0, 'message'
                    FROM classified
                    WHERE is_tool = 1 AND role = 'assistant'
                      AND message_type = 'ModelResponse' AND has_text = 1
                    UNION ALL
                    SELECT sequence_index, 1, 'tool'
                    FROM classified WHERE is_tool = 1
                ), adjacent AS (
                    SELECT sequence_index, piece_order, kind,
                           lag(kind) OVER (
                               ORDER BY sequence_index, piece_order
                           ) AS previous_kind
                    FROM pieces
                ), grouped AS (
                    SELECT sequence_index, kind,
                           sum(CASE WHEN kind = 'tool' AND previous_kind = 'tool'
                                    THEN 0 ELSE 1 END) OVER (
                               ORDER BY sequence_index, piece_order
                           ) AS group_id
                    FROM adjacent
                ), display_rows AS (
                    SELECT group_id, min(sequence_index) AS first_sequence_index,
                           max(sequence_index) AS last_sequence_index,
                           min(kind) AS kind
                    FROM grouped GROUP BY group_id HAVING kind != 'separator'
                )
                SELECT totals.total_entries, page.first_sequence_index,
                       page.last_sequence_index, page.kind
                FROM (SELECT count(*) AS total_entries FROM display_rows) totals
                LEFT JOIN (
                    SELECT first_sequence_index, last_sequence_index, kind
                    FROM display_rows ORDER BY group_id LIMIT ? OFFSET ?
                ) page ON 1 = 1
                """,
                (session_id, vault_name, through_sequence_index, limit, offset),
            ).fetchall()
        finally:
            conn.close()
        total_entries = int(rows[0][0]) if rows else 0
        boundaries = [
            (int(first), int(last), str(kind))
            for _, first, last, kind in rows
            if first is not None and last is not None and kind is not None
        ]
        return total_entries, boundaries

    def get_canonical_fork_points_for_sequences(
        self,
        session_id: str,
        vault_name: str,
        candidate_sequence_indexes: Sequence[int],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> set[int]:
        """Validate selected assistant fork points against compact prefix tool metadata."""
        candidates = sorted(set(candidate_sequence_indexes))
        if not candidates:
            return set()
        conn = connection or self._connect()
        owns_connection = connection is None
        try:
            cursor = iter(
                self._raw_tool_protocol_rows_from_conn(
                    conn,
                    session_id=session_id,
                    vault_name=vault_name,
                    through_sequence_index=candidates[-1],
                )
            )
            current = next(cursor, None)
            protocol = ToolHistoryProtocolState()
            fork_points: set[int] = set()
            for candidate in candidates:
                while current is not None and int(current[0]) <= candidate:
                    sequence_index = int(current[0])
                    message_index = int(current[1])
                    calls: list[ToolHistoryInvocation] = []
                    replies: list[ToolHistoryInvocation] = []
                    while current is not None and int(current[0]) == sequence_index:
                        invocation = ToolHistoryInvocation(
                            tool_call_id=str(current[3] or ""),
                            tool_name=None if current[4] is None else str(current[4]),
                            is_retry=current[2] == "retry-prompt",
                        )
                        if current[2] == "tool-call":
                            calls.append(invocation)
                        else:
                            replies.append(invocation)
                        current = next(cursor, None)
                    protocol.record_message(message_index, calls, replies)
                    if protocol.issues:
                        break
                if not protocol.issues and not protocol.pending:
                    fork_points.add(candidate)
            return fork_points
        finally:
            if owns_connection:
                conn.close()

    def get_raw_history_structure(
        self,
        session_id: str,
        vault_name: str,
    ) -> StoredChatHistoryStructure:
        """Inspect grouping and tool protocol without hydrating message content."""
        conn = self._connect()
        try:
            count_row = conn.execute(
                """
                WITH ordered AS (
                    SELECT row_number() OVER (ORDER BY sequence_index) AS position,
                           message_type,
                           message_json
                    FROM chat_messages
                    WHERE session_id = ? AND vault_name = ?
                )
                SELECT count(*),
                       CASE WHEN count(*) = 0 THEN 0 ELSE
                           1 + coalesce(sum(CASE WHEN position > 1
                               AND message_type = 'ModelRequest'
                               AND EXISTS (
                                   SELECT 1
                                   FROM json_each(message_json, '$.parts') AS part
                                   WHERE json_extract(part.value, '$.part_kind')
                                       IN ('user-prompt', 'system-prompt')
                               ) THEN 1 ELSE 0 END), 0)
                       END
                FROM ordered
                """,
                (session_id, vault_name),
            ).fetchone()
            message_count = int(count_row[0]) if count_row else 0
            group_count = int(count_row[1]) if count_row else 0

            rows = self._raw_tool_protocol_rows_from_conn(
                conn,
                session_id=session_id,
                vault_name=vault_name,
            )
        finally:
            conn.close()

        protocol = ToolHistoryProtocolState()
        cursor = 0
        while cursor < len(rows):
            sequence_index = int(rows[cursor][0])
            message_index = int(rows[cursor][1])
            calls: list[ToolHistoryInvocation] = []
            replies: list[ToolHistoryInvocation] = []
            while cursor < len(rows) and int(rows[cursor][0]) == sequence_index:
                part_kind = str(rows[cursor][2] or "")
                invocation = ToolHistoryInvocation(
                    tool_call_id=str(rows[cursor][3] or ""),
                    tool_name=(
                        None if rows[cursor][4] is None else str(rows[cursor][4])
                    ),
                    is_retry=part_kind == "retry-prompt",
                )
                if part_kind == "tool-call":
                    calls.append(invocation)
                else:
                    replies.append(invocation)
                cursor += 1
            protocol.record_message(message_index, calls, replies)

        return StoredChatHistoryStructure(
            message_count=message_count,
            group_count=group_count,
            tool_history_ok=not protocol.issues
            and not protocol.unmatched_call_issues(),
        )

    def _raw_tool_protocol_rows_from_conn(
        self,
        conn: sqlite3.Connection,
        *,
        session_id: str,
        vault_name: str,
        through_sequence_index: int | None = None,
    ) -> list[Any]:
        """Project only ordered tool identities needed by protocol checks."""
        boundary_filter = ""
        params: list[Any] = [session_id, vault_name]
        if through_sequence_index is not None:
            boundary_filter = "AND sequence_index <= ?"
            params.append(through_sequence_index)
        return conn.execute(
            f"""
            WITH numbered AS (
                SELECT sequence_index, message_type, message_json,
                       row_number() OVER (ORDER BY sequence_index) - 1
                           AS message_index
                FROM chat_messages
                WHERE session_id = ? AND vault_name = ?
                  {boundary_filter}
            )
            SELECT message.sequence_index, message.message_index,
                   json_extract(part.value, '$.part_kind'),
                   json_extract(part.value, '$.tool_call_id'),
                   json_extract(part.value, '$.tool_name')
            FROM numbered AS message,
                 json_each(message.message_json, '$.parts') AS part
            WHERE (
                  (message.message_type = 'ModelResponse'
                   AND json_extract(part.value, '$.part_kind') = 'tool-call')
                  OR
                  (message.message_type = 'ModelRequest'
                   AND (
                       json_extract(part.value, '$.part_kind') = 'tool-return'
                       OR (
                           json_extract(part.value, '$.part_kind') = 'retry-prompt'
                           AND json_extract(part.value, '$.tool_name') IS NOT NULL
                       )
                   ))
              )
            ORDER BY message.sequence_index, CAST(part.key AS INTEGER)
            """,
            params,
        ).fetchall()

    def add_messages(
        self,
        session_id: str,
        vault_name: str,
        messages: list[ModelMessage],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[int]:
        """Append provider-native messages and return their canonical sequence indexes."""
        if not messages:
            return []
        if connection is None:
            with self.transaction() as conn:
                return self.add_messages(
                    session_id,
                    vault_name,
                    messages,
                    connection=conn,
                )
            return
        self._upsert_session(connection, session_id=session_id, vault_name=vault_name)
        next_index = self._next_sequence_index(
            connection, session_id=session_id, vault_name=vault_name
        )
        persist_reasoning = get_persist_model_reasoning_parts()
        for offset, message in enumerate(messages):
            message = _message_for_persistence(
                message,
                persist_reasoning_parts=persist_reasoning,
            )
            role, content_text = extract_role_and_text(message)
            direction = (
                "response" if type(message).__name__ == "ModelResponse" else "request"
            )
            connection.execute(
                """
                INSERT INTO chat_messages (
                    session_id,
                    vault_name,
                    sequence_index,
                    direction,
                    message_type,
                    role,
                    content_text,
                    message_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    vault_name,
                    next_index + offset,
                    direction,
                    type(message).__name__,
                    role,
                    content_text,
                    _MODEL_MESSAGE_ADAPTER.dump_json(message).decode("utf-8"),
                ),
            )
        self._touch_session(
            connection,
            session_id=session_id,
            vault_name=vault_name,
            advance_history_revision=True,
        )
        return list(range(next_index, next_index + len(messages)))

    def ensure_session(
        self,
        session_id: str,
        vault_name: str,
        *,
        owner_principal_id: str,
    ) -> StoredChatSession:
        """Create or touch a session bound to one vault, returning its summary."""
        conn = self._connect()
        try:
            self._upsert_session(
                conn,
                session_id=session_id,
                vault_name=vault_name,
                owner_principal_id=owner_principal_id,
            )
            conn.commit()
        finally:
            conn.close()
        session = self.get_session(session_id=session_id, vault_name=vault_name)
        if session is None:  # pragma: no cover - defensive consistency check
            raise RuntimeError(f"Failed to create chat session '{session_id}'.")
        return session

    def replace_session_messages(
        self,
        session_id: str,
        vault_name: str,
        messages: list[ModelMessage],
        *,
        metadata_update: dict[str, Any] | None = None,
    ) -> None:
        """Replace one session's canonical messages in a single transaction."""
        conn = self._connect()
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            self._upsert_session(conn, session_id=session_id, vault_name=vault_name)
            conn.execute(
                """
                DELETE FROM chat_compaction_checkpoints
                WHERE session_id = ? AND vault_name = ?
                """,
                (session_id, vault_name),
            )
            conn.execute(
                """
                DELETE FROM chat_messages
                WHERE session_id = ? AND vault_name = ?
                """,
                (session_id, vault_name),
            )
            persist_reasoning = get_persist_model_reasoning_parts()
            for sequence_index, message in enumerate(messages):
                message = _message_for_persistence(
                    message,
                    persist_reasoning_parts=persist_reasoning,
                )
                role, content_text = extract_role_and_text(message)
                direction = (
                    "response"
                    if type(message).__name__ == "ModelResponse"
                    else "request"
                )
                conn.execute(
                    """
                    INSERT INTO chat_messages (
                        session_id,
                        vault_name,
                        sequence_index,
                        direction,
                        message_type,
                        role,
                        content_text,
                        message_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        vault_name,
                        sequence_index,
                        direction,
                        type(message).__name__,
                        role,
                        content_text,
                        _MODEL_MESSAGE_ADAPTER.dump_json(message).decode("utf-8"),
                    ),
                )
            self._touch_session(
                conn,
                session_id=session_id,
                vault_name=vault_name,
                metadata_update=metadata_update,
                advance_history_revision=True,
            )
            conn.commit()
        finally:
            conn.close()

    def fork_session(
        self,
        *,
        source_session_id: str,
        new_session_id: str,
        vault_name: str,
        through_sequence_index: int,
        title: str | None,
        metadata_update: dict[str, Any] | None = None,
    ) -> int:
        """Create an isolated session from one canonical source-message prefix."""
        with self.transaction() as conn:
            # Reserve the writer before reading any source state. Implicit
            # SQLite transactions begin only at the first write, which would
            # otherwise allow messages and checkpoints from different revisions.
            conn.execute("BEGIN IMMEDIATE")
            source = conn.execute(
                """
                SELECT metadata_json, owner_principal_id
                FROM chat_sessions
                WHERE session_id = ? AND vault_name = ?
                """,
                (source_session_id, vault_name),
            ).fetchone()
            if source is None:
                raise ValueError(f"Chat session not found: {source_session_id}")

            source_metadata: dict[str, Any] = {}
            if source[0]:
                try:
                    parsed_metadata = json.loads(str(source[0]))
                    if isinstance(parsed_metadata, dict):
                        source_metadata = parsed_metadata
                except Exception:
                    source_metadata = {}
            inherited_fork_metadata = source_metadata.get("fork")
            source_metadata = _fork_session_metadata(source_metadata)
            if metadata_update:
                source_metadata.update(metadata_update)
            fork_metadata = source_metadata.get("fork")
            root_session_id = source_session_id
            if isinstance(inherited_fork_metadata, dict):
                root_session_id = str(
                    inherited_fork_metadata.get("root_session_id") or source_session_id
                )

            messages = self._fetch_raw_messages_from_conn(
                conn,
                session_id=source_session_id,
                vault_name=vault_name,
                through_sequence_index=through_sequence_index,
            )
            if not messages:
                raise ValueError(
                    f"No chat messages found through sequence {through_sequence_index}"
                )
            if messages[-1].sequence_index != through_sequence_index:
                raise ValueError(
                    f"Canonical fork point does not exist: {through_sequence_index}"
                )
            if through_sequence_index not in canonical_assistant_fork_points(messages):
                raise ValueError(
                    f"Canonical fork point is not a protocol-complete assistant "
                    f"message: {through_sequence_index}"
                )

            checkpoint_rows = conn.execute(
                """
                SELECT id, checkpoint_id, session_id, vault_name, created_at,
                       source, checkpoint_kind, message_count_before,
                       last_message_sequence_index, summary_message_json,
                       replacement_history_json,
                       replacement_source_sequence_indexes_json, metadata_json
                FROM chat_compaction_checkpoints
                WHERE session_id = ? AND vault_name = ?
                ORDER BY id ASC
                """,
                (source_session_id, vault_name),
            ).fetchall()
            eligible_checkpoints = [
                checkpoint
                for checkpoint in (
                    self._context_checkpoint_from_row(row) for row in checkpoint_rows
                )
                if _checkpoint_observation_boundary(checkpoint)
                <= through_sequence_index
            ]
            for checkpoint in eligible_checkpoints:
                self._checkpoint_replacement_messages(
                    conn,
                    checkpoint,
                    session_id=source_session_id,
                    vault_name=vault_name,
                )
            source_metadata["fork"] = {
                "source_session_id": source_session_id,
                "through_sequence_index": through_sequence_index,
                "root_session_id": root_session_id,
                "child_owned_from_sequence_index": through_sequence_index + 1,
                "inherited_checkpoint_count": len(eligible_checkpoints),
                "created_at": (
                    fork_metadata.get("created_at")
                    if isinstance(fork_metadata, dict)
                    else None
                ),
            }

            conn.execute(
                """
                INSERT INTO chat_sessions (
                    session_id,
                    vault_name,
                    owner_principal_id,
                    title,
                    metadata_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    new_session_id,
                    vault_name,
                    str(source[1]),
                    title or None,
                    json.dumps(source_metadata, ensure_ascii=False, sort_keys=True),
                ),
            )

            copied_tool_call_ids: set[str] = set()
            copied_tool_event_count = 0
            for message in messages:
                conn.execute(
                    """
                    INSERT INTO chat_messages (
                        session_id,
                        vault_name,
                        sequence_index,
                        direction,
                        message_type,
                        role,
                        content_text,
                        message_json,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_session_id,
                        vault_name,
                        message.sequence_index,
                        message.direction,
                        message.message_type,
                        message.role,
                        message.content_text,
                        message.message_json,
                        message.created_at,
                    ),
                )
                copied_tool_call_ids.update(message.tool_call_ids)
                copied_tool_call_ids.update(message.tool_return_ids)

            if copied_tool_call_ids:
                declaration_counts = self._tool_call_declaration_counts_from_conn(
                    conn,
                    source_session_id,
                    vault_name,
                    tool_call_ids=sorted(copied_tool_call_ids),
                )
                event_rows = conn.execute(
                    """
                    SELECT tool_call_id, tool_name, event_type, created_at,
                           args_json, result_text, result_metadata_json, artifact_ref
                    FROM chat_tool_events
                    WHERE session_id = ? AND vault_name = ?
                    ORDER BY id ASC
                    """,
                    (source_session_id, vault_name),
                ).fetchall()
                events = [_stored_tool_event_from_row(row) for row in event_rows]
                events_by_id: dict[str, list[StoredChatToolEvent]] = {}
                for event in events:
                    events_by_id.setdefault(event.tool_call_id, []).append(event)
                safe_tool_call_ids = {
                    tool_call_id
                    for tool_call_id, call_events in events_by_id.items()
                    if tool_call_id in copied_tool_call_ids
                    and declaration_counts.get(tool_call_id) == 1
                    and tool_call_events_are_unambiguous(call_events)
                }
                for event in events:
                    # Events lack an invocation sequence. A reused ID cannot
                    # distinguish inherited details from a later invocation.
                    if event.tool_call_id not in safe_tool_call_ids:
                        continue
                    conn.execute(
                        """
                        INSERT INTO chat_tool_events (
                            session_id,
                            vault_name,
                            tool_call_id,
                            tool_name,
                            event_type,
                            args_json,
                            result_text,
                            result_metadata_json,
                            artifact_ref,
                            created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            new_session_id,
                            vault_name,
                            event.tool_call_id,
                            event.tool_name,
                            event.event_type,
                            event.args_json,
                            event.result_text,
                            event.result_metadata_json,
                            event.artifact_ref,
                            event.created_at,
                        ),
                    )
                    copied_tool_event_count += 1

            copied_checkpoint_ids: dict[str, str] = {}
            for checkpoint in eligible_checkpoints:
                child_checkpoint_id = uuid.uuid4().hex
                copied_checkpoint_ids[checkpoint.checkpoint_id] = child_checkpoint_id
                checkpoint_metadata = _checkpoint_metadata(checkpoint)
                checkpoint_metadata["fork_origin"] = {
                    "source_session_id": source_session_id,
                    "source_checkpoint_id": checkpoint.checkpoint_id,
                }
                conn.execute(
                    """
                    INSERT INTO chat_compaction_checkpoints (
                        checkpoint_id, session_id, vault_name, created_at, source,
                        checkpoint_kind, message_count_before,
                        last_message_sequence_index, summary_message_json,
                        replacement_history_json,
                        replacement_source_sequence_indexes_json, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        child_checkpoint_id,
                        new_session_id,
                        vault_name,
                        checkpoint.created_at,
                        checkpoint.source,
                        checkpoint.checkpoint_kind,
                        checkpoint.message_count_before,
                        checkpoint.last_message_sequence_index,
                        checkpoint.summary_message_json,
                        checkpoint.replacement_history_json,
                        checkpoint.replacement_source_sequence_indexes_json,
                        json.dumps(
                            checkpoint_metadata, ensure_ascii=False, sort_keys=True
                        ),
                    ),
                )

            if eligible_checkpoints:
                latest = eligible_checkpoints[-1]
                latest_child_id = copied_checkpoint_ids[latest.checkpoint_id]
                latest_metadata = _checkpoint_metadata(latest)
                if latest.checkpoint_kind == "recovery_card":
                    source_metadata["last_compaction"] = {
                        **latest_metadata,
                        "compaction_id": latest_child_id,
                    }
                else:
                    source_metadata["last_session_map_checkpoint"] = {
                        "checkpoint_id": latest_child_id,
                        "prompt_contract_version": latest_metadata.get(
                            "prompt_contract_version"
                        ),
                        "consumed_through_sequence_index": (
                            latest.last_message_sequence_index
                        ),
                        "map_observed_through_sequence_index": (
                            _checkpoint_observation_boundary(latest)
                        ),
                        "source_history_revision": latest_metadata.get(
                            "source_history_revision"
                        ),
                    }
            child_fork_metadata = source_metadata.get("fork")
            if isinstance(child_fork_metadata, dict):
                child_fork_metadata["copied_tool_event_count"] = copied_tool_event_count

            conn.execute(
                """
                UPDATE chat_sessions
                SET metadata_json = ?
                WHERE session_id = ? AND vault_name = ?
                """,
                (
                    json.dumps(source_metadata, ensure_ascii=False, sort_keys=True),
                    new_session_id,
                    vault_name,
                ),
            )

            self._touch_session(
                conn,
                session_id=new_session_id,
                vault_name=vault_name,
                advance_history_revision=True,
            )
            return len(messages)

    def set_session_title(
        self, session_id: str, vault_name: str, title: str | None
    ) -> None:
        """Set or clear the user-defined title for a session."""
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE chat_sessions SET title = ? WHERE session_id = ? AND vault_name = ?",
                (title or None, session_id, vault_name),
            )
            conn.commit()
        finally:
            conn.close()

    def delete_sessions(
        self,
        vault_name: str,
        *,
        session_id: str | None = None,
        older_than_days: int | None = None,
    ) -> list[str]:
        """Delete sessions for a vault, returning the list of deleted session_ids.

        - session_id: delete exactly one session by ID
        - older_than_days: delete sessions older than N days
        - neither: delete all sessions for the vault

        CASCADE deletes handle messages and tool_events automatically.
        """
        conn = self._connect()
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            if session_id is not None:
                rows = conn.execute(
                    "SELECT session_id FROM chat_sessions WHERE session_id = ? AND vault_name = ?",
                    (session_id, vault_name),
                ).fetchall()
                conn.execute(
                    "DELETE FROM chat_sessions WHERE session_id = ? AND vault_name = ?",
                    (session_id, vault_name),
                )
            elif older_than_days is not None:
                rows = conn.execute(
                    """
                    SELECT session_id FROM chat_sessions
                    WHERE vault_name = ?
                    AND last_activity_at < datetime('now', ? || ' days')
                    """,
                    (vault_name, f"-{older_than_days}"),
                ).fetchall()
                conn.execute(
                    """
                    DELETE FROM chat_sessions
                    WHERE vault_name = ?
                    AND last_activity_at < datetime('now', ? || ' days')
                    """,
                    (vault_name, f"-{older_than_days}"),
                )
            else:
                rows = conn.execute(
                    "SELECT session_id FROM chat_sessions WHERE vault_name = ?",
                    (vault_name,),
                ).fetchall()
                conn.execute(
                    "DELETE FROM chat_sessions WHERE vault_name = ?",
                    (vault_name,),
                )
            conn.commit()
        finally:
            conn.close()
        return [str(row[0]) for row in rows]

    def get_message_count(
        self,
        session_id: str,
        vault_name: str,
        *,
        mode: HistoryMode = "effective",
    ) -> int:
        """Return the number of messages for one session."""
        _validate_history_mode(mode)
        if mode == "effective":
            return len(self.get_stored_messages(session_id, vault_name))
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT COUNT(*)
                FROM chat_messages
                WHERE session_id = ? AND vault_name = ?
                """,
                (session_id, vault_name),
            ).fetchone()
            return int(row[0] or 0) if row else 0
        finally:
            conn.close()

    def get_recent(
        self,
        session_id: str,
        vault_name: str,
        limit: int,
        *,
        mode: HistoryMode = "effective",
    ) -> list[ModelMessage]:
        """Return the last N messages in chronological order."""
        if limit <= 0:
            return []
        rows = self._fetch_messages(
            session_id=session_id,
            vault_name=vault_name,
            limit=limit,
            mode=mode,
        )
        return [row.message for row in rows]

    def get_recent_matching(
        self,
        session_id: str,
        vault_name: str,
        limit: int,
        predicate: Callable[[ModelMessage], bool],
    ) -> list[ModelMessage]:
        """Return the last N matching messages in chronological order."""
        if limit <= 0:
            return []
        history = self.get_history(session_id, vault_name) or []
        matched: list[ModelMessage] = []
        for msg in reversed(history):
            try:
                if predicate(msg):
                    matched.append(msg)
                    if len(matched) >= limit:
                        break
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Message predicate raised in get_recent_matching",
                    data={
                        "session_id": session_id,
                        "vault_name": vault_name,
                        "error": str(exc),
                    },
                )
                continue
        matched.reverse()
        return matched

    def add_tool_event(
        self,
        *,
        session_id: str,
        vault_name: str,
        tool_call_id: str,
        tool_name: str,
        event_type: str,
        args: dict[str, Any] | None = None,
        result_text: str | None = None,
        result_metadata: dict[str, Any] | None = None,
        artifact_ref: str | None = None,
    ) -> None:
        """Persist one structured tool event for a chat session."""
        conn = self._connect()
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            self._upsert_session(conn, session_id=session_id, vault_name=vault_name)
            conn.execute(
                """
                INSERT INTO chat_tool_events (
                    session_id,
                    vault_name,
                    tool_call_id,
                    tool_name,
                    event_type,
                    args_json,
                    result_text,
                    result_metadata_json,
                    artifact_ref
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    vault_name,
                    tool_call_id,
                    tool_name,
                    event_type,
                    (None if args is None else _json_dumps(args)),
                    result_text,
                    (None if result_metadata is None else _json_dumps(result_metadata)),
                    artifact_ref,
                ),
            )
            self._touch_session(conn, session_id=session_id, vault_name=vault_name)
            conn.commit()
        finally:
            conn.close()

    def get_tool_events(
        self,
        session_id: str,
        vault_name: str,
        *,
        limit: int | None = None,
        committed_only: bool = False,
    ) -> list[StoredChatToolEvent]:
        """Return persisted structured tool events for one session."""
        conn = self._connect()
        try:
            committed_tool_call_ids = (
                self._committed_tool_call_ids(
                    conn,
                    session_id=session_id,
                    vault_name=vault_name,
                )
                if committed_only
                else None
            )
            if committed_tool_call_ids == set():
                return []

            if limit is None or committed_tool_call_ids is not None:
                rows = conn.execute(
                    """
                    SELECT tool_call_id, tool_name, event_type, created_at, args_json, result_text, result_metadata_json, artifact_ref
                    FROM chat_tool_events
                    WHERE session_id = ? AND vault_name = ?
                    ORDER BY id ASC
                    """,
                    (session_id, vault_name),
                ).fetchall()
                if committed_tool_call_ids is not None:
                    rows = [
                        row for row in rows if str(row[0]) in committed_tool_call_ids
                    ]
                if limit is not None:
                    rows = rows[-limit:]
            else:
                rows = conn.execute(
                    """
                    SELECT tool_call_id, tool_name, event_type, created_at, args_json, result_text, result_metadata_json, artifact_ref
                    FROM (
                        SELECT tool_call_id, tool_name, event_type, created_at, args_json, result_text, result_metadata_json, artifact_ref, id
                        FROM chat_tool_events
                        WHERE session_id = ? AND vault_name = ?
                        ORDER BY id DESC
                        LIMIT ?
                    ) recent
                    ORDER BY id ASC
                    """,
                    (session_id, vault_name, limit),
                ).fetchall()
        finally:
            conn.close()

        return [_stored_tool_event_from_row(row) for row in rows]

    def get_tool_events_for_call(
        self,
        session_id: str,
        vault_name: str,
        tool_call_id: str,
    ) -> list[StoredChatToolEvent]:
        """Return persisted events for one tool call in insertion order."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT tool_call_id, tool_name, event_type, created_at, args_json,
                       result_text, result_metadata_json, artifact_ref
                FROM chat_tool_events
                WHERE session_id = ? AND vault_name = ? AND tool_call_id = ?
                ORDER BY id ASC
                """,
                (session_id, vault_name, tool_call_id),
            ).fetchall()
        finally:
            conn.close()

        return [_stored_tool_event_from_row(row) for row in rows]

    def get_tool_call_declaration_counts(
        self,
        session_id: str,
        vault_name: str,
        *,
        tool_call_ids: Sequence[str] | None = None,
    ) -> dict[str, int]:
        """Count raw tool-call declarations without hydrating message history."""
        conn = self._connect()
        try:
            return self._tool_call_declaration_counts_from_conn(
                conn, session_id, vault_name, tool_call_ids=tool_call_ids
            )
        finally:
            conn.close()

    @staticmethod
    def _tool_call_declaration_counts_from_conn(
        conn: sqlite3.Connection,
        session_id: str,
        vault_name: str,
        *,
        tool_call_ids: Sequence[str] | None = None,
    ) -> dict[str, int]:
        """Count declarations within the caller's consistent history snapshot."""
        if tool_call_ids is not None and not tool_call_ids:
            return {}
        id_filter = ""
        params: list[Any] = [session_id, vault_name]
        if tool_call_ids is not None:
            id_filter = (
                "AND json_extract(part.value, '$.tool_call_id') IN ("
                + ", ".join("?" for _ in tool_call_ids)
                + ")"
            )
            params.extend(tool_call_ids)
        rows = conn.execute(
            f"""
                SELECT json_extract(part.value, '$.tool_call_id') AS tool_call_id,
                       COUNT(*) AS declaration_count
                FROM chat_messages AS message,
                     json_each(message.message_json, '$.parts') AS part
                WHERE message.session_id = ?
                  AND message.vault_name = ?
                  AND json_extract(part.value, '$.part_kind') = 'tool-call'
                  AND json_extract(part.value, '$.tool_call_id') IS NOT NULL
                  {id_filter}
                GROUP BY tool_call_id
                """,
            params,
        ).fetchall()
        return {str(tool_call_id): int(count) for tool_call_id, count in rows}

    def _committed_tool_call_ids(
        self,
        conn: Any,
        *,
        session_id: str,
        vault_name: str,
    ) -> set[str]:
        rows = conn.execute(
            """
            SELECT message_json
            FROM chat_messages
            WHERE session_id = ? AND vault_name = ?
            ORDER BY sequence_index ASC
            """,
            (session_id, vault_name),
        ).fetchall()
        ids: set[str] = set()
        for (message_json,) in rows:
            ids.update(_tool_call_ids_from_json(str(message_json)))
        return ids

    def list_sessions(
        self, vault_name: str, *, limit: int | None = None
    ) -> list[StoredChatSession]:
        """Return chat sessions for one vault ordered by latest activity descending."""
        conn = self._connect()
        try:
            if limit is None:
                rows = conn.execute(
                    """
                    SELECT session_id, vault_name, owner_principal_id, created_at, last_activity_at, title, metadata_json
                    FROM chat_sessions
                    WHERE vault_name = ?
                    ORDER BY last_activity_at DESC, created_at DESC, session_id DESC
                    """,
                    (vault_name,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT session_id, vault_name, owner_principal_id, created_at, last_activity_at, title, metadata_json
                    FROM chat_sessions
                    WHERE vault_name = ?
                    ORDER BY last_activity_at DESC, created_at DESC, session_id DESC
                    LIMIT ?
                    """,
                    (vault_name, limit),
                ).fetchall()
        finally:
            conn.close()

        return [
            StoredChatSession(
                session_id=str(session_id),
                vault_name=str(session_vault_name),
                owner_principal_id=str(owner_principal_id),
                created_at=str(created_at or ""),
                last_activity_at=str(last_activity_at or ""),
                title=None if title is None else str(title),
                metadata_json=None if metadata_json is None else str(metadata_json),
            )
            for session_id, session_vault_name, owner_principal_id, created_at, last_activity_at, title, metadata_json in rows
        ]

    def get_session(self, session_id: str, vault_name: str) -> StoredChatSession | None:
        """Return one stored chat session summary, if present."""
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT session_id, vault_name, owner_principal_id, created_at, last_activity_at, title, metadata_json
                FROM chat_sessions
                WHERE session_id = ? AND vault_name = ?
                """,
                (session_id, vault_name),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        (
            session_id_value,
            session_vault_name,
            owner_principal_id,
            created_at,
            last_activity_at,
            title,
            metadata_json,
        ) = row
        return StoredChatSession(
            session_id=str(session_id_value),
            vault_name=str(session_vault_name),
            owner_principal_id=str(owner_principal_id),
            created_at=str(created_at or ""),
            last_activity_at=str(last_activity_at or ""),
            title=None if title is None else str(title),
            metadata_json=None if metadata_json is None else str(metadata_json),
        )

    def get_session_by_id(self, session_id: str) -> StoredChatSession | None:
        """Return one stored chat session by globally unique session ID."""
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT session_id, vault_name, owner_principal_id, created_at, last_activity_at, title, metadata_json
                FROM chat_sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        (
            session_id_value,
            session_vault_name,
            owner_principal_id,
            created_at,
            last_activity_at,
            title,
            metadata_json,
        ) = row
        return StoredChatSession(
            session_id=str(session_id_value),
            vault_name=str(session_vault_name),
            owner_principal_id=str(owner_principal_id),
            created_at=str(created_at or ""),
            last_activity_at=str(last_activity_at or ""),
            title=None if title is None else str(title),
            metadata_json=None if metadata_json is None else str(metadata_json),
        )

    def get_session_metadata(
        self,
        session_id: str,
        vault_name: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        """Return parsed session metadata, ignoring malformed stored JSON."""
        if connection is not None:
            return self._session_metadata(
                connection,
                session_id=session_id,
                vault_name=vault_name,
            )
        session = self.get_session(session_id, vault_name)
        if session is None or not session.metadata_json:
            return {}
        try:
            parsed = json.loads(session.metadata_json)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def update_session_metadata(
        self,
        *,
        session_id: str,
        vault_name: str,
        metadata_update: dict[str, Any] | None = None,
        remove_keys: tuple[str, ...] = (),
        advance_history_revision: bool = False,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        """Merge or remove session metadata keys."""
        if connection is None:
            with self.transaction() as conn:
                self.update_session_metadata(
                    session_id=session_id,
                    vault_name=vault_name,
                    metadata_update=metadata_update,
                    remove_keys=remove_keys,
                    advance_history_revision=advance_history_revision,
                    connection=conn,
                )
            return
        self._upsert_session(connection, session_id=session_id, vault_name=vault_name)
        metadata = self._session_metadata(
            connection,
            session_id=session_id,
            vault_name=vault_name,
        )
        for key in remove_keys:
            metadata.pop(key, None)
        if metadata_update:
            metadata.update(metadata_update)
        if advance_history_revision:
            metadata["history_revision"] = _metadata_history_revision(metadata) + 1
        connection.execute(
            """
            UPDATE chat_sessions
            SET last_activity_at = CURRENT_TIMESTAMP, metadata_json = ?
            WHERE session_id = ? AND vault_name = ?
            """,
            (
                json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                session_id,
                vault_name,
            ),
        )

    def set_session_workspace(
        self,
        *,
        session_id: str,
        vault_name: str,
        workspace_path: str | None,
    ) -> None:
        """Set or clear the workspace path stored in session metadata."""
        conn = self._connect()
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            self._upsert_session(conn, session_id=session_id, vault_name=vault_name)
            metadata = self._session_metadata(
                conn,
                session_id=session_id,
                vault_name=vault_name,
            )
            normalized_path = (workspace_path or "").strip()
            if normalized_path:
                metadata["workspace"] = {"path": normalized_path}
            else:
                metadata.pop("workspace", None)
            conn.execute(
                """
                UPDATE chat_sessions
                SET last_activity_at = CURRENT_TIMESTAMP, metadata_json = ?
                WHERE session_id = ? AND vault_name = ?
                """,
                (
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    session_id,
                    vault_name,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_session_workspace_path(self, session_id: str, vault_name: str) -> str:
        """Return the stored workspace path for one session, if set."""
        metadata = self.get_session_metadata(session_id, vault_name)
        workspace = metadata.get("workspace")
        if not isinstance(workspace, dict):
            return ""
        path = workspace.get("path")
        return str(path).strip() if path is not None else ""

    def set_session_chat_mode(
        self, *, session_id: str, vault_name: str, chat_mode: str
    ) -> None:
        """Persist the selected chat mode in session metadata."""
        normalized = (
            "inline_edit"
            if str(chat_mode).strip().lower() == "inline_edit"
            else "normal"
        )
        self.update_session_metadata(
            session_id=session_id,
            vault_name=vault_name,
            metadata_update={"chat_mode": normalized},
        )

    def get_session_chat_mode(
        self, session_id: str, vault_name: str
    ) -> Literal["normal", "inline_edit"]:
        """Return the selected chat mode for one session."""
        value = str(
            self.get_session_metadata(session_id, vault_name).get("chat_mode")
            or "normal"
        )
        return "inline_edit" if value.strip().lower() == "inline_edit" else "normal"

    def get_session_history_revision(self, session_id: str, vault_name: str) -> int:
        """Return the monotonic effective-history revision for one session."""
        return _metadata_history_revision(
            self.get_session_metadata(session_id, vault_name)
        )

    def get_latest_compaction_checkpoint(
        self,
        session_id: str,
        vault_name: str,
    ) -> StoredContextCheckpoint | None:
        """Return the latest effective-history checkpoint for one session."""
        return self.get_latest_context_checkpoint(session_id, vault_name)

    def get_latest_context_checkpoint(
        self,
        session_id: str,
        vault_name: str,
    ) -> StoredContextCheckpoint | None:
        """Return the latest effective-history checkpoint for one session."""
        conn = self._connect()
        try:
            checkpoint = self._latest_context_checkpoint(
                conn,
                session_id=session_id,
                vault_name=vault_name,
            )
        finally:
            conn.close()
        return checkpoint

    def list_context_checkpoints(
        self,
        session_id: str,
        vault_name: str,
        *,
        checkpoint_kind: ContextCheckpointKind | None = None,
    ) -> list[StoredContextCheckpoint]:
        """Return append-only context checkpoints in creation order."""
        conn = self._connect()
        try:
            kind_filter = ""
            params: list[Any] = [session_id, vault_name]
            if checkpoint_kind is not None:
                kind_filter = "AND checkpoint_kind = ?"
                params.append(checkpoint_kind)
            rows = conn.execute(
                f"""
                SELECT id, checkpoint_id, session_id, vault_name, created_at,
                       source, checkpoint_kind, message_count_before,
                       last_message_sequence_index, summary_message_json,
                       replacement_history_json,
                       replacement_source_sequence_indexes_json, metadata_json
                FROM chat_compaction_checkpoints
                WHERE session_id = ? AND vault_name = ?
                {kind_filter}
                ORDER BY id ASC
                """,
                params,
            ).fetchall()
        finally:
            conn.close()
        return [self._context_checkpoint_from_row(row) for row in rows]

    def get_highest_message_sequence_index(
        self, session_id: str, vault_name: str
    ) -> int:
        """Return the current raw message high-water mark for one session."""
        conn = self._connect()
        try:
            return self._highest_message_sequence_index(
                conn,
                session_id=session_id,
                vault_name=vault_name,
            )
        finally:
            conn.close()

    def add_compaction_checkpoint(
        self,
        *,
        session_id: str,
        vault_name: str,
        checkpoint_id: str,
        source: str,
        message_count_before: int,
        last_message_sequence_index: int,
        summary_message: ModelMessage,
        replacement_history: list[ModelMessage],
        replacement_source_sequence_indexes: list[int | None] | None = None,
        metadata: dict[str, Any] | None = None,
        metadata_update: dict[str, Any] | None = None,
        expected_history_revision: int | None = None,
    ) -> None:
        """Record a compaction checkpoint without mutating raw chat messages."""
        self.add_context_checkpoint(
            session_id=session_id,
            vault_name=vault_name,
            checkpoint_id=checkpoint_id,
            checkpoint_kind="recovery_card",
            source=source,
            message_count_before=message_count_before,
            last_message_sequence_index=last_message_sequence_index,
            summary_message=summary_message,
            replacement_history=replacement_history,
            replacement_source_sequence_indexes=(replacement_source_sequence_indexes),
            metadata=metadata,
            metadata_update=metadata_update,
            expected_history_revision=expected_history_revision,
        )

    def add_context_checkpoint(
        self,
        *,
        session_id: str,
        vault_name: str,
        checkpoint_id: str,
        checkpoint_kind: ContextCheckpointKind,
        source: str,
        message_count_before: int,
        last_message_sequence_index: int,
        summary_message: ModelMessage,
        replacement_history: list[ModelMessage],
        replacement_source_sequence_indexes: list[int | None] | None = None,
        metadata: dict[str, Any] | None = None,
        metadata_update: dict[str, Any] | None = None,
        expected_history_revision: int | None = None,
    ) -> StoredContextCheckpoint:
        """Atomically record and return one typed effective-history checkpoint."""
        if checkpoint_kind not in {"recovery_card", "session_map"}:
            raise ValueError(f"Unsupported context checkpoint kind: {checkpoint_kind}")
        with self.transaction() as conn:
            self._upsert_session(conn, session_id=session_id, vault_name=vault_name)
            current_revision = _metadata_history_revision(
                self._session_metadata(
                    conn,
                    session_id=session_id,
                    vault_name=vault_name,
                )
            )
            if (
                expected_history_revision is not None
                and current_revision != expected_history_revision
            ):
                raise ValueError(
                    "Context checkpoint history revision changed: "
                    f"expected {expected_history_revision}, found {current_revision}"
                )
            persist_reasoning = get_persist_model_reasoning_parts()
            summary_message = _message_for_persistence(
                summary_message,
                persist_reasoning_parts=persist_reasoning,
            )
            replacement_origins = (
                None
                if replacement_source_sequence_indexes is None
                else _aligned_checkpoint_replacement_origins(
                    replacement_history, replacement_source_sequence_indexes
                )
            )
            replacement_history = [
                (
                    message
                    if replacement_origins is not None
                    and replacement_origins[index] is not None
                    else _message_for_persistence(
                        message,
                        persist_reasoning_parts=persist_reasoning,
                    )
                )
                for index, message in enumerate(replacement_history)
            ]
            if replacement_source_sequence_indexes is not None:
                self._validate_checkpoint_replacement_origins(
                    conn,
                    session_id=session_id,
                    vault_name=vault_name,
                    messages=replacement_history,
                    origins=replacement_source_sequence_indexes,
                    last_message_sequence_index=last_message_sequence_index,
                )
            conn.execute(
                """
                INSERT INTO chat_compaction_checkpoints (
                    checkpoint_id,
                    session_id,
                    vault_name,
                    source,
                    checkpoint_kind,
                    message_count_before,
                    last_message_sequence_index,
                    summary_message_json,
                    replacement_history_json,
                    replacement_source_sequence_indexes_json,
                    metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint_id,
                    session_id,
                    vault_name,
                    source,
                    checkpoint_kind,
                    message_count_before,
                    last_message_sequence_index,
                    _MODEL_MESSAGE_ADAPTER.dump_json(summary_message).decode("utf-8"),
                    _MODEL_MESSAGE_LIST_ADAPTER.dump_json(replacement_history).decode(
                        "utf-8"
                    ),
                    (
                        None
                        if replacement_source_sequence_indexes is None
                        else json.dumps(replacement_source_sequence_indexes)
                    ),
                    (
                        None
                        if metadata is None
                        else json.dumps(metadata, ensure_ascii=False, sort_keys=True)
                    ),
                ),
            )
            self._touch_session(
                conn,
                session_id=session_id,
                vault_name=vault_name,
                metadata_update=metadata_update,
                advance_history_revision=True,
            )
            row = conn.execute(
                """
                SELECT id, checkpoint_id, session_id, vault_name, created_at,
                       source, checkpoint_kind, message_count_before,
                       last_message_sequence_index, summary_message_json,
                       replacement_history_json,
                       replacement_source_sequence_indexes_json, metadata_json
                FROM chat_compaction_checkpoints
                WHERE checkpoint_id = ? AND session_id = ? AND vault_name = ?
                """,
                (checkpoint_id, session_id, vault_name),
            ).fetchone()
            if row is None:  # pragma: no cover - guarded by the insert above
                raise RuntimeError("Inserted context checkpoint could not be loaded")
            checkpoint = self._context_checkpoint_from_row(row)
        return checkpoint

    def _fetch_messages(
        self,
        *,
        session_id: str,
        vault_name: str,
        limit: int | None = None,
        mode: HistoryMode = "effective",
    ) -> list[StoredChatMessage]:
        _validate_history_mode(mode)
        conn = self._connect()
        try:
            if mode == "raw":
                return self._fetch_raw_messages_from_conn(
                    conn,
                    session_id=session_id,
                    vault_name=vault_name,
                    limit=limit,
                )
            return self._fetch_effective_messages_from_conn(
                conn,
                session_id=session_id,
                vault_name=vault_name,
                limit=limit,
            )
        finally:
            conn.close()

    def _fetch_effective_messages_from_conn(
        self,
        conn: sqlite3.Connection,
        *,
        session_id: str,
        vault_name: str,
        limit: int | None = None,
    ) -> list[StoredChatMessage]:
        checkpoint = self._latest_context_checkpoint(
            conn,
            session_id=session_id,
            vault_name=vault_name,
        )
        if checkpoint is None:
            return self._fetch_raw_messages_from_conn(
                conn,
                session_id=session_id,
                vault_name=vault_name,
                limit=limit,
            )
        replacement = self._checkpoint_replacement_messages(
            conn,
            checkpoint,
            session_id=session_id,
            vault_name=vault_name,
        )
        raw_after = self._fetch_raw_messages_from_conn(
            conn,
            session_id=session_id,
            vault_name=vault_name,
            after_sequence_index=checkpoint.last_message_sequence_index,
        )
        messages = [*replacement, *raw_after]
        if limit is not None:
            messages = messages[-limit:]
        return messages

    def _fetch_raw_messages_from_conn(
        self,
        conn: sqlite3.Connection,
        *,
        session_id: str,
        vault_name: str,
        limit: int | None = None,
        after_sequence_index: int | None = None,
        through_sequence_index: int | None = None,
    ) -> list[StoredChatMessage]:
        sequence_filter = ""
        params: list[Any] = [session_id, vault_name]
        if after_sequence_index is not None:
            sequence_filter = "AND sequence_index > ?"
            params.append(after_sequence_index)
        if through_sequence_index is not None:
            sequence_filter += " AND sequence_index <= ?"
            params.append(through_sequence_index)

        if limit is None:
            rows = conn.execute(
                f"""
                SELECT sequence_index, direction, message_type, role, content_text, created_at, message_json
                FROM chat_messages
                WHERE session_id = ? AND vault_name = ?
                {sequence_filter}
                ORDER BY sequence_index ASC
                """,
                params,
            ).fetchall()
        else:
            params.append(limit)
            rows = conn.execute(
                f"""
                SELECT sequence_index, direction, message_type, role, content_text, created_at, message_json
                FROM (
                    SELECT sequence_index, direction, message_type, role, content_text, created_at, message_json
                    FROM chat_messages
                    WHERE session_id = ? AND vault_name = ?
                    {sequence_filter}
                    ORDER BY sequence_index DESC
                    LIMIT ?
                ) recent
                ORDER BY sequence_index ASC
                """,
                params,
            ).fetchall()
        return self._stored_messages_from_rows(
            rows,
            session_id=session_id,
            vault_name=vault_name,
        )

    def _checkpoint_replacement_messages(
        self,
        conn: sqlite3.Connection,
        checkpoint: StoredContextCheckpoint,
        *,
        session_id: str,
        vault_name: str,
    ) -> list[StoredChatMessage]:
        try:
            messages = _MODEL_MESSAGE_LIST_ADAPTER.validate_json(
                checkpoint.replacement_history_json
            )
        except ValidationError as exc:
            error = ChatHistoryCorruptionError(
                session_id=session_id,
                vault_name=vault_name,
                checkpoint_id=checkpoint.checkpoint_id,
            )
            logger.warning(
                "Failed to deserialize compaction checkpoint replacement history",
                data={
                    "event": "chat_history_deserialization_failed",
                    "status": "failed",
                    "reason": "invalid_checkpoint_replacement",
                    "issue": f"chat-history-corruption:{checkpoint.checkpoint_id}",
                    "session_id": session_id,
                    "vault_name": vault_name,
                    "checkpoint_id": checkpoint.checkpoint_id,
                    "error_type": type(error).__name__,
                    "cause_type": type(exc).__name__,
                    "error": str(error),
                },
            )
            raise error from exc

        origins = self._checkpoint_replacement_origins(
            conn,
            checkpoint=checkpoint,
            messages=messages,
            session_id=session_id,
            vault_name=vault_name,
        )
        stored_messages: list[StoredChatMessage] = []
        for sequence_index, message in enumerate(messages):
            role, content_text = extract_role_and_text(message)
            direction = (
                "response" if type(message).__name__ == "ModelResponse" else "request"
            )
            stored_messages.append(
                StoredChatMessage(
                    sequence_index=sequence_index,
                    fork_sequence_index=origins[sequence_index],
                    direction=direction,
                    message_type=type(message).__name__,
                    role=role,
                    content_text=content_text,
                    created_at=checkpoint.created_at,
                    message_json=_MODEL_MESSAGE_ADAPTER.dump_json(message).decode(
                        "utf-8"
                    ),
                    message=message,
                )
            )
        return stored_messages

    def _checkpoint_replacement_origins(
        self,
        conn: sqlite3.Connection,
        *,
        checkpoint: StoredContextCheckpoint,
        messages: list[ModelMessage],
        session_id: str,
        vault_name: str,
    ) -> list[int | None]:
        """Resolve canonical origins, including legacy checkpoint compatibility."""
        encoded = checkpoint.replacement_source_sequence_indexes_json
        if encoded is not None:
            try:
                parsed_origins = json.loads(encoded)
                return self._validate_checkpoint_replacement_origins(
                    conn,
                    session_id=session_id,
                    vault_name=vault_name,
                    messages=messages,
                    origins=parsed_origins,
                    last_message_sequence_index=checkpoint.last_message_sequence_index,
                )
            except ValueError as exc:
                error = ChatHistoryCorruptionError(
                    session_id=session_id,
                    vault_name=vault_name,
                    checkpoint_id=checkpoint.checkpoint_id,
                )
                logger.warning(
                    "Invalid checkpoint replacement origins",
                    data={
                        "event": "chat_history_deserialization_failed",
                        "status": "failed",
                        "reason": "invalid_checkpoint_replacement_origins",
                        "issue": f"chat-history-corruption:{checkpoint.checkpoint_id}",
                        "session_id": session_id,
                        "vault_name": vault_name,
                        "checkpoint_id": checkpoint.checkpoint_id,
                        "error_type": type(error).__name__,
                        "cause_type": type(exc).__name__,
                        "error": str(error),
                    },
                )
                raise error from exc

        serialized_messages = [
            _MODEL_MESSAGE_ADAPTER.dump_json(message).decode("utf-8")
            for message in messages
        ]
        candidate_json = json.dumps(
            list(dict.fromkeys(serialized_messages)),
            ensure_ascii=False,
        )
        raw = conn.execute(
            """
            SELECT sequence_index, message_json
            FROM chat_messages
            WHERE session_id = ? AND vault_name = ?
              AND sequence_index <= ?
              AND message_json IN (
                  SELECT value FROM json_each(?)
              )
            ORDER BY sequence_index ASC
            """,
            (
                session_id,
                vault_name,
                checkpoint.last_message_sequence_index,
                candidate_json,
            ),
        ).fetchall()
        candidates_by_json: dict[str, list[int]] = {}
        for sequence_index, message_json in raw:
            candidates_by_json.setdefault(str(message_json), []).append(
                int(sequence_index)
            )
        resolved_origins: list[int | None] = []
        previous_origin = -1
        for message_json in serialized_messages:
            candidates = [
                index
                for index in candidates_by_json.get(message_json, [])
                if index > previous_origin
            ]
            if len(candidates) != 1:
                resolved_origins.append(None)
                continue
            origin = candidates[0]
            resolved_origins.append(origin)
            previous_origin = origin
        return resolved_origins

    def _validate_checkpoint_replacement_origins(
        self,
        conn: sqlite3.Connection,
        *,
        session_id: str,
        vault_name: str,
        messages: list[ModelMessage],
        origins: object,
        last_message_sequence_index: int,
    ) -> list[int | None]:
        """Require explicit origins to identify ordered canonical messages."""
        validated_origins = _aligned_checkpoint_replacement_origins(messages, origins)
        previous_origin = -1
        canonical_origins: list[int] = []
        for origin in validated_origins:
            if origin is None:
                continue
            if origin < 0 or origin > last_message_sequence_index:
                raise ValueError(
                    "Checkpoint replacement origins must fall within its canonical boundary"
                )
            if origin <= previous_origin:
                raise ValueError(
                    "Checkpoint replacement origins must be strictly increasing"
                )
            previous_origin = origin
            canonical_origins.append(origin)
        if not canonical_origins:
            return validated_origins

        canonical_messages = self._fetch_raw_messages_from_conn(
            conn,
            session_id=session_id,
            vault_name=vault_name,
            after_sequence_index=canonical_origins[0] - 1,
            through_sequence_index=canonical_origins[-1],
        )
        canonical_by_origin = {
            item.sequence_index: _MODEL_MESSAGE_ADAPTER.dump_json(item.message)
            for item in canonical_messages
        }
        for message, origin in zip(messages, validated_origins, strict=True):
            if origin is None:
                continue
            canonical_message_json = canonical_by_origin.get(origin)
            if canonical_message_json is None:
                raise ValueError(
                    "Checkpoint replacement origin must identify an existing canonical message"
                )
            if _MODEL_MESSAGE_ADAPTER.dump_json(message) != canonical_message_json:
                raise ValueError(
                    "Checkpoint replacement must match its canonical message origin"
                )
        return validated_origins

    def _stored_messages_from_rows(
        self,
        rows: Sequence[Sequence[Any]],
        *,
        session_id: str,
        vault_name: str,
    ) -> list[StoredChatMessage]:
        messages: list[StoredChatMessage] = []
        for row in rows:
            (
                sequence_index,
                direction,
                message_type,
                role,
                content_text,
                created_at,
                message_json,
            ) = row
            try:
                message = _MODEL_MESSAGE_ADAPTER.validate_json(message_json)
            except ValidationError as exc:
                error = ChatHistoryCorruptionError(
                    session_id=session_id,
                    vault_name=vault_name,
                    sequence_index=int(sequence_index),
                )
                logger.warning(
                    "Failed to deserialize stored chat message",
                    data={
                        "event": "chat_history_deserialization_failed",
                        "status": "failed",
                        "reason": "invalid_canonical_message",
                        "issue": f"chat-history-corruption:{session_id}:{sequence_index}",
                        "session_id": session_id,
                        "vault_name": vault_name,
                        "sequence_index": sequence_index,
                        "error_type": type(error).__name__,
                        "cause_type": type(exc).__name__,
                        "error": str(error),
                    },
                )
                raise error from exc
            messages.append(
                StoredChatMessage(
                    sequence_index=int(sequence_index),
                    fork_sequence_index=int(sequence_index),
                    direction=str(direction),
                    message_type=str(message_type),
                    role=str(role),
                    content_text=str(content_text or ""),
                    created_at=str(created_at or ""),
                    message_json=str(message_json),
                    message=message,
                )
            )
        return messages

    @staticmethod
    def _latest_context_checkpoint(
        conn: sqlite3.Connection,
        *,
        session_id: str,
        vault_name: str,
    ) -> StoredContextCheckpoint | None:
        row = conn.execute(
            """
            SELECT id, checkpoint_id, session_id, vault_name, created_at,
                   source, checkpoint_kind, message_count_before,
                   last_message_sequence_index, summary_message_json,
                   replacement_history_json,
                   replacement_source_sequence_indexes_json, metadata_json
            FROM chat_compaction_checkpoints
            WHERE session_id = ? AND vault_name = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (session_id, vault_name),
        ).fetchone()
        if row is None:
            return None
        return ChatStore._context_checkpoint_from_row(row)

    @staticmethod
    def _context_checkpoint_from_row(row: Sequence[Any]) -> StoredContextCheckpoint:
        """Convert one context-checkpoint query row through a stable mapping."""
        (
            row_id,
            checkpoint_id,
            row_session_id,
            row_vault_name,
            created_at,
            source,
            checkpoint_kind,
            message_count_before,
            last_message_sequence_index,
            summary_message_json,
            replacement_history_json,
            replacement_source_sequence_indexes_json,
            metadata_json,
        ) = row
        kind = str(checkpoint_kind)
        if kind not in {"recovery_card", "session_map"}:
            raise ValueError(f"Unsupported stored context checkpoint kind: {kind}")
        return StoredContextCheckpoint(
            id=int(row_id),
            checkpoint_id=str(checkpoint_id),
            session_id=str(row_session_id),
            vault_name=str(row_vault_name),
            created_at=str(created_at or ""),
            source=str(source),
            checkpoint_kind=cast(ContextCheckpointKind, kind),
            message_count_before=int(message_count_before),
            last_message_sequence_index=int(last_message_sequence_index),
            summary_message_json=str(summary_message_json),
            replacement_history_json=str(replacement_history_json),
            replacement_source_sequence_indexes_json=(
                None
                if replacement_source_sequence_indexes_json is None
                else str(replacement_source_sequence_indexes_json)
            ),
            metadata_json=None if metadata_json is None else str(metadata_json),
        )

    @staticmethod
    def _highest_message_sequence_index(
        conn: sqlite3.Connection, *, session_id: str, vault_name: str
    ) -> int:
        row = conn.execute(
            """
            SELECT COALESCE(MAX(sequence_index), -1)
            FROM chat_messages
            WHERE session_id = ? AND vault_name = ?
            """,
            (session_id, vault_name),
        ).fetchone()
        return int(row[0] if row else -1)

    def _connect(self) -> sqlite3.Connection:
        # Re-ensure the schema at call time so long-lived store instances remain
        # correct when the active runtime root changes across validation/system boot.
        ensure_chat_sessions_schema(self.system_root)
        return cast(
            sqlite3.Connection,
            connect_sqlite_from_system_db(DB_NAME, self.system_root),
        )

    @staticmethod
    def _upsert_session(
        conn: sqlite3.Connection,
        *,
        session_id: str,
        vault_name: str,
        owner_principal_id: str | None = None,
    ) -> None:
        existing = conn.execute(
            "SELECT owner_principal_id FROM chat_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if existing is None and owner_principal_id is None:
            raise ValueError(
                f"Owner principal is required to create chat session '{session_id}'."
            )
        normalized_owner = normalize_principal_id(
            owner_principal_id if owner_principal_id is not None else str(existing[0])
        )
        if existing is not None and str(existing[0]) != normalized_owner:
            raise ValueError(
                f"Chat session '{session_id}' belongs to a different principal."
            )
        conn.execute(
            """
            INSERT INTO chat_sessions (session_id, vault_name, owner_principal_id)
            VALUES (?, ?, ?)
            ON CONFLICT(session_id, vault_name)
            DO UPDATE SET last_activity_at = CURRENT_TIMESTAMP
            """,
            (session_id, vault_name, normalized_owner),
        )

    @staticmethod
    def _touch_session(
        conn: sqlite3.Connection,
        *,
        session_id: str,
        vault_name: str,
        metadata_update: dict[str, Any] | None = None,
        advance_history_revision: bool = False,
    ) -> None:
        if metadata_update or advance_history_revision:
            metadata = ChatStore._session_metadata(
                conn,
                session_id=session_id,
                vault_name=vault_name,
            )
            if metadata_update:
                metadata.update(metadata_update)
            if advance_history_revision:
                metadata["history_revision"] = _metadata_history_revision(metadata) + 1
            conn.execute(
                """
                UPDATE chat_sessions
                SET last_activity_at = CURRENT_TIMESTAMP, metadata_json = ?
                WHERE session_id = ? AND vault_name = ?
                """,
                (
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    session_id,
                    vault_name,
                ),
            )
            return
        conn.execute(
            """
            UPDATE chat_sessions
            SET last_activity_at = CURRENT_TIMESTAMP
            WHERE session_id = ? AND vault_name = ?
            """,
            (session_id, vault_name),
        )

    @staticmethod
    def _session_metadata(
        conn: sqlite3.Connection, *, session_id: str, vault_name: str
    ) -> dict[str, Any]:
        row = conn.execute(
            """
            SELECT metadata_json
            FROM chat_sessions
            WHERE session_id = ? AND vault_name = ?
            """,
            (session_id, vault_name),
        ).fetchone()
        metadata: dict[str, Any] = {}
        if row and row[0]:
            try:
                parsed = json.loads(str(row[0]))
                if isinstance(parsed, dict):
                    metadata = parsed
            except Exception:
                metadata = {}
        return metadata

    @staticmethod
    def _next_sequence_index(
        conn: sqlite3.Connection, *, session_id: str, vault_name: str
    ) -> int:
        row = conn.execute(
            """
            SELECT COALESCE(MAX(sequence_index), -1) + 1
            FROM chat_messages
            WHERE session_id = ? AND vault_name = ?
            """,
            (session_id, vault_name),
        ).fetchone()
        return int(row[0] or 0) if row else 0

    @staticmethod
    def _merged_session_metadata_json(
        conn: sqlite3.Connection,
        *,
        session_id: str,
        vault_name: str,
        metadata_update: dict[str, Any],
    ) -> str:
        metadata = ChatStore._session_metadata(
            conn,
            session_id=session_id,
            vault_name=vault_name,
        )
        metadata.update(metadata_update)
        return json.dumps(metadata, ensure_ascii=False, sort_keys=True)


def _validate_history_mode(mode: str) -> None:
    if mode not in {"effective", "raw"}:
        raise ValueError("history mode must be one of: effective, raw")


def _metadata_history_revision(metadata: dict[str, Any]) -> int:
    raw = metadata.get("history_revision")
    if raw is None:
        return 0
    try:
        revision = int(raw)
    except (TypeError, ValueError):
        return 0
    return max(revision, 0)


def canonical_assistant_fork_points(messages: Sequence[StoredChatMessage]) -> set[int]:
    """Return known canonical assistant origins whose prefixes are protocol-complete."""
    protocol = ToolHistoryProtocolState()
    fork_points: set[int] = set()
    for index, message in enumerate(messages):
        calls, replies = model_message_tool_invocations(message.message)
        protocol.record_message(index, calls, replies)
        if protocol.issues:
            break
        if (
            isinstance(message.message, ModelResponse)
            and message.fork_sequence_index is not None
            and not protocol.pending
        ):
            fork_points.add(message.fork_sequence_index)
    return fork_points


def _fork_session_metadata(source_metadata: dict[str, Any]) -> dict[str, Any]:
    """Return source session metadata that is safe to carry into a fork."""
    metadata = dict(source_metadata)
    for key in (
        "history_revision",
        "last_compaction",
        "last_session_map_checkpoint",
        "latest_turn_failure",
    ):
        metadata.pop(key, None)
    return metadata


def _checkpoint_metadata(checkpoint: StoredContextCheckpoint) -> dict[str, Any]:
    """Return one checkpoint's object metadata or an empty object."""
    if not checkpoint.metadata_json:
        return {}
    try:
        parsed = json.loads(checkpoint.metadata_json)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _aligned_checkpoint_replacement_origins(
    messages: Sequence[ModelMessage], origins: object
) -> list[int | None]:
    """Require one integer or null origin per replacement message."""
    if not isinstance(origins, list) or len(origins) != len(messages):
        raise ValueError(
            "Checkpoint replacement origins must align with replacement history"
        )
    if any(origin is not None and type(origin) is not int for origin in origins):
        raise ValueError("Checkpoint replacement origins must be integers or null")
    return cast(list[int | None], origins)


def _checkpoint_observation_boundary(checkpoint: StoredContextCheckpoint) -> int:
    """Return the newest canonical message visible to a checkpoint author."""
    invalid_boundary = (
        f"Invalid checkpoint observation boundary: {checkpoint.checkpoint_id}"
    )
    if checkpoint.last_message_sequence_index < 0:
        raise ValueError(invalid_boundary)
    if checkpoint.checkpoint_kind != "session_map":
        return checkpoint.last_message_sequence_index
    if checkpoint.metadata_json is None:
        return checkpoint.last_message_sequence_index
    try:
        metadata = json.loads(checkpoint.metadata_json)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(invalid_boundary) from exc
    if not isinstance(metadata, dict):
        raise ValueError(invalid_boundary)
    # Early map checkpoints did not record the author observation cutoff.
    # Their consumed boundary is the only durable inheritance boundary available.
    if "map_observed_through_sequence_index" not in metadata:
        return checkpoint.last_message_sequence_index
    observed = metadata["map_observed_through_sequence_index"]
    if type(observed) is not int or observed < checkpoint.last_message_sequence_index:
        raise ValueError(invalid_boundary)
    return observed


def _message_for_persistence(
    message: ModelMessage,
    *,
    persist_reasoning_parts: bool,
) -> ModelMessage:
    """Return the message shape that should be serialized to durable history."""
    if persist_reasoning_parts or not isinstance(message, ModelResponse):
        return message
    filtered_parts = [
        part for part in message.parts if not isinstance(part, ThinkingPart)
    ]
    portable_parts = _strip_response_provider_item_ids(filtered_parts)
    if portable_parts == list(message.parts):
        return message
    return replace(message, parts=portable_parts)


def _message_for_model_history(
    message: ModelMessage,
    *,
    persist_reasoning_parts: bool,
) -> ModelMessage:
    """Return the message shape that is safe to replay as model history."""
    if not isinstance(message, ModelResponse):
        return message
    if not persist_reasoning_parts:
        return _message_for_persistence(
            message,
            persist_reasoning_parts=False,
        )
    if any(isinstance(part, ThinkingPart) for part in message.parts):
        return message
    portable_parts = _strip_response_provider_item_ids(message.parts)
    if portable_parts == list(message.parts):
        return message
    return replace(message, parts=portable_parts)


def _strip_response_provider_item_ids(parts: Sequence[Any]) -> list[Any]:
    """Remove provider response item ids that require exact reasoning-item replay."""
    return [_strip_response_part_provider_item_id(part) for part in parts]


def _strip_response_part_provider_item_id(part: Any) -> Any:
    untyped_part: Any = part
    if not is_dataclass(untyped_part):
        return untyped_part
    field_names = {field.name for field in fields(untyped_part)}
    updates: dict[str, Any] = {}
    if "id" in field_names and getattr(untyped_part, "id", None) is not None:
        updates["id"] = None
    if "tool_call_id" in field_names:
        tool_call_id = getattr(untyped_part, "tool_call_id", None)
        if isinstance(tool_call_id, str) and "|" in tool_call_id:
            updates["tool_call_id"] = tool_call_id.split("|", 1)[0]
    if not updates:
        return untyped_part
    # `is_dataclass` is the runtime guard, but typeshed cannot narrow `Any` to
    # its private dataclass protocol for `replace`.
    return replace(untyped_part, **updates)  # type: ignore[type-var]


def _tool_call_ids_from_json(message_json: str) -> set[str]:
    try:
        message = _MODEL_MESSAGE_ADAPTER.validate_json(message_json)
    except Exception:
        return set()
    return _tool_call_ids_from_message(message)


def _tool_call_ids_from_message(message: ModelMessage) -> set[str]:
    return set(_ordered_tool_call_ids_from_message(message))


def _ordered_tool_call_ids_from_message(message: ModelMessage) -> tuple[str, ...]:
    ids: set[str] = set()
    ordered_ids: list[str] = []
    for part in getattr(message, "parts", ()) or ():
        if isinstance(part, ToolCallPart):
            tool_call_id = getattr(part, "tool_call_id", None)
            if tool_call_id and str(tool_call_id) not in ids:
                ids.add(str(tool_call_id))
                ordered_ids.append(str(tool_call_id))
    return tuple(ordered_ids)


def _tool_return_ids_from_message(message: ModelMessage) -> set[str]:
    return set(_ordered_tool_return_ids_from_message(message))


def _ordered_tool_return_ids_from_message(message: ModelMessage) -> tuple[str, ...]:
    ids: set[str] = set()
    ordered_ids: list[str] = []
    for part in getattr(message, "parts", ()) or ():
        if isinstance(part, ToolReturnPart | NativeToolReturnPart) or (
            isinstance(part, RetryPromptPart) and part.tool_name is not None
        ):
            tool_call_id = getattr(part, "tool_call_id", None)
            if tool_call_id and str(tool_call_id) not in ids:
                ids.add(str(tool_call_id))
                ordered_ids.append(str(tool_call_id))
    return tuple(ordered_ids)
