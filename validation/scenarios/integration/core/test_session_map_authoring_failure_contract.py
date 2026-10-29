"""Invalid citations remain provenance failures after real model retries."""

import json

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.session_map_author_http import configure_author
from validation.scenarios.support.session_map_authoring_failure_contract import (
    invalid_author,
)
from validation.scenarios.support.session_map_batch15_inputs import author_request

pytestmark = pytest.mark.scenario


async def test_exhausted_author_provenance_is_safe_and_preserves_checkpoint(
    app: Application,
) -> None:
    vault = app.vault("MapAuthorFailureContractVault")
    with invalid_author() as server:
        await app.start()
        from pydantic_ai import ModelRetry
        from pydantic_ai.exceptions import UnexpectedModelBehavior

        from core.identity import LOCAL_USER_AUTHORITY
        from core.memory.session_map.checkpoints import (
            build_session_map_context_message,
        )
        from core.memory.session_map.models import (
            SessionMapDraft,
            SessionMapProvenanceError,
        )
        from core.memory.session_map.service import run_session_map_authoring
        from core.runtime.execution_tasks import ExecutionTaskKind
        from core.runtime.state import get_runtime_context

        configure_author(app, server)
        runtime = get_runtime_context()
        store = runtime.chat_store
        session = "session-map-task"
        store.ensure_session(session, vault.name, owner_principal_id="local-user")
        message = build_session_map_context_message(SessionMapDraft())
        store.add_context_checkpoint(
            session_id=session,
            vault_name=vault.name,
            checkpoint_id="existing-map",
            checkpoint_kind="session_map",
            source="validation",
            message_count_before=0,
            last_message_sequence_index=-1,
            summary_message=message,
            replacement_history=[message],
            metadata={"map": SessionMapDraft().model_dump(mode="json")},
        )
        before = store.list_context_checkpoints(session, vault.name)
        checkpoint = app.events.checkpoint()
        with pytest.raises(SessionMapProvenanceError) as failure:
            await run_session_map_authoring(
                author_request(vault.name), authority=LOCAL_USER_AUTHORITY
            )
        assert isinstance(failure.value.__cause__, UnexpectedModelBehavior)
        retry = failure.value.__cause__.__cause__
        assert isinstance(retry, ModelRetry) and isinstance(
            retry.__cause__, SessionMapProvenanceError
        )
        assert len(server.requests) > 1
        assert store.list_context_checkpoints(session, vault.name) == before
        tasks = await runtime.task_coordinator.list_tasks(
            kind=ExecutionTaskKind.SESSION_MAP_AUTHORING.value
        )
        assert (
            len(tasks) == 1
            and tasks[0].status == "failed"
            and tasks[0].terminal_error_type == "SessionMapProvenanceError"
        )
        events = [
            row
            for row in app.events.select(after=checkpoint)
            if row["data"].get("task_id") == tasks[0].task_id
        ]
        assert any(
            row["name"] == "session_map_authoring_failed"
            and row["data"]["reason"] == "invalid_provenance"
            for row in events
        )
        assert not any(
            row["name"] == "session_map_authoring_completed" for row in events
        )
        activity = app.request("/api/system/activity-log", params={"limit": 1000})
        assert activity.status_code == 200
        rows = [
            row["data"]
            for row in activity.json()["entries"]
            if row.get("data", {}).get("task_id") == tasks[0].task_id
            and row["data"].get("event") == "session_map_authoring_failed"
        ]
        assert (
            len(rows) == 1
            and rows[0]["reason"] == "invalid_provenance"
            and rows[0]["error_type"] == "SessionMapProvenanceError"
        )
        assert "Unsupported citation" not in json.dumps(
            rows
        ) and "99-99" not in json.dumps(rows)
        assert "Unsupported citation" not in str(failure.value)
        app.evidence.save_json("invalid-author-requests.json", server.requests)


async def test_exhausted_schema_failure_is_not_reclassified_as_provenance(
    app: Application,
) -> None:
    vault = app.vault("MapAuthorSchemaFailureVault")
    with invalid_author() as server:
        server.mode = "schema"
        await app.start()
        from pydantic_ai.exceptions import UnexpectedModelBehavior

        from core.identity import LOCAL_USER_AUTHORITY
        from core.memory.session_map.service import run_session_map_authoring
        from core.runtime.execution_tasks import ExecutionTaskKind
        from core.runtime.state import get_runtime_context

        configure_author(app, server)
        with pytest.raises(UnexpectedModelBehavior):
            await run_session_map_authoring(
                author_request(vault.name), authority=LOCAL_USER_AUTHORITY
            )
        assert len(server.requests) > 1
        tasks = await get_runtime_context().task_coordinator.list_tasks(
            kind=ExecutionTaskKind.SESSION_MAP_AUTHORING.value
        )
        assert len(tasks) == 1 and tasks[0].status == "failed"
        assert tasks[0].terminal_error_type == "UnexpectedModelBehavior"
