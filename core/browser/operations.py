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

    async def settled(self) -> bool:
        if self._tasks:
            _, pending = await asyncio.wait(self._tasks, timeout=1)
            return not pending
        return True
