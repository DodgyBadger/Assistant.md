"""Byte transport, literal filenames, and plain text use real bound file tools."""

from __future__ import annotations

import json

import pytest

from validation.support.application import Application
from validation.support.chat.chat_tasks import chat_turn
from validation.support.chat.code_tool_chat import (
    code_tool_provider,
    configure_provider,
)
from validation.support.chat.inline_edit_reviews import request_review, submit_review
from validation.support.integrations.gmail_http import (
    configure_gmail,
    gmail_server,
    message_payload,
)
from validation.support.vault.file_tools import BoundFileTools
from validation.support.vault.vault_recovery import execute_rollback, preview_rollback

pytestmark = pytest.mark.scenario


async def test_copy_and_move_preserve_arbitrary_bytes_and_recovery(
    app: Application,
) -> None:
    vault = app.vault("BinaryCopyVault")
    app.write(vault, "Keep.md", "keep")
    original = b"PK\x03\x04\x00\xff\r\nsource document"
    (vault / "source.odt").write_bytes(original)
    await app.start()
    tools = BoundFileTools(app, vault)
    moved = await tools.call(
        "file_write",
        operation="move",
        path="source.odt",
        destination="Project/source.odt",
    )
    assert moved.metadata["status"] == "completed"
    assert (vault / "Project/source.odt").read_bytes() == original
    copied = await tools.call(
        "file_write",
        operation="copy",
        path="Project/source.odt",
        destination="AssistantMD/shell-exchange/copy.odt",
    )
    assert copied.metadata["status"] == "completed", copied.return_value
    target = vault / "AssistantMD/shell-exchange/copy.odt"
    activity = app.request(
        "/api/system/activity-log", params={"limit": 200, "tag": "vault-state"}
    )
    assert activity.status_code == 200
    assert any(
        row.get("data", {}).get("event") == "vault_activity_completed"
        and row["data"].get("activity_id") == copied.metadata["activity_id"]
        and row["data"].get("status") == "completed"
        and row["data"].get("vault_name") == vault.name
        for row in activity.json()["entries"]
    )
    assert (
        target.read_bytes() == (vault / "Project/source.odt").read_bytes() == original
    )
    rejected = await tools.call(
        "file_write",
        operation="copy",
        path="Keep.md",
        destination="AssistantMD/shell-exchange/copy.odt",
    )
    assert rejected.metadata["status"] == "already_exists"
    assert target.read_bytes() == original
    overwritten = await tools.call(
        "file_write",
        operation="copy",
        path="Keep.md",
        destination="AssistantMD/shell-exchange/copy.odt",
        overwrite=True,
    )
    assert overwritten.metadata["status"] == "completed"
    assert target.read_bytes() == b"keep"
    recovery = preview_rollback(app, vault.name, overwritten.metadata["activity_id"])
    assert recovery["can_rollback"], recovery
    assert (
        execute_rollback(
            app, vault.name, overwritten.metadata["activity_id"], recovery
        ).status_code
        == 200
    )
    assert target.read_bytes() == original and (vault / "Keep.md").read_text() == "keep"
    for path, destination in [
        ("Project", "clone"),
        ("Keep.md", "Keep.md"),
        ("Keep.md", "../escape"),
        ("Keep.md", "/absolute-copy.data"),
        ("Keep.md", "__virtual_docs__/copy.md"),
    ]:
        failed = await tools.call(
            "file_write",
            operation="copy",
            path=path,
            destination=destination,
            overwrite=True,
        )
        assert failed.metadata["status"] != "completed"
    assert not (vault / "clone").exists()
    assert not (vault.parent / "escape").exists()
    assert not (vault / "absolute-copy.data").exists()
    listed = await tools.call("file_read", operation="list", path="Project")
    assert "Project/source.odt" in listed.metadata["files"]


async def test_plain_text_operations_use_literal_paths_and_preserve_text(
    app: Application,
) -> None:
    vault = app.vault("PlainTextVault")
    app.write(vault, "report.md", "Markdown sibling\n")
    app.write(vault, "LICENSE", "original\n")
    app.write(vault, "LICENSE.md", "keep sibling\n")
    app.write(vault, "baseline.json", '{"working": true}\n')
    await app.start()
    tools = BoundFileTools(app, vault)
    baseline = await tools.call("file_read", operation="read", path="baseline.json")
    assert json.loads(baseline.return_value) == {"working": True}
    omitted = await tools.call("file_read", operation="read", path="report")
    assert omitted.metadata["status"] == "not_found"
    for path in [
        "settings.yaml",
        "data.json",
        "table.csv",
        "notes.txt",
        "script.py",
        "report",
        "LICENSE",
    ]:
        created = await tools.call(
            "file_write",
            operation="write",
            path=path,
            content="first\r\nsecond\r\n",
            overwrite=True,
        )
        assert created.metadata["status"] == "completed", created.return_value
        edited = await tools.call(
            "file_write",
            operation="edit_line",
            path=path,
            line_number=2,
            old_text="second",
            new_text="changed",
        )
        assert edited.metadata["status"] == "completed", edited.return_value
        replaced = await tools.call(
            "file_write",
            operation="replace_text",
            path=path,
            old_text="first",
            new_text="start",
        )
        assert replaced.metadata["status"] == "completed", replaced.return_value
        appended = await tools.call(
            "file_write", operation="append", path=path, content="last\r\n"
        )
        assert appended.metadata["status"] == "completed", appended.return_value
        assert (vault / path).read_bytes() == b"start\r\nchanged\r\nlast\r\n"
        read = await tools.call("file_read", operation="read", path=path)
        assert read.metadata["media_mode"] == "text"
        assert read.return_value == "start\r\nchanged\r\nlast\r\n"
    assert (vault / "report.md").read_text() == "Markdown sibling\n"
    assert (vault / "LICENSE.md").read_text() == "keep sibling\n"


