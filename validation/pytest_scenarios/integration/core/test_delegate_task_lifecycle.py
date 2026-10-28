"""Managed delegate ownership and actual model deadline/cancellation outcomes."""

from __future__ import annotations

import asyncio

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.delegate_inputs import (
    delegate_provider,
    invoke_delegate,
)
from validation.pytest_scenarios.support.delegate_tasks import (
    managed_launch,
    wait_provider,
)

pytestmark = pytest.mark.scenario


async def test_managed_delegate_survives_parent_and_exposes_terminal_result(
    app: Application,
):
    vault = app.vault("ManagedDelegate")
    await app.start()
    from core.runtime.state import get_runtime_context

    with delegate_provider(app) as server:
        server.block = True
        parent_id, handle = await managed_launch(vault, "managed-child")
        child_id = handle.metadata["job_id"]
        await wait_provider(server)
        runtime = get_runtime_context()
        child = await runtime.task_coordinator.get_task(child_id)
        assert child is not None and not child.is_terminal
        assert child.detached_from_parent_lifecycle is True
        parent = await runtime.task_coordinator.get_task(parent_id)
        assert parent.is_terminal
        server.release.set()
        terminal = await runtime.task_coordinator.wait_for_tasks(
            [child_id], timeout_seconds=10, terminal_or_attention_only=True
        )
        child = terminal.snapshots[0]
        assert child.kind == "delegate" and child.parent_task_id == parent_id
        assert child.result["return_value"] == "DELEGATE_LOCAL_COMPLETE"


async def test_delegate_real_deadline_settles_failed_child(app: Application):
    vault = app.vault("DelegateDeadline")
    await app.start()
    from core.runtime.state import get_runtime_context

    update = app.request(
        "/api/system/settings/general/delegate_timeout_seconds",
        method="PUT",
        data={"value": "1"},
    )
    assert update.status_code == 200, update.text
    with delegate_provider(app) as server:
        server.block = True
        try:
            result = await invoke_delegate(
                vault,
                "delegate_timeout",
                {
                    "prompt": "Wait for bounded local provider",
                    "model": "code-tool-model",
                },
            )
            assert server.entered.is_set()
            assert result.metadata["status"] == "failed"
            assert result.metadata["failure_kind"] == "delegate_timeout"
            assert result.metadata["retryable"] is False
            jobs = await get_runtime_context().task_coordinator.list_tasks(
                kind="delegate"
            )
            assert all(job.is_terminal for job in jobs)
        finally:
            server.release.set()


async def test_cancelling_real_delegate_publishes_cancellation(app: Application):
    vault = app.vault("DelegateCancellation")
    await app.start()
    with delegate_provider(app) as server:
        server.block = True
        task = asyncio.create_task(
            invoke_delegate(
                vault,
                "delegate_parent_cancellation",
                {"prompt": "Wait for caller cancellation", "model": "code-tool-model"},
            )
        )
        try:
            await wait_provider(server)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            app.events.require(
                "delegate_cancelled",
                session_id="delegate_parent_cancellation",
                status="cancelled",
            )
        finally:
            server.release.set()
            await asyncio.gather(task, return_exceptions=True)
