"""Bounded Gmail API resources independent from LLM formatting."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.policy import SMTP
from html.parser import HTMLParser
from typing import Any

import httpx

from core.oauth import OAuthHTTPClientFactory
from core.settings import get_default_api_timeout

GMAIL_API_ROOT = "https://gmail.googleapis.com/gmail/v1/users/me"
GMAIL_REQUEST_ATTEMPTS = 3
GMAIL_ATTACHMENT_LIMIT = 100
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_RETRYABLE_403_REASONS = frozenset(
    {"rateLimitExceeded", "userRateLimitExceeded", "backendError"}
)

AccessTokenProvider = Callable[[], Awaitable[str]]
Sleep = Callable[[float], Awaitable[None]]


class GmailError(RuntimeError):
    """Stable sanitized Gmail resource failure."""

    def __init__(self, message: str, *, category: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.category = category
        self.retryable = retryable


class GmailRequestError(ValueError):
    """Raised when a Gmail operation receives invalid user-controlled input."""


@dataclass(frozen=True)
class GmailAttachment:
    attachment_id: str
    filename: str
    media_type: str
    declared_size: int | None
    message_id: str


@dataclass(frozen=True)
class GmailMessage:
    message_id: str
    thread_id: str
    history_id: str | None
    internal_date: str | None
    subject: str
    sender: str
    recipients: tuple[str, ...]
    date: str | None
    snippet: str
    labels: tuple[str, ...]
    text: str
    text_truncated: bool
    attachments: tuple[GmailAttachment, ...]
    attachments_truncated: bool


@dataclass(frozen=True)
class GmailSearchResult:
    query: str
    messages: tuple[GmailMessage, ...]
    result_count: int
    result_size_estimate: int
    next_page_token: str | None
    requested_max_results: int
    partial: bool


@dataclass(frozen=True)
class GmailThread:
    thread_id: str
    history_id: str | None
    messages: tuple[GmailMessage, ...]
    omitted_message_count: int
    truncated: bool


@dataclass(frozen=True)
class GmailDraft:
    draft_id: str
    message_id: str
    thread_id: str


class GmailAPIClient:
    """Issue authenticated bounded Gmail requests and normalize provider payloads."""

    def __init__(
        self,
        *,
        access_token_provider: AccessTokenProvider,
        http_client_factory: OAuthHTTPClientFactory | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._access_token_provider = access_token_provider
        self._http_client_factory = http_client_factory or _gmail_http_client
        self._sleep = sleep

    async def search(self, *, query: str, max_results: int) -> GmailSearchResult:
        """Search message handles and load compact metadata without message bodies."""
        clean_query = str(query or "").strip()
        if not clean_query:
            raise GmailRequestError("Gmail search query cannot be empty.")
        if not 1 <= max_results <= 500:
            raise GmailRequestError("Gmail search results must be between 1 and 500.")
        payload = await self._request(
            "GET",
            "/messages",
            params={"q": clean_query, "maxResults": str(max_results)},
        )
        raw_items = payload.get("messages")
        items = raw_items if isinstance(raw_items, list) else []
        messages: list[GmailMessage] = []
        for item in items[:max_results]:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            metadata = await self._request(
                "GET",
                f"/messages/{item['id']}",
                params={"format": "metadata"},
            )
            messages.append(_normalize_message(metadata, max_characters=0))
        next_page_token = _optional_string(payload.get("nextPageToken"))
        return GmailSearchResult(
            query=clean_query,
            messages=tuple(messages),
            result_count=len(messages),
            result_size_estimate=_integer(payload.get("resultSizeEstimate")) or 0,
            next_page_token=next_page_token,
            requested_max_results=max_results,
            partial=next_page_token is not None,
        )

    async def get_message(
        self, message_id: str, *, max_characters: int
    ) -> GmailMessage:
        """Load one full message with bounded normalized text."""
        clean_id = _resource_id(message_id, "message")
        if not 1 <= max_characters <= 250_000:
            raise GmailRequestError(
                "Gmail message characters must be between 1 and 250000."
            )
        payload = await self._request(
            "GET", f"/messages/{clean_id}", params={"format": "full"}
        )
        return _normalize_message(payload, max_characters=max_characters)

    async def get_thread(
        self,
        thread_id: str,
        *,
        max_messages: int,
        max_characters: int,
    ) -> GmailThread:
        """Load a thread with explicit message-count truncation."""
        clean_id = _resource_id(thread_id, "thread")
        if not 1 <= max_messages <= 100:
            raise GmailRequestError("Gmail thread messages must be between 1 and 100.")
        payload = await self._request(
            "GET", f"/threads/{clean_id}", params={"format": "full"}
        )
        raw_messages = payload.get("messages")
        items = raw_messages if isinstance(raw_messages, list) else []
        normalized = tuple(
            _normalize_message(item, max_characters=max_characters)
            for item in items[:max_messages]
            if isinstance(item, dict)
        )
        omitted = max(0, len(items) - len(normalized))
        return GmailThread(
            thread_id=str(payload.get("id") or clean_id),
            history_id=_optional_string(payload.get("historyId")),
            messages=normalized,
            omitted_message_count=omitted,
            truncated=omitted > 0,
        )

    async def download_attachment(
        self, message_id: str, attachment_id: str, *, max_bytes: int
    ) -> bytes:
        """Download and decode one attachment within a strict byte limit."""
        clean_message_id = _resource_id(message_id, "message")
        clean_attachment_id = _resource_id(attachment_id, "attachment")
        if max_bytes <= 0:
            raise GmailError(
                "Gmail attachment downloads are disabled.",
                category="attachment_disabled",
            )
        encoded_limit = ((max_bytes + 2) // 3) * 4 + 4096
        payload = await self._bounded_request(
            "GET",
            f"/messages/{clean_message_id}/attachments/{clean_attachment_id}",
            params={},
            max_response_bytes=encoded_limit,
            too_large_message="Gmail attachment response exceeds the configured size limit.",
            too_large_category="attachment_too_large",
        )
        declared_size = _integer(payload.get("size"))
        if declared_size is not None and declared_size > max_bytes:
            raise GmailError(
                "Gmail attachment exceeds the configured size limit.",
                category="attachment_too_large",
            )
        encoded = payload.get("data")
        if not isinstance(encoded, str) or not encoded:
            raise GmailError(
                "Gmail returned invalid attachment data.",
                category="provider_response",
            )
        try:
            content = base64.b64decode(
                encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
            )
        except (ValueError, binascii.Error) as exc:
            raise GmailError(
                "Gmail returned invalid attachment data.",
                category="provider_response",
            ) from exc
        if len(content) > max_bytes:
            raise GmailError(
                "Gmail attachment exceeds the configured size limit.",
                category="attachment_too_large",
            )
        return content

    async def create_draft(
        self,
        *,
        subject: str,
        body: str,
    ) -> GmailDraft:
        """Create one plain-text draft without retrying an ambiguous mutation."""
        clean_subject = _validated_header(subject, "subject", required=True)
        if not isinstance(body, str) or not body:
            raise GmailRequestError("Gmail draft body cannot be empty.")
        message = EmailMessage()
        message["Subject"] = clean_subject
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes(policy=SMTP)).decode("ascii")
        try:
            payload = await self._bounded_request(
                "POST",
                "/drafts",
                params={},
                json_body={"message": {"raw": raw.rstrip("=")}},
                attempts=1,
                max_response_bytes=64 * 1024,
                too_large_message="Gmail returned an oversized draft response.",
                too_large_category="provider_response",
            )
            draft_id = _resource_id(str(payload.get("id") or ""), "draft")
            raw_message = payload.get("message")
            draft_message = raw_message if isinstance(raw_message, dict) else {}
            message_id = _resource_id(str(draft_message.get("id") or ""), "message")
            thread_id = _resource_id(str(draft_message.get("threadId") or ""), "thread")
        except (GmailError, GmailRequestError) as exc:
            definite_categories = {
                "authentication",
                "permission",
                "validation",
                "not_found",
            }
            if (
                isinstance(exc, GmailRequestError)
                or exc.category not in definite_categories
            ):
                raise GmailError(
                    "Gmail draft creation outcome is unknown. Inspect Gmail drafts before retrying.",
                    category="mutation_outcome_unknown",
                ) from exc
            raise
        return GmailDraft(
            draft_id=draft_id,
            message_id=message_id,
            thread_id=thread_id,
        )

    async def _bounded_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str],
        json_body: dict[str, Any] | None = None,
        attempts: int = GMAIL_REQUEST_ATTEMPTS,
        max_response_bytes: int,
        too_large_message: str,
        too_large_category: str,
    ) -> dict[str, Any]:
        """Receive a JSON response without allowing unbounded buffering."""
        last_error: Exception | None = None
        for attempt in range(attempts):
            token = await self._access_token_provider()
            try:
                async with self._http_client_factory() as client:
                    async with client.stream(
                        method,
                        f"{GMAIL_API_ROOT}{path}",
                        params=params,
                        json=json_body,
                        headers={"Authorization": f"Bearer {token}"},
                    ) as response:
                        declared = response.headers.get("content-length")
                        if declared:
                            try:
                                declared_length = int(declared)
                            except ValueError:
                                declared_length = None
                            if (
                                declared_length is not None
                                and declared_length > max_response_bytes
                            ):
                                raise GmailError(
                                    too_large_message,
                                    category=too_large_category,
                                )
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(chunk) > max_response_bytes - len(body):
                                raise GmailError(
                                    too_large_message,
                                    category=too_large_category,
                                )
                            body.extend(chunk)
                        status_code = response.status_code
                        response_headers = response.headers
                        response_request = response.request
            except httpx.RequestError as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    await self._sleep(_retry_delay(attempt, None))
                    continue
                raise GmailError(
                    "Gmail could not be reached after retrying.",
                    category="network",
                    retryable=True,
                ) from exc
            if status_code < 400:
                break
            bounded_response = httpx.Response(
                status_code,
                headers=response_headers,
                content=bytes(body),
                request=response_request,
            )
            retryable = _response_retryable(bounded_response)
            if retryable and attempt + 1 < attempts:
                await self._sleep(_retry_delay(attempt, bounded_response))
                continue
            raise _gmail_response_error(bounded_response, retryable=retryable)
        else:
            raise GmailError(
                "Gmail request failed after retrying.",
                category="network",
                retryable=True,
            ) from last_error
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise GmailError(
                "Gmail returned an invalid response.", category="provider_response"
            ) from exc
        if not isinstance(payload, dict):
            raise GmailError(
                "Gmail returned an invalid response.", category="provider_response"
            )
        return payload

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str],
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(GMAIL_REQUEST_ATTEMPTS):
            token = await self._access_token_provider()
            try:
                async with self._http_client_factory() as client:
                    response = await client.request(
                        method,
                        f"{GMAIL_API_ROOT}{path}",
                        params=params,
                        headers={"Authorization": f"Bearer {token}"},
                    )
            except httpx.RequestError as exc:
                last_error = exc
                if attempt + 1 < GMAIL_REQUEST_ATTEMPTS:
                    await self._sleep(_retry_delay(attempt, None))
                    continue
                raise GmailError(
                    "Gmail could not be reached after retrying.",
                    category="network",
                    retryable=True,
                ) from exc
            retryable = _response_retryable(response)
            if retryable and attempt + 1 < GMAIL_REQUEST_ATTEMPTS:
                await self._sleep(_retry_delay(attempt, response))
                continue
            if response.is_error:
                raise _gmail_response_error(response, retryable=retryable)
            try:
                payload = response.json()
            except ValueError as exc:
                raise GmailError(
                    "Gmail returned an invalid response.", category="provider_response"
                ) from exc
            if not isinstance(payload, dict):
                raise GmailError(
                    "Gmail returned an invalid response.", category="provider_response"
                )
            return payload
        raise GmailError(
            "Gmail request failed after retrying.", category="network", retryable=True
        ) from last_error


def _normalize_message(payload: dict[str, Any], *, max_characters: int) -> GmailMessage:
    message_id = _resource_id(str(payload.get("id") or ""), "message")
    raw_root = payload.get("payload")
    root: dict[str, Any] = raw_root if isinstance(raw_root, dict) else {}
    headers = _headers(root.get("headers"))
    plain_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[GmailAttachment] = []
    _walk_parts(
        root,
        message_id=message_id,
        plain_parts=plain_parts,
        html_parts=html_parts,
        attachments=attachments,
    )
    full_text = "\n\n".join(part.strip() for part in plain_parts if part.strip())
    if not full_text and html_parts:
        parser = _PlainTextHTMLParser()
        parser.feed("\n".join(html_parts))
        full_text = parser.text
    if max_characters == 0:
        text = ""
        truncated = False
    else:
        text = full_text[:max_characters]
        truncated = len(full_text) > max_characters
    return GmailMessage(
        message_id=message_id,
        thread_id=str(payload.get("threadId") or ""),
        history_id=_optional_string(payload.get("historyId")),
        internal_date=_optional_string(payload.get("internalDate")),
        subject=_decoded_header(headers.get("subject", "")),
        sender=_decoded_header(headers.get("from", "")),
        recipients=tuple(
            _decoded_header(value)
            for key in ("to", "cc")
            if (value := headers.get(key))
        ),
        date=_optional_string(headers.get("date")),
        snippet=str(payload.get("snippet") or ""),
        labels=tuple(str(item) for item in payload.get("labelIds", []) if item),
        text=text,
        text_truncated=truncated,
        attachments=tuple(attachments[:GMAIL_ATTACHMENT_LIMIT]),
        attachments_truncated=len(attachments) > GMAIL_ATTACHMENT_LIMIT,
    )


def _walk_parts(
    part: dict[str, Any],
    *,
    message_id: str,
    plain_parts: list[str],
    html_parts: list[str],
    attachments: list[GmailAttachment],
) -> None:
    raw_body = part.get("body")
    body: dict[str, Any] = raw_body if isinstance(raw_body, dict) else {}
    media_type = str(part.get("mimeType") or "application/octet-stream").lower()
    filename = str(part.get("filename") or "")
    attachment_id = str(body.get("attachmentId") or "")
    if attachment_id or filename:
        if attachment_id:
            attachments.append(
                GmailAttachment(
                    attachment_id=attachment_id,
                    filename=filename,
                    media_type=media_type,
                    declared_size=_integer(body.get("size")),
                    message_id=message_id,
                )
            )
    elif data := body.get("data"):
        decoded = _decode_body(str(data))
        if media_type == "text/plain":
            plain_parts.append(decoded)
        elif media_type == "text/html":
            html_parts.append(decoded)
    children = part.get("parts")
    if isinstance(children, list):
        for child in children:
            if isinstance(child, dict):
                _walk_parts(
                    child,
                    message_id=message_id,
                    plain_parts=plain_parts,
                    html_parts=html_parts,
                    attachments=attachments,
                )


def _headers(value: object) -> dict[str, str]:
    result: dict[str, str] = {}
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and item.get("name") and item.get("value"):
                result[str(item["name"]).lower()] = str(item["value"])
    return result


def _validated_header(value: str, name: str, *, required: bool = False) -> str:
    clean = str(value or "").strip()
    if required and not clean:
        raise GmailRequestError(f"Gmail draft {name} cannot be empty.")
    if any(ord(character) < 32 or ord(character) == 127 for character in clean):
        raise GmailRequestError(
            f"Gmail draft {name} contains an invalid control character."
        )
    if len(clean) > 998:
        raise GmailRequestError(f"Gmail draft {name} is too long.")
    return clean


def _decode_body(value: str) -> str:
    try:
        padding = "=" * (-len(value) % 4)
        return base64.b64decode(
            value + padding,
            altchars=b"-_",
            validate=True,
        ).decode(
            "utf-8",
            errors="replace",
        )
    except (ValueError, TypeError, binascii.Error):
        return ""


def _decoded_header(value: str) -> str:
    try:
        return str(make_header(decode_header(value)))
    except (LookupError, UnicodeError):
        return value


def _response_retryable(response: httpx.Response) -> bool:
    if response.status_code in _RETRYABLE_STATUS:
        return True
    if response.status_code != 403:
        return False
    try:
        payload = response.json()
        errors = payload.get("error", {}).get("errors", [])
        return any(
            isinstance(item, dict) and item.get("reason") in _RETRYABLE_403_REASONS
            for item in errors
        )
    except (AttributeError, ValueError):
        return False


def _gmail_response_error(response: httpx.Response, *, retryable: bool) -> GmailError:
    if response.status_code == 401:
        return GmailError(
            "Google authorization expired or was revoked. Reconnect Google.",
            category="authentication",
        )
    if response.status_code == 403 and not retryable:
        return GmailError(
            "Google denied Gmail access. Reconnect with the required Gmail scope.",
            category="permission",
        )
    if response.status_code == 404:
        return GmailError("Gmail resource was not found.", category="not_found")
    if response.status_code == 400:
        return GmailError("Gmail rejected the request.", category="validation")
    return GmailError(
        (
            "Gmail returned a temporary service error."
            if retryable
            else "Gmail request failed."
        ),
        category="provider",
        retryable=retryable,
    )


def _retry_delay(attempt: int, response: httpx.Response | None) -> float:
    if response is not None:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return min(30.0, max(0.0, float(retry_after)))
            except ValueError:
                pass
    return float(min(8.0, (2**attempt) + random.uniform(0.0, 0.25)))


def _resource_id(value: str, kind: str) -> str:
    clean = str(value or "").strip()
    if not clean or any(character in clean for character in "/?#"):
        raise GmailRequestError(f"Gmail {kind} ID is invalid.")
    return clean


def _integer(value: object) -> int | None:
    try:
        return int(str(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None and str(value) else None


class _PlainTextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        clean = " ".join(data.split())
        if clean:
            self._parts.append(clean)

    @property
    def text(self) -> str:
        return " ".join(self._parts)


def _gmail_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(get_default_api_timeout()),
        follow_redirects=False,
        trust_env=False,
    )
