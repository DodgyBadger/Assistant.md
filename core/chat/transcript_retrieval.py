"""Authorized retrieval over canonical raw chat transcripts."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
from dataclasses import dataclass
from typing import Any

from core.database import connect_sqlite_from_system_db
from core.utils.fts import build_fts_query
from core.utils.tokens import estimate_token_count

from .chat_store import ChatStore, StoredChatMessage
from .schema import DB_NAME, rebuild_chat_message_fts
from .session_access import ChatSessionAccessService

DEFAULT_SEARCH_LIMIT = 5
MAX_SEARCH_LIMIT = 20
MAX_VAULT_SEARCH_LIMIT = 200
DEFAULT_EXCERPT_CHARS = 600
DEFAULT_WINDOW_BEFORE = 2
DEFAULT_WINDOW_AFTER = 2
MAX_WINDOW_NEIGHBORS = 10
DEFAULT_WINDOW_TOKENS = 2_000
MIN_WINDOW_TOKENS = 512
MAX_WINDOW_TOKENS = 8_000
_WINDOW_CURSOR_VERSION = 1
_WINDOW_CURSOR_KEY = secrets.token_bytes(32)
_WINDOW_OUTPUT_TOKEN_RESERVE = 96


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


@dataclass(frozen=True)
class TranscriptWindowMessage:
    """One canonical message or bounded fragment returned in a window."""

    sequence_index: int
    role: str
    message_type: str
    created_at: str
    content: str
    content_start: int = 0
    content_end: int | None = None
    content_complete: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Render the message for a model-facing adapter."""
        return {
            "sequence_index": self.sequence_index,
            "role": self.role,
            "message_type": self.message_type,
            "created_at": self.created_at,
            "content": self.content,
            "content_start": self.content_start,
            "content_end": self.content_end,
            "content_complete": self.content_complete,
        }


