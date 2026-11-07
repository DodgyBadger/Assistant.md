"""Real file/review/attachment boundaries exposed by branch hardening review."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from validation.support.application import Application
from validation.support.chat.code_tool_chat import (
    code_tool_provider,
    configure_provider,
)
from validation.support.chat.inline_edit_reviews import (
    request_review,
    submit_review,
)
from validation.support.vault.file_tools import BoundFileTools

pytestmark = pytest.mark.scenario


async def test_delete_normalizes_separator_syntax_before_target_preflight(
    app: Application,
) -> None:
    vault = app.vault("DeleteSeparatorVault")
    app.write(vault, "Folder/note.md", "intended target")
    (vault / "Folder\\note.md").write_text("literal name must survive")
    await app.start()
    result = await BoundFileTools(app, vault).call(
        "file_write", operation="delete", path="Folder\\note.md"
    )
    assert result.metadata["status"] == "completed", result.return_value
    assert result.metadata["paths"] == ["Folder/note.md"]
    assert not (vault / "Folder/note.md").exists()
    assert (vault / "Folder\\note.md").read_text() == "literal name must survive"


async def test_delete_preflight_rejects_unrepresentable_tree_names(
    app: Application,
) -> None:
    from core.vault_state.file_mutations import VaultMutationRejected
    from core.vault_state.path_deletion import prepare_vault_deletion

    vault = app.vault("DeleteLiteralNameVault")
    app.write(vault, "Tree/keep.md", "must survive")
    (vault / "Tree/literal\\name.md").write_bytes(b"literal bytes")
    await app.start()
    with pytest.raises(VaultMutationRejected) as failure:
        prepare_vault_deletion(vault_path=vault, sources=["Tree"])
    assert failure.value.code == "invalid_path"
    assert (vault / "Tree/keep.md").read_text() == "must survive"
    assert (vault / "Tree/literal\\name.md").read_bytes() == b"literal bytes"


async def test_deletion_review_lock_wait_does_not_block_event_loop(
    app: Application,
) -> None:
    from core.vault_state.file_mutations import vault_directory_mutation_lock

    vault = app.vault("ResponsiveDeleteReviewVault")
    app.write(vault, "Tree/note.md", "reviewed")
    with code_tool_provider() as server:
        await app.start()
        configure_provider(app, server)
        review = await request_review(
            app,
            server,
            vault.name,
            "responsive-delete",
            {"operation": "delete", "path": "Tree"},
        )
        entered, release = threading.Event(), threading.Event()
        lock_timed_out = []
        loop = asyncio.get_running_loop()
        checkpoint = app.events.checkpoint()

        def hold_lock():
            with vault_directory_mutation_lock(vault):
                entered.set()
                deadline = time.monotonic() + 5
                while not app.events.contains(
                    "chat_deferred_review_validation_started",
                    fields={"artifact_ref": review["artifact_ref"]},
                    after=checkpoint,
                ):
                    if time.monotonic() >= deadline or release.wait(0.01):
                        lock_timed_out.append(True)
                        return
                # Release requires the actual event loop to execute this callback.
                # A bounded guard prevents the blocking implementation deadlocking
                # the test; success is logical release, not elapsed time.
                loop.call_soon_threadsafe(release.set)
                lock_timed_out.append(not release.wait(timeout=2))

        holder = threading.Thread(target=hold_lock)
        holder.start()
        assert await asyncio.to_thread(entered.wait, 5)
        submitted = asyncio.create_task(submit_review(app, review, {}))
        try:
            response = await submitted
        finally:
            release.set()
            await asyncio.to_thread(holder.join, 5)
        assert not holder.is_alive()
        assert lock_timed_out == [
            False
        ], "Review scan blocked the event loop waiting for a filesystem lock"
        assert response.status_code == 200, response.text
        await app.wait_task(response.json()["task"]["task_id"])


async def test_delete_separator_normalization_cannot_bypass_vault_identity(
    app: Application,
) -> None:
    from validation.support.vault.vault_state import wait_for_event

    vault = app.vault("DeleteIdentitySeparatorVault")
    await app.start()
    await wait_for_event(
        app,
        0,
        name="vault_state_background_refresh_completed",
        expected={"reason": "startup"},
    )
    identity = vault / "AssistantMD/vault.yaml"
    original = identity.read_bytes()
    literal = vault / "AssistantMD\\vault.yaml"
    literal.write_bytes(original)
    result = await BoundFileTools(app, vault).call(
        "file_write", operation="delete", path="AssistantMD\\vault.yaml"
    )
    assert (
        result.metadata.get("error_type") == "protected_vault_identity"
    ), result.return_value
    assert identity.read_bytes() == original
    assert literal.read_bytes() == original
