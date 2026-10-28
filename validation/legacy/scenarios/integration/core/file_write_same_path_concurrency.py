"""Validate concurrent text edits to one file do not overwrite sibling changes."""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import core.vault_state.file_operations as file_operations
from validation.support.base_scenario import BaseScenario


class FileWriteSamePathConcurrencyScenario(BaseScenario):
    """Validate same-path read-modify-write operations are serialized."""

    async def test_scenario(self) -> None:
        vault = self.create_vault("FileWriteSamePathConcurrencyVault")
        self.create_file(vault, "notes/concurrent.md", "alpha\nbeta\ngamma\n")
        target = vault / "notes/concurrent.md"
        await self.start_system()

        original_replace = file_operations.replace_vault_file_content
        active_writes = 0
        max_active_writes = 0
        counter_lock = threading.Lock()
        start_barrier = threading.Barrier(3)

        def observed_replace(**kwargs):
            nonlocal active_writes, max_active_writes
            with counter_lock:
                active_writes += 1
                max_active_writes = max(max_active_writes, active_writes)
            try:
                time.sleep(0.05)
                return original_replace(**kwargs)
            finally:
                with counter_lock:
                    active_writes -= 1

        def replace(old_text: str, new_text: str):
            start_barrier.wait()
            return file_operations.replace_text_vault_file_operation(
                vault_path=vault,
                path="notes/concurrent.md",
                old_text=old_text,
                new_text=new_text,
                count=1,
            )

        file_operations.replace_vault_file_content = observed_replace
        try:
            results = await asyncio.gather(
                asyncio.to_thread(replace, "alpha", "one"),
                asyncio.to_thread(replace, "beta", "two"),
                asyncio.to_thread(replace, "gamma", "three"),
            )
            assert all(result.metadata["status"] == "completed" for result in results)
            assert max_active_writes == 1, "Same-path text mutations must not overlap"
            assert target.read_text(encoding="utf-8") == "one\ntwo\nthree\n"
        finally:
            file_operations.replace_vault_file_content = original_replace
            await self.stop_system()
            self.teardown_scenario()
