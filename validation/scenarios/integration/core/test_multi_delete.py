"""Native deletion shares preflight, recursive recovery, and standard chat review."""

from __future__ import annotations

import sqlite3

import pytest

from validation.support.application import Application
from validation.support.chat.chat_tasks import chat_turn
from validation.support.chat.code_tool_chat import (
    code_tool_provider,
    configure_provider,
)
from validation.support.chat.inline_edit_reviews import request_review, submit_review
from validation.support.vault.file_tools import BoundFileTools, vault_tree_snapshot
from validation.support.vault.vault_recovery import execute_rollback, preview_rollback

pytestmark = pytest.mark.scenario


async def test_native_multi_delete_normalizes_and_recovers_complete_trees(
    app: Application,
) -> None:
    vault = app.vault("NativeDeleteVault")
    app.write(vault, "Archive/a.md", "old note\n")
    (vault / "Archive/bytes.bin").write_bytes(b"\x00\xffbinary")
    (vault / "Archive/Empty/Deep").mkdir(parents=True)
    app.write(vault, "Loose.md", "loose note")
    app.write(vault, "Keep.md", "keep")
    await app.start()
    tools = BoundFileTools(app, vault)
    assert "confirm_path" not in tools.write_schema["properties"]
    deleted = await tools.call(
        "file_write",
        operation="delete",
        paths=["./Archive", "Archive/a.md", "Archive", "Loose.md"],
    )
    assert deleted.metadata["status"] == "completed", deleted.return_value
    assert deleted.metadata["paths"] == ["Archive", "Loose.md"]
    assert not (vault / "Archive").exists() and not (vault / "Loose.md").exists()
    assert (vault / "Keep.md").read_text() == "keep"
    activity_id = deleted.metadata["activity_id"]
    preview = preview_rollback(app, vault.name, activity_id)
    assert preview["can_rollback"] is True, preview
    assert execute_rollback(app, vault.name, activity_id, preview).status_code == 200
    assert (vault / "Archive/a.md").read_text() == "old note\n"
    assert (vault / "Archive/bytes.bin").read_bytes() == b"\x00\xffbinary"
    assert (vault / "Archive/Empty/Deep").is_dir()
    single = await tools.call("file_write", operation="delete", path="Archive")
    assert single.metadata["status"] == "completed" and not (vault / "Archive").exists()


@pytest.mark.parametrize(
    "arguments",
    [
        {"paths": ["Keep.md", "Missing.md"]},
        {"paths": ["Keep.md", "."]},
        {"paths": ["Keep.md", "./"]},
        {"paths": ["Keep.md", "AssistantMD"]},
        {"paths": []},
        {"path": "Keep.md", "paths": ["Keep.md"]},
        {"paths": ["Keep.md", "../outside"]},
        {"path": "Keep.md", "confirm_path": "Keep.md"},
    ],
)
async def test_multi_delete_preflight_preserves_all_targets(
    app: Application, arguments: dict
) -> None:
    vault = app.vault("MultiDeletePreflightVault")
    app.write(vault, "Keep.md", "unchanged")
    (vault / "Empty/Deep").mkdir(parents=True)
    await app.start()
    # Establish vault identity and finish background initialization before taking
    # the immutable preservation snapshot.
    from validation.support.vault.vault_state import wait_for_event

    await wait_for_event(
        app,
        0,
        name="vault_state_background_refresh_completed",
        expected={"reason": "startup"},
    )
    before = vault_tree_snapshot(vault)
    result = await BoundFileTools(app, vault).call(
        "file_write", operation="delete", **arguments
    )
    assert result.metadata["status"] != "completed"
    assert vault_tree_snapshot(vault) == before


async def test_multi_delete_review_covers_effective_selection_and_tree_changes(
    app: Application,
) -> None:
    vault = app.vault("MultiDeleteReviewVault")
    app.write(vault, "Archive/a.md", "reviewed")
    (vault / "Archive/Empty").mkdir()
    app.write(vault, "Loose.md", "also reviewed")
    with code_tool_provider() as server:
        await app.start()
        configure_provider(app, server)
        arguments = {
            "operation": "delete",
            "paths": ["Archive", "Archive/a.md", "Loose.md", "Archive"],
        }
        review = await request_review(
            app, server, vault.name, "review-delete", arguments
        )
        assert [row["path"] for row in review["approvals"][0]["deletion_targets"]] == [
            "Archive",
            "Loose.md",
        ]
        assert review["approvals"][0]["deletion_targets"][0]["kind"] == "directory"
        assert (vault / "Archive/a.md").read_text() == "reviewed"
        forbidden = await submit_review(app, review, {"paths": ["Loose.md"]})
        assert forbidden.status_code == 400
        for change in ("content", "new_file", "empty_directory"):
            if change == "content":
                (vault / "Archive/a.md").write_text("changed")
            elif change == "new_file":
                app.write(vault, "Archive/new.md", "new")
            else:
                (vault / "Archive/NewEmpty").mkdir()
            blocked = await submit_review(app, review, {})
            assert (
                blocked.status_code == 409
                and blocked.json()["error"] == "DeferredReviewTargetConflict"
            )
            assert (vault / "Loose.md").exists()
            if change == "content":
                (vault / "Archive/a.md").write_text("reviewed")
            elif change == "new_file":
                (vault / "Archive/new.md").unlink()
            else:
                (vault / "Archive/NewEmpty").rmdir()
        accepted = await submit_review(app, review, {})
        assert accepted.status_code == 200, accepted.text
        assert (await app.wait_task(accepted.json()["task"]["task_id"]))[
            "status"
        ] == "completed"
        assert not (vault / "Archive").exists() and not (vault / "Loose.md").exists()


