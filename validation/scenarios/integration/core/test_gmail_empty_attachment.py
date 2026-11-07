"""Real file/review/attachment boundaries exposed by branch hardening review."""

from __future__ import annotations

import json

import pytest

from validation.support.application import Application
from validation.support.integrations.gmail_http import (
    configure_gmail,
    gmail_server,
    message_payload,
)

pytestmark = pytest.mark.scenario


async def test_gmail_downloads_a_zero_byte_attachment(app: Application) -> None:
    vault = app.vault("EmptyAttachmentVault")
    await app.start()
    from core.identity import use_execution_authority
    from core.tools.gmail import Gmail

    # Real Google client/service and persistence; only remote HTTP is controlled.
    with gmail_server(app) as provider:
        authority, connection, _, _ = configure_gmail(app, provider)
        provider.attachment_content = b""
        descriptor = message_payload("message-1", "thread-1")
        part = descriptor["payload"]["parts"][1]
        part.update({"filename": "empty.txt", "mimeType": "text/plain"})
        part["body"]["size"] = 0
        provider.queue(payload=descriptor)
        with use_execution_authority(authority):
            result = await Gmail.get_tool(vault_path=str(vault)).function(
                operation="download_attachment",
                message_id="message-1",
                attachment_id="attachment-1",
                connection=connection.slug,
                destination_path="Inbox/empty.txt",
            )
        assert isinstance(result, str), getattr(result, "metadata", result)
        downloaded = json.loads(result)
        assert (vault / downloaded["path"]).read_bytes() == b""
        assert downloaded["attachment"]["declared_size"] == 0
