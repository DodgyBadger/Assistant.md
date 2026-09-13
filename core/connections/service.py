"""Authorization-aware built-in connection configuration service."""

from __future__ import annotations

import re
import sqlite3
from uuid import uuid4

from core.access_store import write_transaction
from core.identity import ExecutionAuthority, require_current_execution_authority
from core.secrets.crypto import SecretIntegrityError

from .models import (
    GmailPreferences,
    GoogleConnection,
    GoogleConnectionCreate,
    GoogleConnectionUpdate,
)
from .schema import connect_connections, ensure_connections_schema

_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


class BuiltInConnectionService:
    """Manage non-secret built-in connection metadata by principal."""

    def __init__(
        self,
        *,
        system_root: str,
        initialize_schema: bool = True,
        available: bool = True,
    ) -> None:
        self._system_root = system_root
        self._available = available
        if available and initialize_schema:
            ensure_connections_schema(system_root)

    def _require_available(self) -> None:
        if not self._available:
            raise SecretIntegrityError(
                "Google connections are unavailable while encrypted secrets are locked."
            )

    def list_google_connections(self) -> list[GoogleConnection]:
        return self.list_google_connections_for_authority(
            require_current_execution_authority()
        )

    def list_google_connections_for_authority(
        self, authority: ExecutionAuthority
    ) -> list[GoogleConnection]:
        self._require_available()
        conn = connect_connections(self._system_root)
        try:
            rows = conn.execute(
                """
                SELECT * FROM google_connections
                WHERE owner_principal_id = ?
                ORDER BY is_default DESC, lower(display_name), connection_id
                """,
                (authority.principal_id,),
            ).fetchall()
        finally:
            conn.close()
        return [_row_to_google_connection(row) for row in rows]

    def get_google_connection(self) -> GoogleConnection | None:
        """Compatibility accessor returning the current principal's default."""
        return self.get_google_connection_for_authority(
            require_current_execution_authority()
        )

    def get_google_connection_for_authority(
        self,
        authority: ExecutionAuthority,
        connection_id: str | None = None,
    ) -> GoogleConnection | None:
        self._require_available()
        conn = connect_connections(self._system_root)
        try:
            if connection_id is None:
                row = conn.execute(
                    """
                    SELECT * FROM google_connections
                    WHERE owner_principal_id = ? AND is_default = 1
                    """,
                    (authority.principal_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT * FROM google_connections
                    WHERE owner_principal_id = ? AND connection_id = ?
                    """,
                    (authority.principal_id, _required_id(connection_id)),
                ).fetchone()
        finally:
            conn.close()
        return _row_to_google_connection(row) if row is not None else None

    def get_google_connection_by_slug_for_authority(
        self, authority: ExecutionAuthority, slug: str
    ) -> GoogleConnection | None:
        self._require_available()
        conn = connect_connections(self._system_root)
        try:
            row = conn.execute(
                """
                SELECT * FROM google_connections
                WHERE owner_principal_id = ? AND slug = ?
                """,
                (authority.principal_id, str(slug or "").strip()),
            ).fetchone()
        finally:
            conn.close()
        return _row_to_google_connection(row) if row is not None else None

    def create_google_connection(
        self, request: GoogleConnectionCreate
    ) -> GoogleConnection:
        return self.create_google_connection_for_authority(
            require_current_execution_authority(), request
        )

    def create_google_connection_for_authority(
        self, authority: ExecutionAuthority, request: GoogleConnectionCreate
    ) -> GoogleConnection:
        self._require_available()
        connection_id = str(uuid4())
        try:
            with write_transaction(self._system_root) as conn:
                count = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM google_connections WHERE owner_principal_id = ?",
                        (authority.principal_id,),
                    ).fetchone()[0]
                )
                is_default = request.is_default or count == 0
                if is_default:
                    conn.execute(
                        "UPDATE google_connections SET is_default = 0 WHERE owner_principal_id = ?",
                        (authority.principal_id,),
                    )
                slug = _unique_slug(conn, authority.principal_id, request.display_name)
                conn.execute(
                    "INSERT INTO google_connection_slugs (owner_principal_id, connection_id, slug) VALUES (?, ?, ?)",
                    (authority.principal_id, connection_id, slug),
                )
                conn.execute(
                    """
                    INSERT INTO google_connections (
                        owner_principal_id, connection_id, slug, display_name,
                        client_id, is_default, gmail_search_default_results,
                        gmail_search_max_results, gmail_message_max_characters,
                        gmail_thread_max_messages, gmail_attachment_download_enabled,
                        gmail_attachment_max_mb, gmail_draft_creation_enabled,
                        gmail_draft_max_characters
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        authority.principal_id,
                        connection_id,
                        slug,
                        request.display_name,
                        request.client_id,
                        int(is_default),
                        request.gmail.search_default_results,
                        request.gmail.search_max_results,
                        request.gmail.message_max_characters,
                        request.gmail.thread_max_messages,
                        int(request.gmail.attachment_download_enabled),
                        request.gmail.attachment_max_mb,
                        int(request.gmail.draft_creation_enabled),
                        request.gmail.draft_max_characters,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Google connection name must be unique.") from exc
        return self._require_for_authority(authority, connection_id)

    def update_google_connection_on_connection(
        self,
        conn: sqlite3.Connection,
        authority: ExecutionAuthority,
        existing: GoogleConnection,
        request: GoogleConnectionUpdate,
    ) -> None:
        """Update metadata through a caller-owned access transaction."""
        self._require_available()
        resolved_name = request.display_name or existing.display_name
        resolved_default = (
            existing.is_default if request.is_default is None else request.is_default
        )
        if existing.is_default and not resolved_default:
            raise ValueError("Select another default Google connection first.")
        if resolved_default and not existing.is_default:
            conn.execute(
                "UPDATE google_connections SET is_default = 0 WHERE owner_principal_id = ?",
                (authority.principal_id,),
            )
        cursor = conn.execute(
            """
                    UPDATE google_connections SET display_name = ?, client_id = ?,
                        is_default = ?, gmail_search_default_results = ?,
                        gmail_search_max_results = ?, gmail_message_max_characters = ?,
                        gmail_thread_max_messages = ?, gmail_attachment_download_enabled = ?,
                        gmail_attachment_max_mb = ?, gmail_draft_creation_enabled = ?,
                        gmail_draft_max_characters = ?, config_version = config_version + 1,
                        oauth_generation = oauth_generation +
                            CASE WHEN client_id <> ? THEN 1 ELSE 0 END,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE owner_principal_id = ? AND connection_id = ?
                    """,
            (
                resolved_name,
                request.client_id,
                int(resolved_default),
                request.gmail.search_default_results,
                request.gmail.search_max_results,
                request.gmail.message_max_characters,
                request.gmail.thread_max_messages,
                int(request.gmail.attachment_download_enabled),
                request.gmail.attachment_max_mb,
                int(request.gmail.draft_creation_enabled),
                request.gmail.draft_max_characters,
                request.client_id,
                authority.principal_id,
                existing.connection_id,
            ),
        )
        if cursor.rowcount == 0:
            raise LookupError("Google connection not found.")

    def require_google_connection_on_connection(
        self,
        conn: sqlite3.Connection,
        authority: ExecutionAuthority,
        connection_id: str,
    ) -> GoogleConnection:
        """Resolve authoritative metadata within the caller's transaction."""
        self._require_available()
        row = conn.execute(
            "SELECT * FROM google_connections WHERE owner_principal_id=? AND connection_id=?",
            (authority.principal_id, _required_id(connection_id)),
        ).fetchone()
        if row is None:
            raise LookupError("Google connection not found.")
        return _row_to_google_connection(row)

    def _require_for_authority(
        self, authority: ExecutionAuthority, connection_id: str
    ) -> GoogleConnection:
        result = self.get_google_connection_for_authority(authority, connection_id)
        if result is None:
            raise LookupError("Google connection not found.")
        return result


def _required_id(value: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError("Google connection ID is required.")
    return clean


def _unique_slug(conn: sqlite3.Connection, principal_id: str, display_name: str) -> str:
    base = _SLUG_PATTERN.sub("-", display_name.lower()).strip("-") or "google"
    slug = base
    suffix = 2
    while conn.execute(
        "SELECT 1 FROM google_connection_slugs WHERE owner_principal_id = ? AND slug = ?",
        (principal_id, slug),
    ).fetchone():
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


def _row_to_google_connection(row: sqlite3.Row) -> GoogleConnection:
    return GoogleConnection(
        connection_id=str(row["connection_id"]),
        slug=str(row["slug"]),
        display_name=str(row["display_name"]),
        client_id=str(row["client_id"]),
        is_default=bool(row["is_default"]),
        gmail=GmailPreferences(
            search_default_results=int(row["gmail_search_default_results"]),
            search_max_results=int(row["gmail_search_max_results"]),
            message_max_characters=int(row["gmail_message_max_characters"]),
            thread_max_messages=int(row["gmail_thread_max_messages"]),
            attachment_download_enabled=bool(row["gmail_attachment_download_enabled"]),
            attachment_max_mb=int(row["gmail_attachment_max_mb"]),
            draft_creation_enabled=bool(row["gmail_draft_creation_enabled"]),
            draft_max_characters=int(row["gmail_draft_max_characters"]),
        ),
        config_version=int(row["config_version"]),
        oauth_generation=int(row["oauth_generation"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )
