"""Actual task-runner workloads launching production delegate tools."""

from __future__ import annotations

import asyncio

from validation.scenarios.support.delegate_inputs import invoke_delegate


async def managed_launch(vault, session_id):
    from core.identity import LOCAL_USER_AUTHORITY
    from core.runtime.execution_tasks import ExecutionTaskKind, ExecutionTaskSource
    from core.runtime.state import get_runtime_context
    from core.runtime.task_runner import ExecutionTaskSpec

    async def launch(parent):
        result = await invoke_delegate(
            vault,
            session_id,
            {
                "prompt": "Run the bounded managed child",
                "model": "code-tool-model",
                "mode": "managed",
            },
        )
        return parent.task_id, result

    return await get_runtime_context().task_runner.run_inline(
        ExecutionTaskSpec(
            kind=ExecutionTaskKind.CHAT,
            scope=f"delegate:{session_id}",
            source=ExecutionTaskSource.SYSTEM,
            label="delegate parent",
            authority=LOCAL_USER_AUTHORITY,
        ),
        launch,
    )


async def wait_provider(server):
    assert await asyncio.to_thread(server.entered.wait, 10)
