"""Typed built-in connection configuration models."""

from __future__ import annotations

from dataclasses import dataclass, field

GMAIL_SEARCH_DEFAULT_RESULTS = 20
GMAIL_SEARCH_MAX_RESULTS = 100
GMAIL_SEARCH_RESULTS_CEILING = 500
GMAIL_MESSAGE_MAX_CHARACTERS = 50_000
GMAIL_MESSAGE_CHARACTERS_CEILING = 250_000
GMAIL_THREAD_MAX_MESSAGES = 25
GMAIL_THREAD_MESSAGES_CEILING = 100
GMAIL_ATTACHMENT_MAX_MB = 25
GMAIL_ATTACHMENT_MAX_MB_CEILING = 100
GMAIL_DRAFT_MAX_CHARACTERS = 50_000
GMAIL_DRAFT_CHARACTERS_CEILING = 250_000


@dataclass(frozen=True)
class GmailPreferences:
    """Principal-owned Gmail capability limits."""

    search_default_results: int = GMAIL_SEARCH_DEFAULT_RESULTS
    search_max_results: int = GMAIL_SEARCH_MAX_RESULTS
    message_max_characters: int = GMAIL_MESSAGE_MAX_CHARACTERS
    thread_max_messages: int = GMAIL_THREAD_MAX_MESSAGES
    attachment_download_enabled: bool = False
    attachment_max_mb: int = GMAIL_ATTACHMENT_MAX_MB
    draft_creation_enabled: bool = False
    draft_max_characters: int = GMAIL_DRAFT_MAX_CHARACTERS

    def __post_init__(self) -> None:
        if not 1 <= self.search_default_results <= GMAIL_SEARCH_RESULTS_CEILING:
            raise ValueError("Gmail default search results must be between 1 and 500.")
        if not 1 <= self.search_max_results <= GMAIL_SEARCH_RESULTS_CEILING:
            raise ValueError("Gmail maximum search results must be between 1 and 500.")
        if self.search_default_results > self.search_max_results:
            raise ValueError(
                "Gmail default search results cannot exceed the configured maximum."
            )
        if not 1 <= self.message_max_characters <= GMAIL_MESSAGE_CHARACTERS_CEILING:
            raise ValueError("Gmail message characters must be between 1 and 250000.")
        if not 1 <= self.thread_max_messages <= GMAIL_THREAD_MESSAGES_CEILING:
            raise ValueError("Gmail thread messages must be between 1 and 100.")
        if not isinstance(self.attachment_download_enabled, bool):
            raise ValueError("Gmail attachment download enabled must be boolean.")
        if (
            not isinstance(self.attachment_max_mb, int)
            or isinstance(self.attachment_max_mb, bool)
            or not 1 <= self.attachment_max_mb <= GMAIL_ATTACHMENT_MAX_MB_CEILING
        ):
            raise ValueError("Gmail attachment limit must be between 1 and 100 MB.")
        if not isinstance(self.draft_creation_enabled, bool):
            raise ValueError("Gmail draft creation enabled must be boolean.")
        if (
            not isinstance(self.draft_max_characters, int)
            or isinstance(self.draft_max_characters, bool)
            or not 1 <= self.draft_max_characters <= GMAIL_DRAFT_CHARACTERS_CEILING
        ):
            raise ValueError("Gmail draft characters must be between 1 and 250000.")


@dataclass(frozen=True)
class GoogleConnectionCreate:
    """User-controlled fields accepted when creating a Google connection."""

    display_name: str
    client_id: str
    is_default: bool = False
    gmail: GmailPreferences = field(default_factory=GmailPreferences)

    def __post_init__(self) -> None:
        clean_name = str(self.display_name or "").strip()
        if not clean_name:
            raise ValueError("Google connection display name cannot be empty.")
        if len(clean_name) > 120:
            raise ValueError("Google connection display name is too long.")
        object.__setattr__(self, "display_name", clean_name)
        clean_client_id = str(self.client_id or "").strip()
        if not clean_client_id:
            raise ValueError("Google OAuth client ID cannot be empty.")
        if len(clean_client_id) > 2048:
            raise ValueError("Google OAuth client ID is too long.")
        object.__setattr__(self, "client_id", clean_client_id)


@dataclass(frozen=True)
class GoogleConnectionUpdate:
    """Mutable Google fields; immutable identity and slug are absent."""

    client_id: str
    display_name: str | None = None
    is_default: bool | None = None
    gmail: GmailPreferences = field(default_factory=GmailPreferences)

    def __post_init__(self) -> None:
        if self.display_name is not None:
            clean_name = str(self.display_name or "").strip()
            if not clean_name:
                raise ValueError("Google connection display name cannot be empty.")
            if len(clean_name) > 120:
                raise ValueError("Google connection display name is too long.")
            object.__setattr__(self, "display_name", clean_name)
        clean_client_id = str(self.client_id or "").strip()
        if not clean_client_id:
            raise ValueError("Google OAuth client ID cannot be empty.")
        if len(clean_client_id) > 2048:
            raise ValueError("Google OAuth client ID is too long.")
        object.__setattr__(self, "client_id", clean_client_id)


@dataclass(frozen=True)
class GoogleConnection:
    """Sanitized principal-owned Google connection metadata."""

    connection_id: str
    slug: str
    display_name: str
    client_id: str
    is_default: bool
    gmail: GmailPreferences
    config_version: int
    oauth_generation: int
    created_at: str
    updated_at: str
