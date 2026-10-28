"""Cancelling a genuinely queued managed delegate records its normalized result."""

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.delegate_inputs import delegate_provider
from validation.scenarios.support.delegate_queue_inputs import wait_queued
from validation.scenarios.support.delegate_tasks import (
    managed_launch,
    wait_provider,
)

pytestmark = pytest.mark.scenario


async def test_queued_managed_delegate_cancellation_retains_result_and_reason(
    app: Application,
):
    vault = app.vault("DelegateQueue")
    await app.start()
    from core.runtime.state import get_runtime_context

    response = app.request(
        "/api/system/settings/general/max_concurrent_delegates",
        method="PUT",
        data={"value": "1"},
    )
    assert response.status_code == 200
    runtime = get_runtime_context()
    with delegate_provider(app) as server:
        server.block = True
        _, first = await managed_launch(vault, "delegate_managed_holder")
        first_id = first.metadata["job_id"]
        await wait_provider(server)
        _, second = await managed_launch(vault, "delegate_managed_queued_cancel")
        second_id = second.metadata["job_id"]
        try:
            queued = await wait_queued(runtime, second_id)
            assert not queued.is_terminal
            await runtime.task_coordinator.cancel_task(
                second_id, reason="validation_queued_delegate_cancel"
            )
            terminal = await runtime.task_coordinator.wait_for_tasks(
                [second_id],
                timeout_seconds=5,
                terminal_or_attention_only=True,
                wake_on_attention=False,
            )
            assert terminal.snapshots[0].result["metadata"]["status"] == "cancelled"
            app.events.require(
                "delegate_cancelled",
                task_id=second_id,
                session_id="delegate_managed_queued_cancel",
                workflow_id="delegate_managed_queued_cancel",
                detached_from_parent_lifecycle=True,
                reason="validation_queued_delegate_cancel",
                mode="managed",
                status="cancelled",
            )
        finally:
            server.release.set()
            await runtime.task_coordinator.cancel_task(
                first_id, reason="validation_holder_cleanup"
            )
            await runtime.task_coordinator.wait_for_tasks(
                [first_id],
                timeout_seconds=5,
                terminal_or_attention_only=True,
                wake_on_attention=False,
            )
