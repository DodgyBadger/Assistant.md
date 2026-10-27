"""Validate directory mutations exclude descendant file mutations."""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import core.vault_state.file_mutations as file_mutations
from core.vault_state.file_mutations import VaultMutationRejected
from validation.support.base_scenario import BaseScenario


class VaultDirectoryMutationConcurrencyScenario(BaseScenario):
    """Validate a directory move cannot overlap a child-file edit."""

    async def test_scenario(self) -> None:
        vault = self.create_vault("VaultDirectoryMutationConcurrencyVault")
        self.create_file(vault, "source/child.md", "before\n")
        await self.start_system()

        source = (vault / "source").resolve()
        destination = (vault / "destination").resolve()
        original_replace = file_mutations.os.replace
        move_started = threading.Event()
        allow_move = threading.Event()

        def paused_replace(old: str | Path, new: str | Path) -> None:
            if Path(old).resolve() == source and Path(new).resolve() == destination:
                move_started.set()
                assert allow_move.wait(2), "Timed out waiting to release directory move"
            original_replace(old, new)

        file_mutations.os.replace = paused_replace
        try:
            move_task = asyncio.create_task(
                asyncio.to_thread(
                    file_mutations.move_vault_directory,
                    vault_path=vault,
                    path="source",
                    destination="destination",
                )
            )
            assert await asyncio.to_thread(move_started.wait, 1)

            edit_task = asyncio.create_task(
                asyncio.to_thread(
                    file_mutations.replace_vault_file_content,
                    vault_path=vault,
                    path="source/child.md",
                    content="after\n",
                    operation="replace_text",
                )
            )
            await asyncio.sleep(0.05)
            assert (
                not edit_task.done()
            ), "A child-file mutation must wait for an active directory move"

            allow_move.set()
            await move_task
            try:
                await edit_task
            except (FileNotFoundError, VaultMutationRejected) as exc:
                if isinstance(exc, VaultMutationRejected):
                    assert exc.code == "file_not_found"
            else:
                raise AssertionError("The stale child-file mutation should be rejected")

            assert not source.exists()
            assert (destination / "child.md").read_text(encoding="utf-8") == "before\n"
        finally:
            allow_move.set()
            file_mutations.os.replace = original_replace
            await self.stop_system()
            self.teardown_scenario()
