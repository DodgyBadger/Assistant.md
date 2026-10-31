"""Observe live background resources without naming manager implementation methods."""

from __future__ import annotations

import asyncio


def live_background_tasks() -> set[asyncio.Task]:
    current = asyncio.current_task()
    return {
        task for task in asyncio.all_tasks() if task is not current and not task.done()
    }
