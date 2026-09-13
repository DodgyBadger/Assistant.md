"""Principal-authorized Gmail resources shared by tools and future ingestion."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from core.connections import (
    BuiltInConnectionService,
    GmailPreferences,
    GoogleConnection,
)
from core.identity import ExecutionAuthority
from core.logger import UnifiedLogger

from .connection import GoogleCapability, GoogleConnectionService
from .gmail import (
    GmailAPIClient,
    GmailAttachment,
    GmailDraft,
    GmailError,
    GmailMessage,
    GmailRequestError,
    GmailSearchResult,
    GmailThread,
)
from .oauth import GoogleOAuthCoordinator

logger = UnifiedLogger(tag="gmail-resource")


class GmailConfigurationError(ValueError):
    """Raised when Gmail connection state or policy prevents an operation."""


@dataclass(frozen=True)
class GmailAttachmentDownload:
    """Authorized attachment bytes plus their provider-supplied descriptor."""

    attachment: GmailAttachment
    content: bytes


class GmailResourceService:
    """Authorize and apply principal preferences before Gmail resource access."""

    def __init__(
        self,
        *,
        connections: BuiltInConnectionService,
        google: GoogleConnectionService,
        oauth: GoogleOAuthCoordinator,
        client_factory: Callable[[ExecutionAuthority], GmailAPIClient] | None = None,
    ) -> None:
        self._connections = connections
        self._google = google
        self._oauth = oauth
        self._client_factory = client_factory

    def status(
        self, authority: ExecutionAuthority, connection: str | None = None
    ) -> dict[str, object]:
        """Return sanitized Gmail account and capability readiness."""
        selected = self._resolve_connection(authority, connection)
        status = self._google.status(authority, selected.connection_id)
        availability = self._google.capability_availability(
            authority, GoogleCapability.GMAIL_READ, selected.connection_id
        )
        compose = self._google.capability_availability(
            authority, GoogleCapability.GMAIL_COMPOSE, selected.connection_id
        )
        return {
            "provider": "google",
            "connection": selected.slug,
            "connection_name": selected.display_name,
            "is_default": selected.is_default,
            "capability": GoogleCapability.GMAIL_READ.value,
            "available": availability.available,
            "connection_state": availability.connection_state,
            "account_email": status.account_email,
            "missing_scopes": list(availability.missing_scopes),
            "attachment_download_enabled": selected.gmail.attachment_download_enabled,
            "attachment_max_mb": selected.gmail.attachment_max_mb,
            "draft_creation_enabled": selected.gmail.draft_creation_enabled,
            "draft_creation_available": (
                selected.gmail.draft_creation_enabled and compose.available
            ),
            "draft_missing_scopes": list(compose.missing_scopes),
        }

    def list_connections(
        self, authority: ExecutionAuthority
    ) -> list[dict[str, object]]:
        """List sanitized Google account selectors and Gmail readiness."""
        return [
            self.status(authority, connection.slug)
            for connection in self._connections.list_google_connections_for_authority(
                authority
            )
        ]

    async def search(
        self,
        authority: ExecutionAuthority,
        *,
        query: str,
        max_results: int | None = None,
        connection: str | None = None,
    ) -> tuple[GmailSearchResult, bool]:
        """Search under principal configuration and report request capping."""
        selected, preferences = self._preferences(authority, connection)
        requested = max_results or preferences.search_default_results
        effective = min(requested, preferences.search_max_results)
        capped = requested > effective
        logger.info(
            "Gmail search started",
            data={
                "event": "gmail_search_started",
                "principal_id": authority.principal_id,
                "connection_id": selected.connection_id,
                "max_results": effective,
                "request_capped": capped,
            },
        )
        try:
            result = await self._client(authority, selected.connection_id).search(
                query=query, max_results=effective
            )
        except Exception as exc:
            _log_failure("search", authority, exc)
            raise
        logger.info(
            "Gmail search completed",
            data={
                "event": "gmail_search_completed",
                "principal_id": authority.principal_id,
                "connection_id": selected.connection_id,
                "result_count": result.result_count,
                "partial": result.partial,
            },
        )
        return result, capped

    async def get_message(
        self,
        authority: ExecutionAuthority,
        message_id: str,
        *,
        connection: str | None = None,
    ) -> GmailMessage:
        selected, preferences = self._preferences(authority, connection)
        try:
            result = await self._client(authority, selected.connection_id).get_message(
                message_id, max_characters=preferences.message_max_characters
            )
        except Exception as exc:
            _log_failure("get_message", authority, exc)
            raise
        logger.info(
            "Gmail message read completed",
            data={
                "event": "gmail_message_read_completed",
                "principal_id": authority.principal_id,
                "connection_id": selected.connection_id,
                "text_characters": len(result.text),
                "text_truncated": result.text_truncated,
                "attachment_count": len(result.attachments),
                "attachments_truncated": result.attachments_truncated,
            },
        )
        return result

    async def get_thread(
        self,
        authority: ExecutionAuthority,
        thread_id: str,
        *,
        connection: str | None = None,
    ) -> GmailThread:
        selected, preferences = self._preferences(authority, connection)
        try:
            result = await self._client(authority, selected.connection_id).get_thread(
                thread_id,
                max_messages=preferences.thread_max_messages,
                max_characters=preferences.message_max_characters,
            )
        except Exception as exc:
            _log_failure("get_thread", authority, exc)
            raise
        logger.info(
            "Gmail thread read completed",
            data={
                "event": "gmail_thread_read_completed",
                "principal_id": authority.principal_id,
                "connection_id": selected.connection_id,
                "message_count": len(result.messages),
                "omitted_message_count": result.omitted_message_count,
                "truncated": result.truncated,
            },
        )
        return result

    async def download_attachment(
        self,
        authority: ExecutionAuthority,
        message_id: str,
        attachment_id: str,
        *,
        connection: str | None = None,
    ) -> GmailAttachmentDownload:
        """Authorize and download one bounded PDF attachment."""
        selected, preferences = self._preferences(authority, connection)
        if not preferences.attachment_download_enabled:
            logger.info(
                "Gmail attachment download denied by connection policy",
                data={
                    "event": "gmail_attachment_download_disabled",
                    "principal_id": authority.principal_id,
                    "connection_id": selected.connection_id,
                },
            )
            raise GmailConfigurationError(
                "Gmail attachment downloads are disabled for this connection."
            )
        limit = preferences.attachment_max_mb * 1024 * 1024
        logger.info(
            "Gmail attachment download started",
            data={
                "event": "gmail_attachment_download_started",
                "principal_id": authority.principal_id,
                "connection_id": selected.connection_id,
                "max_bytes": limit,
            },
        )
        try:
            client = self._client(authority, selected.connection_id)
            content = await client.download_attachment(
                message_id, attachment_id, max_bytes=limit
            )
            if not content.startswith(b"%PDF-"):
                raise GmailError(
                    "Gmail attachment content is not a valid PDF.",
                    category="provider_response",
                )
            attachment = GmailAttachment(
                attachment_id=attachment_id,
                filename="",
                media_type="application/pdf",
                declared_size=len(content),
                message_id=message_id,
            )
        except Exception as exc:
            _log_failure(
                "download_attachment",
                authority,
                exc,
                connection_id=selected.connection_id,
            )
            raise
        logger.info(
            "Gmail attachment download completed",
            data={
                "event": "gmail_attachment_download_completed",
                "principal_id": authority.principal_id,
                "connection_id": selected.connection_id,
                "content_bytes": len(content),
                "declared_bytes": attachment.declared_size,
            },
        )
        return GmailAttachmentDownload(attachment=attachment, content=content)

    async def create_draft(
        self,
        authority: ExecutionAuthority,
        *,
        subject: str,
        body: str,
        connection: str | None = None,
    ) -> GmailDraft:
        """Create one bounded plain-text draft under explicit connection policy."""
        selected = self._resolve_connection(authority, connection)
        preferences = selected.gmail
        if not preferences.draft_creation_enabled:
            raise GmailConfigurationError(
                "Gmail draft creation is disabled for this connection."
            )
        availability = self._google.capability_availability(
            authority, GoogleCapability.GMAIL_COMPOSE, selected.connection_id
        )
        if not availability.available:
            raise GmailConfigurationError(
                "Gmail draft creation requires adding Gmail compose permission."
            )
        if len(body) > preferences.draft_max_characters:
            raise GmailRequestError(
                "Gmail draft body exceeds this connection's character limit."
            )
        if not body:
            raise GmailRequestError("Gmail draft body cannot be empty.")
        logger.info(
            "Gmail draft creation started",
            data={
                "event": "gmail_draft_creation_started",
                "principal_id": authority.principal_id,
                "connection_id": selected.connection_id,
                "body_characters": len(body),
            },
        )
        try:
            draft = await self._client(authority, selected.connection_id).create_draft(
                subject=subject, body=body
            )
        except Exception as exc:
            _log_failure(
                "create_draft", authority, exc, connection_id=selected.connection_id
            )
            raise
        logger.info(
            "Gmail draft creation completed",
            data={
                "event": "gmail_draft_creation_completed",
                "principal_id": authority.principal_id,
                "connection_id": selected.connection_id,
                "body_characters": len(body),
            },
        )
        return draft

    def _preferences(
        self, authority: ExecutionAuthority, selector: str | None
    ) -> tuple[GoogleConnection, GmailPreferences]:
        connection = self._resolve_connection(authority, selector)
        availability = self._google.capability_availability(
            authority, GoogleCapability.GMAIL_READ, connection.connection_id
        )
        if not availability.available:
            raise GmailConfigurationError(
                "Gmail connection is unavailable. Reconnect Google with Gmail read access."
            )
        return connection, connection.gmail

    def _resolve_connection(
        self, authority: ExecutionAuthority, selector: str | None
    ) -> GoogleConnection:
        if selector is None or not str(selector).strip():
            connection = self._connections.get_google_connection_for_authority(
                authority
            )
        else:
            connection = self._connections.get_google_connection_by_slug_for_authority(
                authority, str(selector).strip()
            )
        if connection is None:
            available = ", ".join(
                item.slug
                for item in self._connections.list_google_connections_for_authority(
                    authority
                )
            )
            suffix = f" Available connections: {available}." if available else ""
            raise GmailConfigurationError(f"Google connection was not found.{suffix}")
        return connection

    def _client(
        self, authority: ExecutionAuthority, connection_id: str
    ) -> GmailAPIClient:
        if self._client_factory is not None:
            return self._client_factory(authority)
        return GmailAPIClient(
            access_token_provider=lambda: self._oauth.access_token(
                authority, connection_id
            )
        )


def _log_failure(
    operation: str,
    authority: ExecutionAuthority,
    exc: Exception,
    *,
    connection_id: str | None = None,
) -> None:
    logger.warning(
        "Gmail resource operation failed",
        data={
            "event": "gmail_resource_failed",
            "principal_id": authority.principal_id,
            "operation": operation,
            "connection_id": connection_id,
            "error_type": type(exc).__name__,
            "category": getattr(exc, "category", "unknown"),
            "retryable": bool(getattr(exc, "retryable", False)),
        },
    )