@pytest.mark.parametrize(
    "content", [b"%PDF-1.7\nplain ASCII PDF", b"binary\x00content", b"\xff\xfeinvalid"]
)
async def test_text_operations_reject_binary_before_mutation(
    app: Application, content: bytes
) -> None:
    vault = app.vault("BinaryTextRejectionVault")
    (vault / "document.data").write_bytes(content)
    await app.start()
    tools = BoundFileTools(app, vault)
    for operation, arguments in [
        ("read", {}),
        ("write", {"content": "replacement", "overwrite": True}),
        ("append", {"content": "append"}),
        ("edit_line", {"line_number": 1, "old_text": "x", "new_text": "y"}),
        ("replace_text", {"old_text": "PDF", "new_text": "text"}),
    ]:
        result = await tools.call(
            "file_read" if operation == "read" else "file_write",
            operation=operation,
            path="document.data",
            **arguments,
        )
        assert result.metadata["status"] != "completed", (
            operation,
            result.return_value,
        )
        assert (vault / "document.data").read_bytes() == content
    new_binary = await tools.call(
        "file_write", operation="write", path="new.data", content="text\x00binary"
    )
    assert new_binary.metadata["status"] != "completed"
    assert not (vault / "new.data").exists()


async def test_copy_review_and_failed_task_preserve_source_and_destination(
    app: Application,
) -> None:
    vault = app.vault("CopyReviewVault")
    app.write(vault, "source.data", "source bytes")
    app.write(vault, "destination.data", "original destination")
    with code_tool_provider() as server:
        await app.start()
        configure_provider(app, server)
        args = {
            "operation": "copy",
            "path": "source.data",
            "destination": "destination.data",
            "overwrite": True,
        }
        review = await request_review(app, server, vault.name, "copy-review", args)
        for path in ["source.data", "destination.data"]:
            original = (vault / path).read_bytes()
            (vault / path).write_bytes(b"changed")
            blocked = await submit_review(app, review, {})
            assert blocked.status_code == 409, blocked.text
            (vault / path).write_bytes(original)
        accepted = await submit_review(app, review, {})
        assert accepted.status_code == 200, accepted.text
        assert (await app.wait_task(accepted.json()["task"]["task_id"]))[
            "status"
        ] == "completed"
        assert (vault / "destination.data").read_bytes() == b"source bytes"
        server.tool_name = "file_write"
        server.arguments = {**args, "destination": "failed-copy.data"}
        server.fail_after_tool = True
        failed = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": "failed-copy",
                "prompt": "Copy this file.",
                "model": "code-tool-model",
            },
        )
        assert failed["task"]["status"] == "failed"
        assert not (vault / "failed-copy.data").exists()
        assert (vault / "source.data").read_bytes() == b"source bytes"


async def test_gmail_downloads_arbitrary_attachment_types_with_real_metadata(
    app: Application,
) -> None:
    vault = app.vault("GeneralAttachmentsVault")
    await app.start()
    from core.identity import use_execution_authority
    from core.tools.gmail import Gmail

    # Only Google HTTP responses are controlled; grants, service authorization,
    # metadata selection, decoding, numbered vault writes, and recovery stay real.
    with gmail_server(app) as provider:
        authority, connection, _, _ = configure_gmail(app, provider)
        tool = Gmail.get_tool(vault_path=str(vault))
        for suffix, mime in [
            (
                "docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
            (
                "pptx",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            ),
            (
                "xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            ("odt", "application/vnd.oasis.opendocument.text"),
            ("unknown", "application/octet-stream"),
        ]:
            content = b"arbitrary\x00\xffbytes-" + suffix.encode()
            provider.attachment_content = content
            message = message_payload("message-1", "thread-1")
            part = message["payload"]["parts"][1]
            part.update({"filename": f"report.{suffix}", "mimeType": mime})
            part["body"]["size"] = len(content)
            provider.queue(payload=message)
            with use_execution_authority(authority):
                result = await tool.function(
                    operation="download_attachment",
                    message_id="message-1",
                    attachment_id="attachment-1",
                    connection=connection.slug,
                    destination_path=f"Inbox/report.{suffix}",
                )
            assert isinstance(result, str), getattr(result, "metadata", result)
            downloaded = json.loads(result)
            assert (vault / downloaded["path"]).read_bytes() == content
            assert downloaded["attachment"]["filename"] == f"report.{suffix}"
            assert downloaded["attachment"]["media_type"] == mime
            assert "content" not in downloaded


async def test_copy_journal_failure_compensates_destination_without_changing_source(
    app: Application,
) -> None:
    import sqlite3

    vault = app.vault("CopyJournalFailureVault")
    app.write(vault, "source.data", "source bytes")
    app.write(vault, "destination.data", "retained destination")
    await app.start()
    with sqlite3.connect(app.root / "system/vault_state.db") as connection:
        connection.executescript(
            "CREATE TRIGGER reject_copy BEFORE INSERT ON vault_mutations WHEN NEW.operation = 'copy' BEGIN SELECT RAISE(ABORT, 'copy journal failure'); END;"
        )
    try:
        result = await BoundFileTools(app, vault).call(
            "file_write",
            operation="copy",
            path="source.data",
            destination="destination.data",
            overwrite=True,
        )
        assert result.metadata["status"] == "error"
        assert (vault / "source.data").read_bytes() == b"source bytes"
        assert (vault / "destination.data").read_bytes() == b"retained destination"
    finally:
        with sqlite3.connect(app.root / "system/vault_state.db") as connection:
            connection.execute("DROP TRIGGER reject_copy")
