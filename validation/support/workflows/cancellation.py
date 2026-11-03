"""Observe real workflow write entry and cancellation while its file lock is held."""

import asyncio

from validation.support.application import Application


async def wait_for_write_attempt(
    app: Application, checkpoint: int, vault_name: str
) -> None:
    async with asyncio.timeout(10):
        while not app.events.contains(
            "tool_invoked",
            after=checkpoint,
            fields={"tool": "file_write", "operation": "write", "vault": vault_name},
        ):
            await asyncio.sleep(0.01)


async def require_cancellation_pending(app: Application, task_id: str) -> None:
    # The actual mutation lock remains held throughout this interval. Publishing
    # terminal state now would permit rollback to miss the still-running write.
    try:
        async with asyncio.timeout(0.5):
            terminal = await app.wait_task(task_id)
    except TimeoutError:
        return
    raise AssertionError(
        "Workflow cancellation became terminal before its blocked write settled: "
        f"{terminal['status']}"
    )
