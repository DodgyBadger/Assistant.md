"""Real directory mutation under a controlled concurrent child-edit workload."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event


async def move_with_competing_edit(vault):
    from core.vault_state.activity import VaultActivityContext, use_vault_activity
    from core.vault_state.file_mutations import (
        VaultMutationRejected,
        move_vault_directory,
        replace_vault_file_content,
        vault_directory_mutation_lock,
    )
    from core.vault_state.service import VaultStateService

    acquired, edit_started, release = Event(), Event(), Event()
    move_context = VaultActivityContext(
        activity_id="move-contention",
        kind="explorer",
        source="api",
        scope=None,
        label="Move directory",
    )
    edit_context = VaultActivityContext(
        activity_id="edit-contention",
        kind="explorer",
        source="api",
        scope=None,
        label="Edit moved file",
    )

    def move():
        with (
            use_vault_activity(move_context),
            vault_directory_mutation_lock(
                vault, vault / "source", vault / "destination"
            ),
        ):
            acquired.set()
            assert release.wait(10)
            result = move_vault_directory(
                vault_path=vault, path="source", destination="destination"
            )
        VaultStateService().finish_activity(
            activity_id="move-contention", status="completed"
        )
        return result

    def edit():
        with use_vault_activity(edit_context):
            edit_started.set()
            try:
                return replace_vault_file_content(
                    vault_path=vault,
                    path="source/child.md",
                    content="after\n",
                    operation="replace_text",
                )
            except (VaultMutationRejected, FileNotFoundError) as exc:
                return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        move_future = pool.submit(move)
        try:
            assert await asyncio.to_thread(acquired.wait, 5)
            edit_future = pool.submit(edit)
            assert await asyncio.to_thread(edit_started.wait, 5)
            await asyncio.sleep(0.1)
            assert not edit_future.done()
        finally:
            release.set()
        move_result, edit_result = await asyncio.gather(
            asyncio.to_thread(move_future.result, 15),
            asyncio.to_thread(edit_future.result, 15),
        )
    return move_result, edit_result
