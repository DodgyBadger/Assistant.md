"""Distinct real author-provider failures retain safe correlated diagnostics."""

import json
from dataclasses import replace

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.model_failures import (
    PRIVATE_VALUES,
    rejecting_provider,
)
from validation.scenarios.support.session_map_author_http import configure_author
from validation.scenarios.support.session_map_batch15_inputs import (
    author_request,
)

pytestmark = pytest.mark.scenario


async def test_distinct_session_map_author_provider_failures(app: Application) -> None:
    vault = app.vault("SessionMapAuthorFailureVault")
    app.evidence.register_secret(*PRIVATE_VALUES)
    with rejecting_provider() as (server, requests):
        await app.start()
        from pydantic_ai.exceptions import ModelHTTPError

        from core.identity import LOCAL_USER_AUTHORITY
        from core.memory.session_map.service import run_session_map_authoring
        from core.runtime.execution_tasks import ExecutionTaskKind
        from core.runtime.state import get_runtime_context

        configure_author(app, server)
        base = author_request(vault.name)
        runtime = get_runtime_context()
        for suffix in ("one", "two"):
            session = f"session-map-failure-{suffix}"
            request = replace(
                base,
                session_id=session,
                new_evidence=(replace(base.new_evidence[0], session_id=session),),
            )
            with pytest.raises(ModelHTTPError):
                await run_session_map_authoring(request, authority=LOCAL_USER_AUTHORITY)
        assert len(requests) == 2
        tasks = await runtime.task_coordinator.list_tasks(
            kind=ExecutionTaskKind.SESSION_MAP_AUTHORING.value
        )
        assert len(tasks) == 2
        for task in tasks:
            assert (
                task.status == "failed" and task.terminal_error_type == "ModelHTTPError"
            )
            assert all(
                private not in str(task.terminal_reason) for private in PRIVATE_VALUES
            )
            response = app.request(f"/api/tasks/{task.task_id}")
            assert response.status_code == 200 and all(
                private not in response.text for private in PRIVATE_VALUES
            )
        activity = app.request("/api/system/activity-log", params={"limit": 1000})
        assert activity.status_code == 200
        ids = {task.task_id for task in tasks}
        rows = [
            row["data"]
            for row in activity.json()["entries"]
            if row.get("tag") == "session-map-authoring"
            and row.get("data", {}).get("event") == "session_map_authoring_failed"
            and row["data"].get("task_id") in ids
        ]
        assert len(rows) == 2 and len({row["issue"] for row in rows}) == 2
        assert all(
            row["status"] == "failed"
            and row["reason"] == "authoring_failed"
            and row["error_type"] == "ModelHTTPError"
            and row["http_status"] == 400
            and row["provider_error_type"] == "invalid_request_error"
            and row["provider_error_code"] == "billing_error"
            and len(row["provider_error_message"]) <= 500
            and row["error"]
            == "Provider request failed (HTTP 400): " + row["provider_error_message"]
            for row in rows
        )
        assert all(private not in json.dumps(rows) for private in PRIVATE_VALUES)
