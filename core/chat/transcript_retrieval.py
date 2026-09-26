"""Authorized retrieval over canonical raw chat transcripts."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from core.database import connect_sqlite_from_system_db
from core.utils.fts import build_fts_query

from .chat_store import ChatStore, StoredChatMessage
from .schema import DB_NAME, rebuild_chat_message_fts
from .session_access import ChatSessionAccessService

DEFAULT_SEARCH_LIMIT = 5
MAX_SEARCH_LIMIT = 20
DEFAULT_EXCERPT_CHARS = 600


@dataclass(frozen=True)
class TranscriptAnchor:
    """Stable public identity for one canonical chat message."""

    session_id: str
    sequence_index: int


@dataclass(frozen=True)
class TranscriptSearchHit:
    """One strategy-neutral transcript search result."""

    anchor: TranscriptAnchor
    vault_name: str
    rank: int
    role: str
    message_type: str
    created_at: str
    excerpt: str


class TranscriptRetrievalService:
    """Search and read canonical transcripts behind session authorization."""

    def __init__(
        self,
        store: ChatStore,
        session_access: ChatSessionAccessService,
        *,
        excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
    ) -> None:
        if excerpt_chars <= 0:
            raise ValueError("excerpt_chars must be positive")
        self._store = store
        self._session_access = session_access
        self._excerpt_chars = excerpt_chars

    def search(
        self,
        *,
        vault_name: str,
        session_id: str,
        query: str,
        limit: int = DEFAULT_SEARCH_LIMIT,
    ) -> list[TranscriptSearchHit]:
        """Return bounded lexical matches from one authorized session."""
        self._require_session(vault_name=vault_name, session_id=session_id)
        normalized_query = build_fts_query(query)
        if not normalized_query:
            raise ValueError("Transcript search query must contain searchable text.")
        if not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise ValueError(
                f"Transcript search limit must be between 1 and {MAX_SEARCH_LIMIT}."
            )

        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT messages.session_id,
                       messages.vault_name,
                       messages.sequence_index,
                       messages.role,
                       messages.message_type,
                       messages.created_at,
                       snippet(chat_messages_fts, 0, '[', ']', '...', 32) AS excerpt,
                       bm25(chat_messages_fts) AS lexical_rank
                FROM chat_messages_fts
                JOIN chat_messages AS messages
                  ON messages.id = chat_messages_fts.rowid
                WHERE chat_messages_fts MATCH ?
                  AND messages.vault_name = ?
                  AND messages.session_id = ?
                ORDER BY lexical_rank ASC, messages.sequence_index ASC
                LIMIT ?
                """,
                (normalized_query, vault_name, session_id, limit),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            raise ValueError("Transcript search query could not be evaluated.") from exc
        finally:
            conn.close()

        return [
            TranscriptSearchHit(
                anchor=TranscriptAnchor(
                    session_id=str(row["session_id"]),
                    sequence_index=int(row["sequence_index"]),
                ),
                vault_name=str(row["vault_name"]),
                rank=rank,
                role=str(row["role"]),
                message_type=str(row["message_type"]),
                created_at=str(row["created_at"] or ""),
                excerpt=_bounded_excerpt(
                    str(row["excerpt"] or ""), self._excerpt_chars
                ),
            )
            for rank, row in enumerate(rows, start=1)
        ]

    def get_range(
        self,
        *,
        vault_name: str,
        session_id: str,
        after_sequence_index: int,
        through_sequence_index: int,
    ) -> list[StoredChatMessage]:
        """Return an authorized canonical interval ``(after, through]``."""
        self._require_session(vault_name=vault_name, session_id=session_id)
        return self._store.get_stored_messages_range(
            session_id,
            vault_name,
            after_sequence_index=after_sequence_index,
            through_sequence_index=through_sequence_index,
        )

    def rebuild_index(self) -> None:
        """Rebuild the derived lexical index from canonical messages."""
        conn = self._connect()
        try:
            rebuild_chat_message_fts(conn)
            conn.commit()
        finally:
            conn.close()

    def _require_session(self, *, vault_name: str, session_id: str) -> None:
        session = self._session_access.require_session(session_id)
        if session.vault_name != vault_name:
            raise LookupError(f"Chat session not found: {session_id}")

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite_from_system_db(DB_NAME, self._store.system_root)


def _bounded_excerpt(value: str, max_chars: int) -> str:
    normalized = value.strip()
    if len(normalized) <= max_chars:
        return normalized
    if max_chars == 1:
        return "…"
    return normalized[: max_chars - 1].rstrip() + "…"
