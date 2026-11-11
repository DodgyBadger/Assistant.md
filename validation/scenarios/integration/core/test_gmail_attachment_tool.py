"""Real Gmail connection policy, HTTP attachment bytes and safe numbered vault writes."""

from __future__ import annotations

import asyncio
import json

import pytest

from validation.support.application import Application
from validation.support.integrations.gmail_http import configure_gmail, gmail_server

pytestmark = pytest.mark.scenario


async def test_gmail_attachment_policy_and_numbered_tool_writes(
    app: Application,
) -> None:
    vault = app.vault("GmailAttachmentVault")
    await app.start()
    from core.authoring.shared.tool_binding import resolve_tool_binding
    from core.identity import use_execution_authority
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
        arbitrary = await service.download_attachment(
            authority, "message-1", "attachment-1", connection=enabled.slug
        )
        assert arbitrary.content == b"not a pdf"
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
            downloads = [
                request
                for request in provider.requests[before:]
                if "/attachments/" in request["path"]
            ]
            assert len(downloads) == 2
            assert all(
                request["path"].endswith("/messages/message-1/attachments/attachment-1")
                for request in downloads
            )
            before = len(provider.requests)
            for destination in (
                "../escape.pdf",
                "/absolute.pdf",
                "__virtual_docs__/file.docx",
                ".",
            ):
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


