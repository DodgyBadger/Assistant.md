"""Structured model-tool errors and Python authoring failures retain durable outcomes."""

from __future__ import annotations

import pytest
from pydantic_ai.messages import ToolReturn

from validation.scenarios.application import Application
from validation.scenarios.support.authoring_failure_inputs import (
    CAUGHT_PROBE_WORKFLOW,
    EXPLICIT_FAILURE_WORKFLOW,
    SESSION_SEARCH_FAILURE_WORKFLOW,
)
from validation.scenarios.support.session_ops_chat import (
    break_discovery_index,
    seed_session_ops_inputs,
)

pytestmark = pytest.mark.scenario


async def test_authoring_tool_failures_preserve_selected_and_uncaught_outcomes(
    app: Application,
) -> None:
    vault = app.vault("AuthoringToolFailureVault")
    app.evidence.register_secret("private search query")
    app.write(vault, "AssistantMD/Authoring/caught_probe.md", CAUGHT_PROBE_WORKFLOW)
    app.write(
        vault, "AssistantMD/Authoring/explicit_failure.md", EXPLICIT_FAILURE_WORKFLOW
    )
    app.write(
        vault,
        "AssistantMD/Authoring/session_search_failure.md",
        SESSION_SEARCH_FAILURE_WORKFLOW,
    )
    await app.start()
    from core.authoring.runtime import AuthoringMontyExecutionError
    from core.identity import LOCAL_USER_AUTHORITY
    from core.runtime.execution_tasks import ExecutionTaskSource
    from core.runtime.state import get_runtime_context
    from core.tools.file_read import FileRead

    runtime = get_runtime_context()
    model_result = FileRead.get_tool(str(vault)).function(operation="unknown")
    assert isinstance(model_result, ToolReturn)
    assert model_result.metadata["status"] == "error"
    caught = await runtime.workflow_governor.execute_workflow(
        global_id=f"{vault.name}/caught_probe",
        source=ExecutionTaskSource.API,
        authority=LOCAL_USER_AUTHORITY,
    )
    explicit = await runtime.workflow_governor.execute_workflow(
        global_id=f"{vault.name}/explicit_failure",
        source=ExecutionTaskSource.API,
        authority=LOCAL_USER_AUTHORITY,
    )
    assert caught.status == "skipped"
    assert "file_read operation 'unknown' failed" in str(caught.reason or "")
    assert explicit.status == "failed"
    assert explicit.success is False
    caught_run = runtime.workflow_run_store.get_latest_run(f"{vault.name}/caught_probe")
    explicit_run = runtime.workflow_run_store.get_latest_run(
        f"{vault.name}/explicit_failure"
    )
    assert caught_run is not None and caught_run.status == "skipped"
    assert explicit_run is not None and explicit_run.status == "failed"

    # Real malformed derived-index input replaces the original patched backend
    # failure. Its generic SQLite error does not prove private exception-payload
    # sanitization; private query confidentiality remains observable.
    seed_session_ops_inputs(runtime.chat_store, vault.name)
    break_discovery_index(app)
    checkpoint = app.events.checkpoint()
    with pytest.raises(AuthoringMontyExecutionError):
        await runtime.workflow_governor.execute_workflow(
            global_id=f"{vault.name}/session_search_failure",
            source=ExecutionTaskSource.API,
            authority=LOCAL_USER_AUTHORITY,
        )
    session_run = runtime.workflow_run_store.get_latest_run(
        f"{vault.name}/session_search_failure"
    )
    assert session_run is not None and session_run.status == "failed"
    reason = str(session_run.reason or "")
    assert "session_ops operation 'search_sessions' failed" in reason
    assert "SQL logic error" not in reason
    assert "private search query" not in reason
    app.events.require(
        "authoring_monty_execution_failed",
        after=checkpoint,
        workflow_id=f"{vault.name}/session_search_failure",
    )
    app.evidence.save_json(
        "authoring-failure-outcomes.json",
        {
            "caught_status": caught_run.status,
            "explicit_status": explicit_run.status,
            "uncaught_status": session_run.status,
            "uncaught_reason": reason,
            "parity_gap": "Original injected private exception-message sanitization not proved by generic real SQLite error",
        },
    )
