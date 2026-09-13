"""Deterministic Gmail read-resource and tool-boundary validation."""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import tempfile
from collections.abc import AsyncIterator, Callable
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

_direct_run_root: tempfile.TemporaryDirectory[str] | None = None
if __name__ == "__main__":
    from core.runtime.paths import set_bootstrap_roots

    _direct_run_root = tempfile.TemporaryDirectory(prefix="assistantmd-gmail-read-")
    direct_root = Path(_direct_run_root.name)
    data_root = direct_root / "data"
    system_root = direct_root / "system"
    data_root.mkdir()
    system_root.mkdir()
    set_bootstrap_roots(data_root=data_root, system_root=system_root)

from core.integrations.google.gmail import GmailAPIClient, GmailError  # noqa: E402
from core.integrations.google.oauth import GoogleOAuthError  # noqa: E402
from core.tools.gmail import _write_numbered_attachment  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class GmailReadToolsScenario(BaseScenario):
    """Prove bounded search, MIME normalization, and attachment metadata."""

    async def test_scenario(self) -> None:
        requests: list[httpx.Request] = []
        client = GmailAPIClient(
            access_token_provider=_access_token,
            http_client_factory=lambda: _gmail_client(requests),
            sleep=_no_sleep,
        )

        search = await client.search(query="from:sender@example.com", max_results=2)
        self.soft_assert_equal(
            (
                [item.message_id for item in search.messages],
                search.result_count,
                search.partial,
            ),
            (["message-1", "message-2"], 2, True),
            "Search should return stable message handles and a bounded count",
        )

        message = await client.get_message("message-1", max_characters=12)
        self.soft_assert_equal(
            (message.subject, message.text, message.text_truncated),
            ("Known message", "Hello world!", True),
            "Message reads should prefer and bound normalized plain text",
        )

        draft = await client.create_draft(
            subject="Review notes",
            body="Plain draft body",
        )
        self.soft_assert_equal(
            (draft.draft_id, draft.message_id, draft.thread_id),
            ("draft-1", "draft-message-1", "draft-thread-1"),
            "Draft creation should return confirmation identifiers",
        )
        draft_request = next(
            request for request in requests if request.url.path.endswith("/drafts")
        )
        encoded_raw = json.loads(draft_request.content)["message"]["raw"]
        raw = base64.urlsafe_b64decode(encoded_raw + "=" * (-len(encoded_raw) % 4))
        parsed = BytesParser(policy=policy.default).parsebytes(raw)
        self.soft_assert_equal(
            (parsed["To"], parsed["Subject"], parsed.get_content().strip()),
            (None, "Review notes", "Plain draft body"),
            "Draft creation should produce recipient-free bounded plain-text MIME",
        )

        draft_attempts = 0

        def ambiguous_draft(_request: httpx.Request) -> httpx.Response:
            nonlocal draft_attempts
            draft_attempts += 1
            return httpx.Response(503)

        ambiguous_client = GmailAPIClient(
            access_token_provider=_access_token,
            http_client_factory=lambda: _response_client(ambiguous_draft),
            sleep=_no_sleep,
        )
        try:
            await ambiguous_client.create_draft(
                subject="Potential duplicate",
                body="Inspect Gmail before trying again.",
            )
        except GmailError as exc:
            self.soft_assert_equal(
                (draft_attempts, exc.category, exc.retryable),
                (1, "mutation_outcome_unknown", False),
                "Ambiguous draft mutations must not retry automatically",
            )
        else:
            self.soft_assert(False, "Ambiguous draft creation should fail explicitly")

        pre_request_attempts: list[httpx.Request] = []
        oauth_failure_client = GmailAPIClient(
            access_token_provider=lambda: _failed_access_token(
                GoogleOAuthError("Google authorization must be reconnected.")
            ),
            http_client_factory=lambda: _gmail_client(pre_request_attempts),
            sleep=_no_sleep,
        )
        try:
            await oauth_failure_client.create_draft(
                subject="Not dispatched",
                body="Body",
            )
        except GoogleOAuthError:
            pass
        else:
            self.soft_assert(
                False,
                "Pre-request OAuth failures should retain configuration semantics",
            )
        self.soft_assert_equal(
            pre_request_attempts,
            [],
            "OAuth failures should occur before any draft request is dispatched",
        )

        unexpected_failure_client = GmailAPIClient(
            access_token_provider=lambda: _failed_access_token(
                ValueError("unexpected token bug")
            ),
            http_client_factory=lambda: _gmail_client(pre_request_attempts),
            sleep=_no_sleep,
        )
        try:
            await unexpected_failure_client.create_draft(
                subject="Not dispatched",
                body="Body",
            )
        except ValueError as exc:
            self.soft_assert_equal(
                str(exc),
                "unexpected token bug",
                "Unexpected pre-request failures should preserve fail-fast behavior",
            )
        else:
            self.soft_assert(
                False,
                "Unexpected pre-request failures should not become mutation outcomes",
            )

        request_timeout_attempts = 0

        def request_timeout(_request: httpx.Request) -> httpx.Response:
            nonlocal request_timeout_attempts
            request_timeout_attempts += 1
            return httpx.Response(408)

        timed_out_client = GmailAPIClient(
            access_token_provider=_access_token,
            http_client_factory=lambda: _response_client(request_timeout),
            sleep=_no_sleep,
        )
        try:
            await timed_out_client.create_draft(subject="Created?", body="Body")
        except GmailError as exc:
            self.soft_assert_equal(
                (request_timeout_attempts, exc.category, exc.retryable),
                (1, "mutation_outcome_unknown", False),
                "Post-dispatch request timeouts must report an unknown outcome",
            )
        else:
            self.soft_assert(False, "Timed-out draft creation should fail explicitly")

        definite_draft_attempts = 0

        def rejected_draft(_request: httpx.Request) -> httpx.Response:
            nonlocal definite_draft_attempts
            definite_draft_attempts += 1
            return httpx.Response(400, json={"error": {"message": "invalid"}})

        rejected_client = GmailAPIClient(
            access_token_provider=_access_token,
            http_client_factory=lambda: _response_client(rejected_draft),
            sleep=_no_sleep,
        )
        try:
            await rejected_client.create_draft(
                subject="Rejected",
                body="Body",
            )
        except GmailError as exc:
            self.soft_assert_equal(
                (definite_draft_attempts, exc.category, exc.retryable),
                (1, "validation", False),
                "Definite draft validation failures should remain non-retryable",
            )
        else:
            self.soft_assert(False, "Rejected draft creation should fail explicitly")

        for invalid_subject in (
            "Injected\r\nBcc: hidden@example.com",
            "Control\x00character",
        ):
            try:
                await client.create_draft(
                    subject=invalid_subject,
                    body="Body",
                )
            except ValueError:
                pass
            else:
                self.soft_assert(False, "Draft headers should reject invalid input")

        for response in (
            httpx.Response(200, content=b"not-json"),
            httpx.Response(200, content=b"x" * (64 * 1024 + 1)),
            httpx.Response(200, json={"message": {"id": "message-only"}}),
        ):
            malformed_success = GmailAPIClient(
                access_token_provider=_access_token,
                http_client_factory=lambda response=response: _response_client(
                    lambda _request: response
                ),
                sleep=_no_sleep,
            )
            try:
                await malformed_success.create_draft(subject="Created?", body="Body")
            except GmailError as exc:
                self.soft_assert_equal(
                    (exc.category, exc.retryable),
                    ("mutation_outcome_unknown", False),
                    "Malformed successful responses must report an unknown outcome",
                )
            else:
                self.soft_assert(
                    False, "Malformed draft success should fail explicitly"
                )
        self.soft_assert_equal(
            (
                len(message.attachments),
                message.attachments[0].filename,
                message.attachments[0].attachment_id,
            ),
            (1, "report.pdf", "attachment-1"),
            "Message reads should expose attachment descriptors without bytes",
        )

        thread = await client.get_thread("thread-1", max_messages=1, max_characters=50)
        self.soft_assert_equal(
            (len(thread.messages), thread.omitted_message_count, thread.truncated),
            (1, 1, True),
            "Thread reads should report bounded message truncation",
        )
        self.soft_assert(
            all(
                request.headers.get("authorization") == "Bearer access-token"
                for request in requests
            ),
            "Every Gmail request should use principal-resolved authorization",
        )
        self.soft_assert(
            all("attachments/" not in request.url.path for request in requests),
            "Message reads must not download attachment bytes",
        )

        downloaded = await client.download_attachment(
            "message-1", "attachment-1", max_bytes=32
        )
        self.soft_assert_equal(
            downloaded,
            b"%PDF-1.7\nexample",
            "Attachment retrieval should decode bounded base64url bytes",
        )
        try:
            await client.download_attachment("message-1", "attachment-1", max_bytes=4)
        except GmailError as exc:
            self.soft_assert_equal(
                exc.category,
                "attachment_too_large",
                "Decoded attachment size must respect the configured limit",
            )
        else:
            self.soft_assert(False, "Oversized attachments should fail")

        malformed_attachment = GmailAPIClient(
            access_token_provider=_access_token,
            http_client_factory=lambda: _response_client(
                lambda _request: httpx.Response(
                    200, json={"size": 4, "data": "!invalid!"}
                )
            ),
            sleep=_no_sleep,
        )
        try:
            await malformed_attachment.download_attachment(
                "message-1", "attachment-1", max_bytes=32
            )
        except GmailError as exc:
            self.soft_assert_equal(
                exc.category,
                "provider_response",
                "Malformed attachment base64 should be rejected",
            )
        else:
            self.soft_assert(False, "Malformed attachment base64 should fail")

        invalid_json = GmailAPIClient(
            access_token_provider=_access_token,
            http_client_factory=lambda: _response_client(
                lambda _request: httpx.Response(200, content=b"not-json")
            ),
            sleep=_no_sleep,
        )
        try:
            await invalid_json.download_attachment(
                "message-1", "attachment-1", max_bytes=32
            )
        except GmailError as exc:
            self.soft_assert_equal(
                exc.category,
                "provider_response",
                "Malformed attachment envelopes should be rejected",
            )
        else:
            self.soft_assert(False, "Malformed attachment envelopes should fail")

        declared_oversize = GmailAPIClient(
            access_token_provider=_access_token,
            http_client_factory=lambda: _response_client(
                lambda _request: httpx.Response(
                    200,
                    headers={"Content-Length": "5000"},
                    stream=_ChunkStream([b"{}"]),
                )
            ),
            sleep=_no_sleep,
        )
        try:
            await declared_oversize.download_attachment(
                "message-1", "attachment-1", max_bytes=32
            )
        except GmailError as exc:
            self.soft_assert_equal(
                exc.category,
                "attachment_too_large",
                "Oversized declared response lengths should fail before buffering",
            )
        else:
            self.soft_assert(False, "Oversized declared response should fail")

        streamed_oversize = GmailAPIClient(
            access_token_provider=_access_token,
            http_client_factory=lambda: _response_client(
                lambda _request: httpx.Response(
                    200,
                    stream=_ChunkStream([b"x" * 5000]),
                )
            ),
            sleep=_no_sleep,
        )
        try:
            await streamed_oversize.download_attachment(
                "message-1", "attachment-1", max_bytes=32
            )
        except GmailError as exc:
            self.soft_assert_equal(
                exc.category,
                "attachment_too_large",
                "Oversized streamed chunks should fail before entering the response buffer",
            )
        else:
            self.soft_assert(False, "Oversized streamed response should fail")

        attachment_retry_attempts = 0

        def retry_attachment(_request: httpx.Request) -> httpx.Response:
            nonlocal attachment_retry_attempts
            attachment_retry_attempts += 1
            if attachment_retry_attempts == 1:
                return httpx.Response(429, headers={"Retry-After": "0"})
            if attachment_retry_attempts == 2:
                return httpx.Response(503)
            return httpx.Response(
                200, json={"size": 16, "data": "JVBERi0xLjcKZXhhbXBsZQ"}
            )

        retrying_attachment = GmailAPIClient(
            access_token_provider=_access_token,
            http_client_factory=lambda: _response_client(retry_attachment),
            sleep=_no_sleep,
        )
        retried_download = await retrying_attachment.download_attachment(
            "message-1", "attachment-1", max_bytes=32
        )
        self.soft_assert_equal(
            (attachment_retry_attempts, retried_download),
            (3, b"%PDF-1.7\nexample"),
            "Bounded attachment requests should preserve Gmail retry behavior",
        )

        with tempfile.TemporaryDirectory(prefix="gmail-attachment-vault-") as root:
            vault = Path(root)
            first = _write_numbered_attachment(
                vault_path=str(vault),
                destination_path="Inbox/report.pdf",
                content=downloaded,
            )
            second = _write_numbered_attachment(
                vault_path=str(vault),
                destination_path="Inbox/report.pdf",
                content=downloaded,
            )
            self.soft_assert_equal(
                (first, second),
                ("Inbox/report.pdf", "Inbox/report (1).pdf"),
                "Attachment downloads should create numbered files instead of overwriting",
            )

        retry_attempts = 0

        def retry_then_empty(request: httpx.Request) -> httpx.Response:
            nonlocal retry_attempts
            retry_attempts += 1
            if retry_attempts == 1:
                return httpx.Response(429, headers={"Retry-After": "0"})
            if retry_attempts == 2:
                return httpx.Response(503)
            return httpx.Response(200, json={"messages": [], "resultSizeEstimate": 0})

        retrying = GmailAPIClient(
            access_token_provider=_access_token,
            http_client_factory=lambda: _response_client(retry_then_empty),
            sleep=_no_sleep,
        )
        empty = await retrying.search(query="label:does-not-exist", max_results=1)
        self.soft_assert_equal(
            (retry_attempts, empty.result_count, empty.partial),
            (3, 0, False),
            "Rate limits and service failures should retry while an empty mailbox result remains successful",
        )

        authentication = GmailAPIClient(
            access_token_provider=_access_token,
            http_client_factory=lambda: _response_client(
                lambda _request: httpx.Response(401)
            ),
            sleep=_no_sleep,
        )
        try:
            await authentication.search(query="newer_than:1d", max_results=1)
        except GmailError as exc:
            self.soft_assert_equal(
                (exc.category, exc.retryable),
                ("authentication", False),
                "Revoked authorization should be distinguishable from an empty result",
            )
        else:
            self.soft_assert(False, "Revoked authorization should fail explicitly")

        timeout_attempts = 0

        def always_timeout(request: httpx.Request) -> httpx.Response:
            nonlocal timeout_attempts
            timeout_attempts += 1
            raise httpx.ConnectTimeout("timed out", request=request)

        timing_out = GmailAPIClient(
            access_token_provider=_access_token,
            http_client_factory=lambda: _response_client(always_timeout),
            sleep=_no_sleep,
        )
        try:
            await timing_out.search(query="newer_than:1d", max_results=1)
        except GmailError as exc:
            self.soft_assert_equal(
                (timeout_attempts, exc.category, exc.retryable),
                (3, "network", True),
                "Network timeouts should exhaust the bounded connector retry policy",
            )
        else:
            self.soft_assert(False, "Repeated Gmail timeouts should fail")

        malformed = _message("message-bad", "thread-bad")
        malformed["payload"]["parts"][0]["body"]["data"] = "!invalid!"
        malformed_client = GmailAPIClient(
            access_token_provider=_access_token,
            http_client_factory=lambda: _response_client(
                lambda _request: httpx.Response(200, json=malformed)
            ),
            sleep=_no_sleep,
        )
        malformed_message = await malformed_client.get_message(
            "message-bad", max_characters=10
        )
        self.soft_assert_equal(
            malformed_message.text,
            "",
            "Malformed MIME body data should normalize safely without leaking encoded bytes",
        )

        self.assert_no_failures()
        self.teardown_scenario()