@pytest.mark.parametrize("read_operation", ["get_message", "get_thread"])
async def test_gmail_read_attachment_handle_downloads_when_provider_tokens_change(
    app: Application,
    read_operation: str,
) -> None:
    import base64
    from copy import deepcopy

    from validation.support.integrations.gmail_http import message_payload

    vault = app.vault("GmailChangingAttachmentVault")
    await app.start()
    from core.identity import use_execution_authority
    from core.runtime.state import get_runtime_context
    from core.tools.gmail import Gmail

    with gmail_server(app) as provider:
        authority, connection, service, _ = configure_gmail(app, provider)
        get_runtime_context().gmail = service
        original = message_payload("message-1", "thread-1")
        first = original["payload"]["parts"][1]
        first["partId"] = "1"
        first["body"]["attachmentId"] = "first-token-old"
        selected = deepcopy(first)
        selected["partId"] = "2.1"
        selected["mimeType"] = (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        selected["body"]["attachmentId"] = "selected-token-old"
        # The duplicate filename and nested MIME structure force identity-based
        # selection. Only remote HTTP responses are controlled; application
        # reads, token/account access, download, limits and vault writes are real.
        original["payload"]["parts"].append(
            {"partId": "2", "mimeType": "multipart/mixed", "parts": [selected]}
        )
        refreshed = deepcopy(original)
        refreshed["payload"]["parts"][1]["body"]["attachmentId"] = "first-token-new"
        refreshed["payload"]["parts"][2]["parts"][0]["body"][
            "attachmentId"
        ] = "selected-token-new"
        content = b"PK\x03\x04document bytes"
        provider.queue(
            payload=(
                original
                if read_operation == "get_message"
                else {"id": "thread-1", "messages": [original]}
            )
        )
        with use_execution_authority(authority):
            tool = Gmail.get_tool(str(vault))
            read = json.loads(
                await tool.function(
                    operation=read_operation,
                    message_id="message-1",
                    thread_id="thread-1",
                    connection=connection.slug,
                )
            )
            if read_operation == "get_thread":
                read = read["messages"][0]
            descriptor = read["attachments"][1]
            provider.queue(payload=refreshed)
            provider.queue(
                payload={
                    "size": len(content),
                    "data": base64.urlsafe_b64encode(content).decode(),
                }
            )
            result = await tool.function(
                operation="download_attachment",
                message_id=read["message_id"],
                attachment_id=descriptor["attachment_id"],
                connection=connection.slug,
                destination_path="Inbox/selected.docx",
            )
            assert isinstance(result, str), getattr(result, "metadata", result)
            downloaded = json.loads(result)
            assert (vault / downloaded["path"]).read_bytes() == content
            assert downloaded["attachment"]["filename"] == descriptor["filename"]
            assert downloaded["attachment"]["media_type"] == selected["mimeType"]
            downloads = [r for r in provider.requests if "/attachments/" in r["path"]]
            assert len(downloads) == 1
            assert downloads[0]["path"].endswith(
                "/messages/message-1/attachments/selected-token-new"
            )
            assert "selected-token-old" not in json.dumps(downloads)
            assert "content" not in downloaded
            # Attachment-specific recovery must not advise draft recreation or
            # changing a valid containing-message ID.
            provider.queue(payload=refreshed)
            missing = await tool.function(
                operation="download_attachment",
                message_id="message-1",
                attachment_id="part:missing",
                connection=connection.slug,
                destination_path="Inbox/missing.docx",
            )
            assert missing.metadata["gmail_category"] == "attachment_not_found"
            assert "get_message" in missing.metadata["suggested_action"]
            provider.queue(payload=refreshed)
            provider.queue(status=404, payload={"error": {"message": "not found"}})
            disappeared = await tool.function(
                operation="download_attachment",
                message_id=read["message_id"],
                attachment_id=descriptor["attachment_id"],
                connection=connection.slug,
                destination_path="Inbox/disappeared.docx",
            )
            assert disappeared.metadata["gmail_category"] == "attachment_not_found"
            assert "get_message" in disappeared.metadata["suggested_action"]
            assert not (vault / "Inbox/disappeared.docx").exists()


@pytest.mark.parametrize("mismatch", ["message", "connection", "reauthorized_account"])
async def test_gmail_attachment_handles_reject_a_different_message_or_account(
    app: Application, mismatch: str
) -> None:
    from dataclasses import replace

    from core.identity import use_execution_authority
    from core.runtime.state import get_runtime_context
    from core.tools.gmail import Gmail
    from validation.support.integrations.gmail_http import message_payload

    vault = app.vault("GmailAttachmentBindingVault")
    await app.start()
    with gmail_server(app) as provider:
        authority, connection, _, _ = configure_gmail(app, provider)
        original = message_payload("message-1", "thread-1")
        original["payload"]["parts"][1]["partId"] = "1"
        provider.queue(payload=original)
        with use_execution_authority(authority):
            tool = Gmail.get_tool(str(vault))
            read = json.loads(
                await tool.function(
                    operation="get_message",
                    message_id="message-1",
                    connection=connection.slug,
                )
            )
            handle = read["attachments"][0]["attachment_id"]
            target_message = "message-2" if mismatch == "message" else "message-1"
            target_connection = connection
            if mismatch == "connection":
                _, target_connection, _, _ = configure_gmail(
                    app,
                    provider,
                    name="Other mailbox",
                    is_default=False,
                )
            elif mismatch == "reauthorized_account":
                runtime = get_runtime_context()
                state = runtime.google_connection.load_token_state(
                    authority, connection.connection_id
                )
                assert state is not None
                token = "SECOND_ACCOUNT_ACCESS_SENTINEL"
                app.evidence.register_secret(token)
                runtime.google_connection.save_token_state(
                    authority,
                    replace(
                        state,
                        account_id="other-mailbox",
                        account_email="other@example.com",
                        access_token=token,
                    ),
                    connection.connection_id,
                )
            target = message_payload(target_message, "thread-other")
            target["payload"]["parts"][1]["partId"] = "1"
            target["payload"]["parts"][1]["body"][
                "attachmentId"
            ] = "other-document-token"
            provider.queue(payload=target)
            before = len(provider.requests)
            result = await tool.function(
                operation="download_attachment",
                message_id=target_message,
                attachment_id=handle,
                connection=target_connection.slug,
                destination_path="Inbox/wrong-document.pdf",
            )
            assert not isinstance(result, str), result
            assert result.metadata["gmail_category"] == "attachment_not_found"
            assert not (vault / "Inbox/wrong-document.pdf").exists()
            assert all(
                "/attachments/" not in request["path"]
                for request in provider.requests[before:]
            )


@pytest.mark.parametrize("phase", ["metadata", "download"])
async def test_gmail_download_rejects_account_changes_while_provider_is_pending(
    app: Application, phase: str
) -> None:
    from dataclasses import replace

    from core.identity import use_execution_authority
    from core.runtime.state import get_runtime_context
    from core.tools.gmail import Gmail
    from validation.support.integrations.gmail_http import message_payload

    vault = app.vault("GmailPendingAccountVault")
    await app.start()
    with gmail_server(app) as provider:
        authority, connection, _, _ = configure_gmail(app, provider)
        descriptor = message_payload("message-1", "thread-1")
        descriptor["payload"]["parts"][1]["partId"] = "1"
        provider.queue(payload=descriptor)
        with use_execution_authority(authority):
            tool = Gmail.get_tool(str(vault))
            read = json.loads(
                await tool.function(
                    operation="get_message",
                    message_id="message-1",
                    connection=connection.slug,
                )
            )
            provider.queue(payload=descriptor, delay=0.3 if phase == "metadata" else 0)
            if phase == "download":
                provider.queue(payload={"size": 1, "data": "QQ"}, delay=0.3)
            before = len(provider.requests)
            pending = asyncio.create_task(
                tool.function(
                    operation="download_attachment",
                    message_id="message-1",
                    attachment_id=read["attachments"][0]["attachment_id"],
                    connection=connection.slug,
                    destination_path="Inbox/changed-account.pdf",
                )
            )
            try:
                async with asyncio.timeout(5):
                    while not any(
                        ("/attachments/" in request["path"]) == (phase == "download")
                        for request in provider.requests[before:]
                    ):
                        await asyncio.sleep(0.01)
                runtime = get_runtime_context()
                state = runtime.google_connection.load_token_state(
                    authority, connection.connection_id
                )
                assert state is not None
                token = "PENDING_ACCOUNT_ACCESS_SENTINEL"
                app.evidence.register_secret(token)
                runtime.google_connection.save_token_state(
                    authority,
                    replace(
                        state,
                        account_id="new-pending-account",
                        account_email="pending@example.com",
                        access_token=token,
                    ),
                    connection.connection_id,
                )
                result = await pending
                assert not isinstance(result, str), result
                assert result.metadata["gmail_category"] == "connection_changed"
                assert not (vault / "Inbox/changed-account.pdf").exists()
                if phase == "metadata":
                    assert all(
                        "/attachments/" not in request["path"]
                        for request in provider.requests[before:]
                    )
            finally:
                if not pending.done():
                    pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
