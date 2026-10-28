"""Actual chat reads and writes enforce inline review edit and path policy."""

from __future__ import annotations

import json

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_tasks import chat_turn
from validation.scenarios.support.code_tool_chat import (
    code_tool_provider,
    configure_provider,
)
from validation.scenarios.support.inline_edit_reviews import (
    request_review,
    session_tool_results,
    submit_review,
)

pytestmark = pytest.mark.scenario


async def test_normal_writes_and_inline_reads_execute_without_review(
    app: Application,
) -> None:
    vault = app.vault("InlineReadPolicyVault")
    app.write(vault, "notes/source.md", "INLINE_READ_SOURCE")
    with code_tool_provider() as server:
        await app.start()
        configure_provider(app, server)
        server.tool_name = "file_write"
        server.arguments = {
            "operation": "write",
            "path": "notes/normal.md",
            "content": "NORMAL_WRITE",
        }
        normal = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": "normal-write",
                "prompt": "Write the note.",
                "model": "code-tool-model",
            },
        )
        assert normal["terminal_event"]["event"] == "done"
        assert not any(
            event.get("event") == "review_required" for event in normal["events"]
        )
        assert (vault / "notes/normal.md").read_text() == "NORMAL_WRITE"
        server.tool_name = "file_read"
        server.arguments = {"operation": "read", "path": "notes/source.md"}
        inline = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": "inline-read",
                "prompt": "Read the note.",
                "model": "code-tool-model",
                "chat_mode": "inline_edit",
            },
        )
        assert inline["terminal_event"]["event"] == "done"
        assert not any(
            event.get("event") == "review_required" for event in inline["events"]
        )
        assert "INLINE_READ_SOURCE" in inline["text"]
        app.events.require("tool_invoked", tool="file_read", operation="read")


async def test_inline_mkdir_review_preserves_operation_and_policy(
    app: Application,
) -> None:
    vault = app.vault("FileOpsInlineEditPolicyVault")
    with code_tool_provider() as server:
        await app.start()
        configure_provider(app, server)
        review = await request_review(
            app,
            server,
            vault.name,
            "mkdir-review",
            {"operation": "mkdir", "path": "Notes/Typo", "overwrite": False},
        )
        assert not (vault / "Notes/Typo").exists()
        for override in ({"operation": "delete"}, {"overwrite": True}):
            rejected = await submit_review(app, review, override)
            assert rejected.status_code == 400, rejected.text
            assert rejected.json()["error"] == "DeferredReviewImmutableArgument"
        approved = await submit_review(app, review, {"path": "Notes/Correct"})
        assert approved.status_code == 200, approved.text
        task = await app.wait_task(approved.json()["task"]["task_id"])
        assert task["status"] == "completed"
        assert (vault / "Notes/Correct").is_dir()
        assert not (vault / "Notes/Typo").exists()
        results = session_tool_results(vault.name, "mkdir-review")
        assert len(results) == 1
        assert json.loads(results[0].result_metadata_json)["status"] == "completed"
        app.events.require("tool_invoked", tool="file_write", operation="mkdir")


@pytest.mark.parametrize("escape_path", ["../mkdir-escape", "Notes/../../mkdir-escape"])
async def test_inline_mkdir_overrides_cannot_escape_vault(
    app: Application, escape_path: str
) -> None:
    vault = app.vault("InlineMkdirEscapeVault")
    with code_tool_provider() as server:
        await app.start()
        configure_provider(app, server)
        review = await request_review(
            app,
            server,
            vault.name,
            "mkdir-review",
            {"operation": "mkdir", "path": "Notes/Typo", "overwrite": False},
        )
        approved = await submit_review(app, review, {"path": escape_path})
        assert approved.status_code == 200, approved.text
        task = await app.wait_task(approved.json()["task"]["task_id"])
        assert task["status"] == "completed"
        results = session_tool_results(vault.name, "mkdir-review")
        assert len(results) == 1
        assert json.loads(results[0].result_metadata_json)["status"] == "error"
        assert not (vault / "Notes/Typo").exists()
        assert not (vault.parent / "mkdir-escape").exists()


async def test_inline_write_review_keeps_target_immutable(app: Application) -> None:
    vault = app.vault("InlineWriteTargetVault")
    with code_tool_provider() as server:
        await app.start()
        configure_provider(app, server)
        review = await request_review(
            app,
            server,
            vault.name,
            "write-review",
            {"operation": "write", "path": "Fixed.md", "content": "REVIEWED_WRITE"},
        )
        rejected = await submit_review(app, review, {"path": "Other.md"})
        assert rejected.status_code == 400, rejected.text
        assert rejected.json()["error"] == "DeferredReviewImmutableArgument"
        assert not (vault / "Fixed.md").exists()
        assert not (vault / "Other.md").exists()
