"""Typed tool-history input boundaries and real recoverable tool-validation retries."""

from __future__ import annotations

from datetime import datetime

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.authoring import run_assembly
from validation.scenarios.support.chat_tasks import chat_turn
from validation.scenarios.support.code_tool_chat import configure_provider
from validation.scenarios.support.retry_provider import retry_provider
from validation.scenarios.support.tool_history_inputs import (
    TOOL_HISTORY_BATCH_CODE,
    TOOL_HISTORY_ORPHAN_CODE,
    _batch_history,
    _orphan_return_history,
    assert_input_integrity,
)

pytestmark = pytest.mark.scenario


async def test_authoring_history_preserves_batches_and_reports_orphans(
    app: Application,
) -> None:
    vault = app.vault("ToolHistoryIntegrityVault")
    await app.start()
    from core.authoring.runtime import WorkflowAuthoringHost

    # Malformed and valid histories are explicit provider-shaped boundary inputs.
    assert_input_integrity()
    workflow_id = f"{vault.name}/tool-history-integrity"
    batch_host = WorkflowAuthoringHost(
        workflow_id=workflow_id,
        vault_path=str(vault),
        reference_date=datetime(2026, 6, 12, 12),
        session_key="tool_history_batch_session",
        chat_session_id="tool_history_batch_session",
        message_history=_batch_history(),
    )
    checkpoint = app.events.checkpoint()
    batch = await run_assembly(
        app, workflow_id=workflow_id, code=TOOL_HISTORY_BATCH_CODE, host=batch_host
    )
    app.events.require(
        "authoring_retrieve_history_completed",
        after=checkpoint,
        workflow_id=workflow_id,
        item_count=3,
        tool_history_integrity_status="ok",
        multi_call_batch_count=1,
        multi_return_batch_count=1,
    )
    assert batch["batch_item_indexes"] == [1]
    assert batch["exchange_count"] == 2
    assert batch["assembled_batch_role_seen"] is True
    orphan_host = WorkflowAuthoringHost(
        workflow_id=workflow_id,
        vault_path=str(vault),
        reference_date=datetime(2026, 6, 12, 12),
        session_key="tool_history_orphan_session",
        chat_session_id="tool_history_orphan_session",
        message_history=_orphan_return_history(),
    )
    checkpoint = app.events.checkpoint()
    orphan = await run_assembly(
        app, workflow_id=workflow_id, code=TOOL_HISTORY_ORPHAN_CODE, host=orphan_host
    )
    app.events.require(
        "authoring_retrieve_history_tool_integrity_issue",
        after=checkpoint,
        workflow_id=workflow_id,
        status="issues",
    )
    assert orphan["issue_codes"] == ["orphan_tool_return"]


async def test_actual_tool_retry_retains_admissible_canonical_history(
    app: Application,
) -> None:
    vault = app.vault("ToolRetryIntegrityVault")
    with retry_provider("tool_retry") as provider:
        await app.start()
        configure_provider(app, provider)
        from core.chat.tool_history import analyze_tool_history
        from core.runtime.state import get_runtime_context

        checkpoint = app.events.checkpoint()
        result = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": "tool-retry-history",
                "prompt": "Try session discovery, correcting invalid input.",
                "model": "code-tool-model",
            },
        )
        app.evidence.save_json("tool-retry-provider-requests.json", provider.calls)
        assert result["terminal_event"]["event"] == "done"
        assert result["task"]["status"] == "completed"
        assert len(provider.calls) == 3
        invocations = app.events.select("tool_invoked", after=checkpoint)
        assert (
            sum(event["data"].get("tool") == "session_ops" for event in invocations)
            == 2
        )
        messages = get_runtime_context().chat_store.get_history(
            "tool-retry-history", vault.name
        )
        integrity = analyze_tool_history(messages)
        assert integrity.ok, integrity.to_dict()
        assert (integrity.tool_call_count, integrity.tool_return_count) == (2, 1)
        app.evidence.save_json("actual-retry-integrity.json", integrity.to_dict())
