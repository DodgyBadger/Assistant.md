"""Keep bounded SDK operations owned after client deadlines or cancellation."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any


class BrowserOperations[T]:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[T]] = set()

    async def run(self, operation: Coroutine[Any, Any, T]) -> T:
        if len(self._tasks) >= 16:
            operation.close()
            raise RuntimeError("Browser IPC capacity is unavailable")
        task = asyncio.create_task(operation)
        self._tasks.add(task)
        task.add_done_callback(self._completed)
        return await asyncio.shield(task)

    def _completed(self, task: asyncio.Task[T]) -> None:
        self._tasks.discard(task)
        if not task.cancelled():
            task.exception()

    @property
    def pending(self) -> bool:
        return bool(self._tasks)

    async def settled(self) -> bool:
        if self._tasks:
            _, pending = await asyncio.wait(self._tasks, timeout=1)
            return not pending
        return True


async def finish_cleanup(task: asyncio.Task[None]) -> None:
    """Finish an independently bounded cleanup owner before propagating Stop."""
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            cancellation = cancellation or error
        except Exception:
            break
    try:
        task.result()
    except BaseException as error:
        if cancellation is not None:
            cancellation.add_note(f"Browser cleanup failed: {type(error).__name__}")
            raise cancellation from error
        raise
    if cancellation is not None:
        raise cancellation
