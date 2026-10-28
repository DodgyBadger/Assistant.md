"""Session-map authoring runs a real structured model call inside its task."""

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.session_map_author_http import (
    author_provider,
    configure_author,
)
from validation.pytest_scenarios.support.session_map_batch15_inputs import (
    author_request,
)

pytestmark = pytest.mark.scenario


async def test_session_map_authoring_task_provenance_and_lifecycle(
    app: Application,
) -> None:
    vault = app.vault("SessionMapTaskVault")
    with author_provider() as server:
        await app.start()
        from core.identity import LOCAL_USER_AUTHORITY
        from core.memory.session_map.service import run_session_map_authoring
        from core.runtime.execution_tasks import ExecutionTaskKind, ExecutionTaskSource
        from core.runtime.state import get_runtime_context

        configure_author(app, server)
        request = author_request(vault.name)
        checkpoint = app.events.checkpoint()
        result = await run_session_map_authoring(
            request, authority=LOCAL_USER_AUTHORITY, source=ExecutionTaskSource.SYSTEM
        )
        assert (
            result.model_alias,
            result.thinking,
            result.prompt_contract_version,
            result.new_evidence_count,
            result.source_history_revision,
            result.evidence_source_start,
            result.evidence_source_end,
        ) == ("session-map-author", None, "eviction-map-v11", 1, 2, 10, 11)
        assert (
            result.draft.trajectory.sources[0].start == 10
            and result.draft.trajectory.sources[0].end == 11
        )
        assert len(server.requests) == 1
        assert server.payloads[0]["new_evidence_envelopes"][0]["source_range"] == {
            "start": 10,
            "end": 11,
        }
        task = await get_runtime_context().task_coordinator.get_task(result.task_id)
        assert (
            task is not None
            and task.kind == ExecutionTaskKind.SESSION_MAP_AUTHORING.value
            and task.status == "completed"
            and task.scope == "chat_session:session-map-task"
        )
        events = [
            row["name"]
            for row in app.events.select(after=checkpoint)
            if row["data"].get("task_id") == result.task_id
        ]
        assert (
            "session_map_authoring_started" in events
            and "session_map_authoring_completed" in events
        )
        activity = app.request("/api/system/activity-log", params={"limit": 1000})
        assert activity.status_code == 200
        rows = [
            row["data"]
            for row in activity.json()["entries"]
            if row.get("tag") == "session-map-authoring"
            and row.get("data", {}).get("task_id") == result.task_id
        ]
        assert {row["status"] for row in rows} == {"running", "completed"}
        assert all(
            row["source"] == "system" and row["parent_task_id"] is None for row in rows
        )
        app.evidence.save_json("session-map-author-requests.json", server.requests)
