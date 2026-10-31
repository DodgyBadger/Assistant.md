"""Public task snapshots expose delegate queue admission."""

import asyncio


async def wait_queued(runtime, task_id):
    async with asyncio.timeout(10):
        while True:
            task = await runtime.task_coordinator.get_task(task_id)
            if task and task.metadata.get("queue_reason"):
                return task
            await asyncio.sleep(0.01)