async def test_multi_delete_normal_mode_denial_and_scripts_keep_standard_policy(
    app: Application,
) -> None:
    vault = app.vault("MultiDeletePolicyVault")
    for name in ("Normal", "Denied", "Scripted"):
        app.write(vault, f"{name}/a.md", name)
        app.write(vault, f"{name}/b.md", name)
    with code_tool_provider() as server:
        await app.start()
        configure_provider(app, server)
        server.tool_name = "file_write"
        server.arguments = {
            "operation": "delete",
            "paths": ["Normal/a.md", "Normal/b.md"],
        }
        normal = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": "normal-delete",
                "prompt": "Delete the selected notes.",
                "model": "code-tool-model",
            },
        )
        assert not any(
            event.get("event") == "review_required" for event in normal["events"]
        )
        assert (
            not (vault / "Normal/a.md").exists()
            and not (vault / "Normal/b.md").exists()
        )
        review = await request_review(
            app,
            server,
            vault.name,
            "denied-delete",
            {"operation": "delete", "paths": ["Denied/a.md", "Denied/b.md"]},
        )
        denied = await app.request_async(
            f"/api/vaults/{vault.name}/chat/denied-delete/deferred-reviews/{review['artifact_ref']}/submit",
            "POST",
            {
                "decisions": [
                    {
                        "tool_call_id": review["approvals"][0]["tool_call_id"],
                        "decision": "deny",
                    }
                ]
            },
        )
        assert denied.status_code == 200
        await app.wait_task(denied.json()["task"]["task_id"])
        assert (vault / "Denied/a.md").exists() and (vault / "Denied/b.md").exists()
        server.tool_name = "code_execution"
        server.arguments = {
            "code": 'for path in ["Scripted/a.md", "Scripted/b.md"]:\n    await file_write(operation="delete", path=path)\n"done"'
        }
        scripted = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": "script-delete",
                "prompt": "Run the deletion script.",
                "model": "code-tool-model",
                "chat_mode": "inline_edit",
            },
        )
        assert not any(
            event.get("event") == "review_required" for event in scripted["events"]
        )
        assert (
            not (vault / "Scripted/a.md").exists()
            and not (vault / "Scripted/b.md").exists()
        )


@pytest.mark.parametrize("block_recovery", [False, True])
async def test_multi_delete_storage_failure_reports_compensation(
    app: Application,
    block_recovery: bool,
) -> None:
    vault = app.vault("MultiDeleteFailureVault")
    app.write(vault, "Tree/a.md", "retained bytes")
    (vault / "Tree/Empty").mkdir()
    await app.start()
    assert app.request(f"/api/vaults/{vault.name}/favorites").status_code == 200
    with sqlite3.connect(app.root / "system/vault_state.db") as connection:
        condition = "NEW.target_kind = 'directory'"
        if block_recovery:
            condition += " OR NEW.operation = 'rollback_activity'"
        connection.executescript(
            f"CREATE TRIGGER reject_multi_delete BEFORE INSERT ON vault_mutations WHEN {condition} BEGIN SELECT RAISE(ABORT, 'directory journal failure'); END;"
        )
    try:
        failed = await BoundFileTools(app, vault).call(
            "file_write", operation="delete", paths=["Tree"]
        )
        assert failed.metadata["status"] == "error"
        assert failed.metadata["recovery_status"] == (
            "partial_or_uncertain" if block_recovery else "restored"
        )
        assert failed.metadata["activity_id"]
        assert (vault / "Tree/Empty").is_dir()
        if failed.metadata["recovery_status"] == "restored":
            assert (vault / "Tree/a.md").read_text() == "retained bytes"
    finally:
        with sqlite3.connect(app.root / "system/vault_state.db") as connection:
            connection.execute("DROP TRIGGER reject_multi_delete")
    if block_recovery:
        activity_id = failed.metadata["activity_id"]
        preview = preview_rollback(app, vault.name, activity_id)
        assert preview["can_rollback"] is True, preview
        assert (
            execute_rollback(app, vault.name, activity_id, preview).status_code == 200
        )
        assert (vault / "Tree/a.md").read_text() == "retained bytes"


@pytest.mark.parametrize("with_file", [True, False])
async def test_failed_chat_recovers_recursive_deletion_and_empty_directories(
    app: Application,
    with_file: bool,
) -> None:
    vault = app.vault("MultiDeleteTaskFailureVault")
    if with_file:
        app.write(vault, "Tree/a.md", "restore after failed chat")
    (vault / "Tree/Empty/Deep").mkdir(parents=True)
    with code_tool_provider() as server:
        await app.start()
        configure_provider(app, server)
        server.tool_name = "file_write"
        server.arguments = {"operation": "delete", "paths": ["Tree"]}
        server.fail_after_tool = True
        result = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": "delete-then-fail",
                "prompt": "Delete the selected folder.",
                "model": "code-tool-model",
            },
        )
        assert result["task"]["status"] == "failed"
        if with_file:
            assert (vault / "Tree/a.md").read_text() == "restore after failed chat"
        assert (vault / "Tree/Empty/Deep").is_dir()