async def _access_token() -> str:
    return "access-token"


async def _failed_access_token(error: Exception) -> str:
    raise error


async def _no_sleep(_seconds: float) -> None:
    return None


def _gmail_client(requests: list[httpx.Request]) -> httpx.AsyncClient:
    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path.endswith("/messages"):
            return httpx.Response(
                200,
                json={
                    "messages": [
                        {"id": "message-1", "threadId": "thread-1"},
                        {"id": "message-2", "threadId": "thread-2"},
                    ],
                    "resultSizeEstimate": 2,
                    "nextPageToken": "next-page",
                },
            )
        if path.endswith("/drafts") and request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "id": "draft-1",
                    "message": {
                        "id": "draft-message-1",
                        "threadId": "draft-thread-1",
                    },
                },
            )
        if path.endswith("/messages/message-1"):
            return httpx.Response(200, json=_message("message-1", "thread-1"))
        if path.endswith("/messages/message-2"):
            return httpx.Response(200, json=_message("message-2", "thread-2"))
        if path.endswith("/messages/message-1/attachments/attachment-1"):
            return httpx.Response(
                200,
                json={"size": 16, "data": "JVBERi0xLjcKZXhhbXBsZQ"},
            )
        if path.endswith("/threads/thread-1"):
            return httpx.Response(
                200,
                json={
                    "id": "thread-1",
                    "historyId": "99",
                    "messages": [
                        _message("message-1", "thread-1"),
                        _message("message-3", "thread-1"),
                    ],
                },
            )
        return httpx.Response(404, json={"error": {"message": "not found"}})

    return httpx.AsyncClient(transport=httpx.MockTransport(respond))


def _response_client(
    respond: Callable[[httpx.Request], httpx.Response],
) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(respond))


class _ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


def _message(message_id: str, thread_id: str) -> dict[str, Any]:
    return {
        "id": message_id,
        "threadId": thread_id,
        "historyId": "42",
        "internalDate": "1700000000000",
        "labelIds": ["INBOX", "UNREAD"],
        "snippet": "Hello world preview",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "Subject", "value": "Known message"},
                {"name": "From", "value": "Sender <sender@example.com>"},
                {"name": "To", "value": "owner@example.com"},
                {"name": "Date", "value": "Tue, 1 Jan 2026 10:00:00 +0000"},
            ],
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": "SGVsbG8gd29ybGQhIEV4dHJh"},
                },
                {
                    "mimeType": "application/pdf",
                    "filename": "report.pdf",
                    "body": {"attachmentId": "attachment-1", "size": 1234},
                },
            ],
        },
    }


if __name__ == "__main__":
    asyncio.run(GmailReadToolsScenario().test_scenario())
