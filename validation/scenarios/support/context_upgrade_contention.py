"""Public execution-task admission observations for compaction contention."""

import asyncio


async def wait_manual_compaction(runtime, session):
    from core.runtime.execution_tasks import chat_session_scope

    async with asyncio.timeout(10):
        while True:
            tasks = await runtime.task_coordinator.list_tasks(
                kind="history_compaction",
                scope=chat_session_scope(session),
                include_terminal=False,
            )
            if tasks:
                return tasks[0]
            await asyncio.sleep(0.01)