@dataclass(frozen=True)
class TranscriptWindow:
    """Bounded canonical context around one stable message anchor."""

    session_id: str
    vault_name: str
    anchor_sequence_index: int
    history_revision: int
    requested_before: int
    requested_after: int
    max_tokens: int
    estimated_tokens: int
    compacted_through_sequence_index: int | None
    messages: tuple[TranscriptWindowMessage, ...]
    truncated_before: bool
    truncated_after: bool
    next_cursor: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Render the window with explicit trust and truncation metadata."""
        return {
            "session_id": self.session_id,
            "vault_name": self.vault_name,
            "anchor_sequence_index": self.anchor_sequence_index,
            "history_revision": self.history_revision,
            "requested_before": self.requested_before,
            "requested_after": self.requested_after,
            "max_tokens": self.max_tokens,
            "estimated_tokens": self.estimated_tokens,
            "compacted_through_sequence_index": self.compacted_through_sequence_index,
            "messages": [message.to_dict() for message in self.messages],
            "truncated_before": self.truncated_before,
            "truncated_after": self.truncated_after,
            "next_cursor": self.next_cursor,
            "historical_content_is_untrusted": True,
        }


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
        return self._search_authorized_sessions(
            vault_name=vault_name,
            session_ids=(session_id,),
            query=query,
            limit=limit,
            max_limit=MAX_SEARCH_LIMIT,
            one_hit_per_session=False,
        )

    def search_vault(
        self,
        *,
        vault_name: str,
        query: str,
        limit: int = DEFAULT_SEARCH_LIMIT,
        session_ids: set[str] | None = None,
    ) -> list[TranscriptSearchHit]:
        """Return each authorized session's best bounded lexical match."""
        authorized_ids = {
            session.session_id
            for session in self._session_access.list_sessions(vault_name)
        }
        if session_ids is not None:
            authorized_ids.intersection_update(session_ids)
        if not authorized_ids:
            return []
        return self._search_authorized_sessions(
            vault_name=vault_name,
            session_ids=tuple(sorted(authorized_ids)),
            query=query,
            limit=limit,
            max_limit=MAX_VAULT_SEARCH_LIMIT,
            one_hit_per_session=True,
        )

    def _search_authorized_sessions(
        self,
        *,
        vault_name: str,
        session_ids: tuple[str, ...],
        query: str,
        limit: int,
        max_limit: int,
        one_hit_per_session: bool,
    ) -> list[TranscriptSearchHit]:
        normalized_query = build_fts_query(query)
        if not normalized_query:
            raise ValueError("Transcript search query must contain searchable text.")
        if not 1 <= limit <= max_limit:
            raise ValueError(
                f"Transcript search limit must be between 1 and {max_limit}."
            )

        session_placeholders = ", ".join("?" for _ in session_ids)
        if one_hit_per_session:
            query_sql = f"""
                WITH message_matches AS (
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
                      AND messages.session_id IN ({session_placeholders})
                ),
                ranked_matches AS (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY session_id
                               ORDER BY lexical_rank ASC, sequence_index ASC
                           ) AS session_rank
                    FROM message_matches
                )
                SELECT session_id,
                       vault_name,
                       sequence_index,
                       role,
                       message_type,
                       created_at,
                       excerpt,
                       lexical_rank
                FROM ranked_matches
                WHERE session_rank = 1
                ORDER BY lexical_rank ASC, session_id ASC, sequence_index ASC
                LIMIT ?
            """
        else:
            query_sql = f"""
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
                  AND messages.session_id IN ({session_placeholders})
                ORDER BY lexical_rank ASC, messages.sequence_index ASC
                LIMIT ?
            """

        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                query_sql,
                (normalized_query, vault_name, *session_ids, limit),
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

    def get_window(
        self,
        *,
        vault_name: str,
        session_id: str,
        sequence_index: int,
        before: int = DEFAULT_WINDOW_BEFORE,
        after: int = DEFAULT_WINDOW_AFTER,
        max_tokens: int = DEFAULT_WINDOW_TOKENS,
        cursor: str = "",
    ) -> TranscriptWindow:
        """Return a token-bounded canonical window centered on one message."""
        self._require_session(vault_name=vault_name, session_id=session_id)
        _validate_window_request(
            sequence_index=sequence_index,
            before=before,
            after=after,
            max_tokens=max_tokens,
        )
        history_revision = self._store.get_session_history_revision(
            session_id, vault_name
        )
        continuation_offset: int | None = None
        if cursor:
            continuation_offset = _decode_window_cursor(
                cursor,
                vault_name=vault_name,
                session_id=session_id,
                sequence_index=sequence_index,
                history_revision=history_revision,
                before=before,
                after=after,
                max_tokens=max_tokens,
            )

        start = max(sequence_index - before, 0)
        end = sequence_index + after
        stored_messages = self.get_range(
            vault_name=vault_name,
            session_id=session_id,
            after_sequence_index=start - 1,
            through_sequence_index=end,
        )
        messages_by_sequence = {
            message.sequence_index: message for message in stored_messages
        }
        anchor = messages_by_sequence.get(sequence_index)
        if anchor is None:
            raise LookupError(f"Chat message not found: {session_id}@{sequence_index}")
        checkpoint = self._store.get_latest_compaction_checkpoint(
            session_id, vault_name
        )
        compacted_through = (
            checkpoint.last_message_sequence_index if checkpoint is not None else None
        )
        has_before = any(
            item.sequence_index < anchor.sequence_index for item in stored_messages
        )
        has_after = any(
            item.sequence_index > anchor.sequence_index for item in stored_messages
        )
        if continuation_offset is not None:
            return _build_fragment_window(
                anchor=anchor,
                session_id=session_id,
                vault_name=vault_name,
                history_revision=history_revision,
                before=before,
                after=after,
                max_tokens=max_tokens,
                content_offset=continuation_offset,
                compacted_through_sequence_index=compacted_through,
                truncated_before=has_before,
                truncated_after=has_after,
            )
        return _build_initial_window(
            anchor=anchor,
            stored_messages=stored_messages,
            session_id=session_id,
            vault_name=vault_name,
            history_revision=history_revision,
            before=before,
            after=after,
            max_tokens=max_tokens,
            compacted_through_sequence_index=compacted_through,
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


def _validate_window_request(
    *, sequence_index: int, before: int, after: int, max_tokens: int
) -> None:
    if sequence_index < 0:
        raise ValueError("sequence_index must be zero or greater")
    if not 0 <= before <= MAX_WINDOW_NEIGHBORS:
        raise ValueError(f"before must be between 0 and {MAX_WINDOW_NEIGHBORS}")
    if not 0 <= after <= MAX_WINDOW_NEIGHBORS:
        raise ValueError(f"after must be between 0 and {MAX_WINDOW_NEIGHBORS}")
    if not MIN_WINDOW_TOKENS <= max_tokens <= MAX_WINDOW_TOKENS:
        raise ValueError(
            f"max_tokens must be between {MIN_WINDOW_TOKENS} and {MAX_WINDOW_TOKENS}"
        )


def _build_initial_window(
    *,
    anchor: StoredChatMessage,
    stored_messages: list[StoredChatMessage],
    session_id: str,
    vault_name: str,
    history_revision: int,
    before: int,
    after: int,
    max_tokens: int,
    compacted_through_sequence_index: int | None,
) -> TranscriptWindow:
    anchor_message = _window_message(anchor)
    has_before = any(
        item.sequence_index < anchor.sequence_index for item in stored_messages
    )
    has_after = any(
        item.sequence_index > anchor.sequence_index for item in stored_messages
    )
    base = _window_payload(
        session_id=session_id,
        vault_name=vault_name,
        anchor_sequence_index=anchor.sequence_index,
        history_revision=history_revision,
        before=before,
        after=after,
        max_tokens=max_tokens,
        compacted_through_sequence_index=compacted_through_sequence_index,
        messages=[anchor_message],
        truncated_before=has_before,
        truncated_after=has_after,
        next_cursor=None,
    )
    if _payload_tokens(base) > max_tokens:
        return _build_fragment_window(
            anchor=anchor,
            session_id=session_id,
            vault_name=vault_name,
            history_revision=history_revision,
            before=before,
            after=after,
            max_tokens=max_tokens,
            content_offset=0,
            compacted_through_sequence_index=compacted_through_sequence_index,
            truncated_before=has_before,
            truncated_after=has_after,
        )

    selected = {anchor.sequence_index: anchor_message}
    neighbors = sorted(
        (
            item
            for item in stored_messages
            if item.sequence_index != anchor.sequence_index
        ),
        key=lambda item: (
            abs(item.sequence_index - anchor.sequence_index),
            item.sequence_index > anchor.sequence_index,
        ),
    )
    for neighbor in neighbors:
        candidate = dict(selected)
        candidate[neighbor.sequence_index] = _window_message(neighbor)
        ordered = [candidate[index] for index in sorted(candidate)]
        test_payload = _window_payload(
            session_id=session_id,
            vault_name=vault_name,
            anchor_sequence_index=anchor.sequence_index,
            history_revision=history_revision,
            before=before,
            after=after,
            max_tokens=max_tokens,
            compacted_through_sequence_index=compacted_through_sequence_index,
            messages=ordered,
            truncated_before=any(
                item.sequence_index < anchor.sequence_index
                and item.sequence_index not in candidate
                for item in stored_messages
            ),
            truncated_after=any(
                item.sequence_index > anchor.sequence_index
                and item.sequence_index not in candidate
                for item in stored_messages
            ),
            next_cursor=None,
        )
        if _payload_tokens(test_payload) <= max_tokens:
            selected = candidate

    ordered_messages = tuple(selected[index] for index in sorted(selected))
    truncated_before = any(
        item.sequence_index < anchor.sequence_index
        and item.sequence_index not in selected
        for item in stored_messages
    )
    truncated_after = any(
        item.sequence_index > anchor.sequence_index
        and item.sequence_index not in selected
        for item in stored_messages
    )
    final_payload = _window_payload(
        session_id=session_id,
        vault_name=vault_name,
        anchor_sequence_index=anchor.sequence_index,
        history_revision=history_revision,
        before=before,
        after=after,
        max_tokens=max_tokens,
        compacted_through_sequence_index=compacted_through_sequence_index,
        messages=list(ordered_messages),
        truncated_before=truncated_before,
        truncated_after=truncated_after,
        next_cursor=None,
    )
    return TranscriptWindow(
        session_id=session_id,
        vault_name=vault_name,
        anchor_sequence_index=anchor.sequence_index,
        history_revision=history_revision,
        requested_before=before,
        requested_after=after,
        max_tokens=max_tokens,
        estimated_tokens=_payload_tokens(final_payload),
        compacted_through_sequence_index=compacted_through_sequence_index,
        messages=ordered_messages,
        truncated_before=truncated_before,
        truncated_after=truncated_after,
    )


def _build_fragment_window(
    *,
    anchor: StoredChatMessage,
    session_id: str,
    vault_name: str,
    history_revision: int,
    before: int,
    after: int,
    max_tokens: int,
    content_offset: int,
    compacted_through_sequence_index: int | None,
    truncated_before: bool,
    truncated_after: bool,
) -> TranscriptWindow:
    if not 0 <= content_offset < len(anchor.content_text):
        raise ValueError("Transcript continuation cursor is stale or exhausted.")
    low = content_offset + 1
    high = len(anchor.content_text)
    best_end = content_offset
    best_cursor: str | None = None
    best_payload: dict[str, Any] | None = None
    while low <= high:
        candidate_end = (low + high) // 2
        next_cursor = (
            _encode_window_cursor(
                vault_name=vault_name,
                session_id=session_id,
                sequence_index=anchor.sequence_index,
                history_revision=history_revision,
                before=before,
                after=after,
                max_tokens=max_tokens,
                content_offset=candidate_end,
            )
            if candidate_end < len(anchor.content_text)
            else None
        )
        fragment = _window_message(
            anchor,
            content=anchor.content_text[content_offset:candidate_end],
            content_start=content_offset,
            content_end=candidate_end,
            content_complete=content_offset == 0
            and candidate_end == len(anchor.content_text),
        )
        payload = _window_payload(
            session_id=session_id,
            vault_name=vault_name,
            anchor_sequence_index=anchor.sequence_index,
            history_revision=history_revision,
            before=before,
            after=after,
            max_tokens=max_tokens,
            compacted_through_sequence_index=compacted_through_sequence_index,
            messages=[fragment],
            truncated_before=truncated_before,
            truncated_after=truncated_after,
            next_cursor=next_cursor,
        )
        if _payload_tokens(payload) <= max_tokens:
            best_end = candidate_end
            best_cursor = next_cursor
            best_payload = payload
            low = candidate_end + 1
        else:
            high = candidate_end - 1
    if best_payload is None or best_end <= content_offset:
        raise ValueError("max_tokens is too small for transcript window metadata.")
    message = _window_message(
        anchor,
        content=anchor.content_text[content_offset:best_end],
        content_start=content_offset,
        content_end=best_end,
        content_complete=content_offset == 0 and best_end == len(anchor.content_text),
    )
    return TranscriptWindow(
        session_id=session_id,
        vault_name=vault_name,
        anchor_sequence_index=anchor.sequence_index,
        history_revision=history_revision,
        requested_before=before,
        requested_after=after,
        max_tokens=max_tokens,
        estimated_tokens=_payload_tokens(best_payload),
        compacted_through_sequence_index=compacted_through_sequence_index,
        messages=(message,),
        truncated_before=truncated_before,
        truncated_after=truncated_after,
        next_cursor=best_cursor,
    )


def _window_message(
    message: StoredChatMessage,
    *,
    content: str | None = None,
    content_start: int = 0,
    content_end: int | None = None,
    content_complete: bool = True,
) -> TranscriptWindowMessage:
    return TranscriptWindowMessage(
        sequence_index=message.sequence_index,
        role=message.role,
        message_type=message.message_type,
        created_at=message.created_at,
        content=message.content_text if content is None else content,
        content_start=content_start,
        content_end=(len(message.content_text) if content_end is None else content_end),
        content_complete=content_complete,
    )


def _window_payload(
    *,
    session_id: str,
    vault_name: str,
    anchor_sequence_index: int,
    history_revision: int,
    before: int,
    after: int,
    max_tokens: int,
    compacted_through_sequence_index: int | None,
    messages: list[TranscriptWindowMessage],
    truncated_before: bool,
    truncated_after: bool,
    next_cursor: str | None,
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "vault_name": vault_name,
        "anchor_sequence_index": anchor_sequence_index,
        "history_revision": history_revision,
        "requested_before": before,
        "requested_after": after,
        "max_tokens": max_tokens,
        "compacted_through_sequence_index": compacted_through_sequence_index,
        "messages": [message.to_dict() for message in messages],
        "truncated_before": truncated_before,
        "truncated_after": truncated_after,
        "next_cursor": next_cursor,
        "historical_content_is_untrusted": True,
    }


def _payload_tokens(payload: dict[str, Any]) -> int:
    return (
        estimate_token_count(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )
        + _WINDOW_OUTPUT_TOKEN_RESERVE
    )


def _encode_window_cursor(
    *,
    vault_name: str,
    session_id: str,
    sequence_index: int,
    history_revision: int,
    before: int,
    after: int,
    max_tokens: int,
    content_offset: int,
) -> str:
    payload = json.dumps(
        {
            "v": _WINDOW_CURSOR_VERSION,
            "vault": vault_name,
            "session": session_id,
            "sequence": sequence_index,
            "revision": history_revision,
            "before": before,
            "after": after,
            "max_tokens": max_tokens,
            "offset": content_offset,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    signature = hmac.new(_WINDOW_CURSOR_KEY, payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(payload + signature).decode("ascii").rstrip("=")


def _decode_window_cursor(
    value: str,
    *,
    vault_name: str,
    session_id: str,
    sequence_index: int,
    history_revision: int,
    before: int,
    after: int,
    max_tokens: int,
) -> int:
    try:
        padded = value + "=" * (-len(value) % 4)
        encoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload, signature = encoded[:-32], encoded[-32:]
        expected = hmac.new(_WINDOW_CURSOR_KEY, payload, hashlib.sha256).digest()
        if len(signature) != 32 or not hmac.compare_digest(signature, expected):
            raise ValueError
        claims = json.loads(payload.decode("utf-8"))
    except Exception as exc:
        raise ValueError("Transcript continuation cursor is invalid.") from exc
    expected_claims = {
        "v": _WINDOW_CURSOR_VERSION,
        "vault": vault_name,
        "session": session_id,
        "sequence": sequence_index,
        "revision": history_revision,
        "before": before,
        "after": after,
        "max_tokens": max_tokens,
    }
    if not isinstance(claims, dict) or any(
        claims.get(key) != expected_value
        for key, expected_value in expected_claims.items()
    ):
        raise ValueError("Transcript continuation cursor is stale or mismatched.")
    offset = claims.get("offset")
    if not isinstance(offset, int) or offset <= 0:
        raise ValueError("Transcript continuation cursor is invalid.")
    return offset
