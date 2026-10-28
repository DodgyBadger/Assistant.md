"""Real child model setup preserves layered instructions and structured failures."""

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.delegate_inputs import (
    delegate_provider,
    invoke_delegate,
)

pytestmark = pytest.mark.scenario


async def test_delegate_real_model_request_preserves_instruction_layer_order(
    app: Application,
):
    vault = app.vault("DelegateInstructionLayers")
    await app.start()
    from core.constants import DELEGATE_FLIGHT_CARD

    with delegate_provider(app) as server:
        result = await invoke_delegate(
            vault,
            "delegate_instruction_layers",
            {
                "prompt": "Return bounded child output",
                "instructions": "TASK-SPECIFIC-DELEGATE-INSTRUCTIONS",
                "model": "code-tool-model",
            },
        )
        assert result.return_value == "DELEGATE_LOCAL_COMPLETE"
        # Instruction ordering is the contract; compare the production constant
        # with the actual model transport input rather than editorial prose.
        instructions = "\n".join(
            str(row.get("content", ""))
            for row in server.calls[0]["messages"]
            if row["role"] in {"system", "developer"}
        )
        assert instructions.index(DELEGATE_FLIGHT_CARD.strip()) < instructions.index(
            "TASK-SPECIFIC-DELEGATE-INSTRUCTIONS"
        )


async def test_delegate_invalid_real_model_configuration_returns_structured_failure(
    app: Application,
):
    vault = app.vault("DelegateInitializationFailure")
    await app.start()
    result = await invoke_delegate(
        vault,
        "delegate_init_failure",
        {
            "prompt": "Exercise missing configured model",
            "model": "nonexistent-delegate-validation-model",
        },
    )
    assert result.metadata["failure_kind"] == "delegate_internal"
    assert result.metadata["status"] == "failed"
    app.events.require(
        "delegate_failed",
        workflow_id="delegate_init_failure",
        session_id="delegate_init_failure",
        failure_kind="delegate_internal",
        mode="blocking",
        status="failed",
    )


async def test_delegate_without_chat_parent_uses_runtime_default(app: Application):
    vault = app.vault("DelegateRuntimeDefault")
    await app.start()
    from core.runtime.state import get_runtime_context

    configured = app.request(
        "/api/system/settings/general/default_model",
        method="PUT",
        data={"value": "test"},
    )
    assert configured.status_code == 200, configured.text
    result = await invoke_delegate(
        vault, "delegate_runtime_default", {"prompt": "Return bounded child output"}
    )
    assert result.metadata["status"] == "completed"
    tasks = await get_runtime_context().task_coordinator.list_tasks(kind="delegate")
    child = next(
        task
        for task in tasks
        if task.metadata.get("session_id") == "delegate_runtime_default"
    )
    assert child.status == "completed"
    app.events.require(
        "delegate_started",
        session_id="delegate_runtime_default",
        model_source="runtime_default",
        model="default",
    )
    app.events.require("delegate_completed", session_id="delegate_runtime_default")
