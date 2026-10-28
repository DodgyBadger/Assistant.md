"""Real Gmail connection policy, HTTP attachment bytes and safe numbered vault writes."""

from __future__ import annotations

import asyncio
import json

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.gmail_http import configure_gmail, gmail_server

pytestmark = pytest.mark.scenario


async def test_gmail_attachment_policy_and_numbered_tool_writes(
    app: Application,
) -> None:
    vault = app.vault("GmailAttachmentVault")
    await app.start()
    from core.authoring.shared.tool_binding import resolve_tool_binding
    from core.identity import use_execution_authority
    from core.integrations.google.gmail import GmailError
    from core.tools.base import ToolRecoveryPolicy, recovery_policy_from_tool_metadata
    from core.tools.gmail import Gmail

    with gmail_server(app) as provider:
        content = b"%PDF-1.7\nuntrusted attachment"
        provider.attachment_content = content
        authority, enabled, service, _ = configure_gmail(app, provider)
        downloaded = await service.download_attachment(
            authority, "message-1", "attachment-1", connection=enabled.slug
        )
        assert downloaded.content == content
        _, disabled, _, _ = configure_gmail(
            app,
            provider,
            name="Disabled",
            download=False,
            compose=False,
            is_default=False,
        )
        before = len(provider.requests)
        with pytest.raises(ValueError, match="disabled"):
            await service.download_attachment(
                authority, "message-1", "attachment-1", connection=disabled.slug
            )
        assert len(provider.requests) == before
        status = service.status(authority)
        assert (status["attachment_download_enabled"], status["attachment_max_mb"]) == (
            True,
            7,
        )
        provider.attachment_content = b"not a pdf"
        with pytest.raises(GmailError, match="valid PDF"):
            await service.download_attachment(
                authority, "message-1", "attachment-1", connection=enabled.slug
            )
        provider.attachment_content = content
        # Compose the real service selected above into the running application.
        from core.runtime.state import get_runtime_context

        get_runtime_context().gmail = service
        with use_execution_authority(authority):
            tool = Gmail.get_tool(vault_path=str(vault))
            before = len(provider.requests)
            first, second = await asyncio.gather(
                *[
                    tool.function(
                        operation="download_attachment",
                        message_id="message-1",
                        attachment_id="attachment-1",
                        connection=enabled.slug,
                        destination_path="Inbox/report.pdf",
                    )
                    for _ in range(2)
                ]
            )
            results = [json.loads(first), json.loads(second)]
            assert sorted(item["path"] for item in results) == [
                "Inbox/report (1).pdf",
                "Inbox/report.pdf",
            ]
            assert all(
                "content" not in item and str(content) not in json.dumps(item)
                for item in results
            )
            assert all(
                (vault / item["path"]).read_bytes() == content for item in results
            )
            downloads = provider.requests[before:]
            assert len(downloads) == 2
            assert all(
                request["path"].endswith("/messages/message-1/attachments/attachment-1")
                for request in downloads
            )
            before = len(provider.requests)
            for destination in ("../escape.pdf", "/absolute.pdf", "Inbox/file.docx"):
                result = await tool.function(
                    operation="download_attachment",
                    message_id="message-1",
                    attachment_id="attachment-1",
                    destination_path=destination,
                )
                assert result.metadata["status"] == "failed"
            assert len(provider.requests) == before
            assert not (vault.parent / "escape.pdf").exists()
            vaultless = await Gmail.get_tool().function(
                operation="download_attachment",
                message_id="message-1",
                attachment_id="attachment-1",
                destination_path="report.pdf",
            )
            assert "vault is required" in str(vaultless.return_value).lower()
            assert Gmail.get_recovery_policy() is ToolRecoveryPolicy.MANUAL_REQUIRED
            binding = resolve_tool_binding(["gmail"], vault_path=str(vault))
            assert (
                recovery_policy_from_tool_metadata(binding.tool_functions[0].metadata)
                is ToolRecoveryPolicy.MANUAL_REQUIRED
            )
        activity = app.request(
            "/api/system/activity-log", params={"limit": 200, "tag": "gmail-resource"}
        )
        assert activity.status_code == 200
        completed = [
            entry["data"]
            for entry in activity.json()["entries"]
            if entry["data"].get("event") == "gmail_attachment_download_completed"
        ]
        assert completed and all(
            row["connection_id"] == enabled.connection_id for row in completed
        )
