"""Shared runtime background task spawning."""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import Callable, Coroutine
from typing import Any


class RuntimeBackgroundSpawner:
    """Spawn runtime-owned background coroutines on the configured event loop."""

    def __init__(
        self,
        *,
        background_loop: asyncio.AbstractEventLoop | None = None,
        background_tasks: set[asyncio.Task[Any]] | None = None,
    ) -> None:
        self._background_loop = background_loop
        self._background_tasks = background_tasks

    def spawn(
        self,
        coroutine_factory: Callable[[], Coroutine[Any, Any, Any]],
    ) -> None:
        """Schedule a coroutine factory and register the created task for shutdown."""

        def _spawn() -> None:
            background_task: asyncio.Task[Any] = asyncio.create_task(
                coroutine_factory(),
                context=contextvars.Context(),
            )
            if self._background_tasks is not None:
                self._background_tasks.add(background_task)
                background_task.add_done_callback(self._background_tasks.discard)

        current_loop = asyncio.get_running_loop()
        target_loop = self._background_loop
        if (
            target_loop is not None
            and target_loop.is_running()
            and target_loop is not current_loop
        ):
            target_loop.call_soon_threadsafe(_spawn, context=contextvars.Context())
            return
        _spawn()
