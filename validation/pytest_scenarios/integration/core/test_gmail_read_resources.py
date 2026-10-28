"""Real HTTP Gmail reads normalize bounded MIME resources without eager attachment fetches."""

from __future__ import annotations

import base64
import json
from email import policy
from email.parser import BytesParser

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.gmail_http import (
    ACCESS_TOKEN,
    configure_gmail,
    gmail_server,
)

pytestmark = pytest.mark.scenario


async def test_gmail_search_message_thread_and_draft_mime(app: Application) -> None:
    app.vault("GmailReadVault")
    await app.start()
    with gmail_server(app) as provider:
        _, _, _, client = configure_gmail(app, provider)
        search = await client.search(query="from:sender@example.com", max_results=2)
        assert (
            [item.message_id for item in search.messages],
            search.result_count,
            search.partial,
        ) == (["message-1", "message-2"], 2, True)
        message = await client.get_message("message-1", max_characters=12)
        assert (message.subject, message.text, message.text_truncated) == (
            "Known message",
            "Hello world!",
            True,
        )
        assert (
            len(message.attachments),
            message.attachments[0].filename,
            message.attachments[0].attachment_id,
        ) == (1, "report.pdf", "attachment-1")
        thread = await client.get_thread("thread-1", max_messages=1, max_characters=50)
        assert (
            len(thread.messages),
            thread.omitted_message_count,
            thread.truncated,
        ) == (1, 1, True)
        assert all(
            "/attachments/" not in request["path"] for request in provider.requests
        )
        draft = await client.create_draft(
            subject="Review notes", body="Plain draft body"
        )
        assert (draft.draft_id, draft.message_id, draft.thread_id) == (
            "draft-1",
            "draft-message-1",
            "draft-thread-1",
        )
        request = next(
            request
            for request in provider.requests
            if request["path"].endswith("/drafts")
        )
        raw = json.loads(request["body"])["message"]["raw"]
        parsed = BytesParser(policy=policy.default).parsebytes(
            base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        )
        assert (parsed["To"], parsed["Subject"], parsed.get_content().strip()) == (
            None,
            "Review notes",
            "Plain draft body",
        )
        assert all(
            request["authorization"] == f"Bearer {ACCESS_TOKEN}"
            for request in provider.requests
        )
        for invalid in ("Injected\r\nBcc: hidden@example.com", "Control\x00character"):
            before = len(provider.requests)
            with pytest.raises(ValueError):
                await client.create_draft(subject=invalid, body="Body")
            assert len(provider.requests) == before
