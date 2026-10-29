"""Private exception projection is coupled to actual transcript-search failure."""

from __future__ import annotations

import json

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.session_ops_chat import (
    call_session_ops,
    configure_provider,
    seed_session_ops_inputs,
    session_ops_provider,
)
from validation.scenarios.support.transcript_error_privacy import (
    PRIVATE_EXCEPTION,
    PRIVATE_QUERY,
    break_transcript_content,
    project_private_error,
)

pytestmark = pytest.mark.scenario


async def test_transcript_search_uses_payload_free_failure_diagnostics(
    app: Application,
):
    vault = app.vault("TranscriptErrorPrivacy")
    (vault / "Projects/WorkspaceA").mkdir(parents=True)
    app.evidence.register_secret(PRIVATE_EXCEPTION, PRIVATE_QUERY)
    with session_ops_provider() as server:
        await app.start()
        from core.runtime.state import get_runtime_context

        # A private exception is a controlled input to the actual application
        # projector. No SQLite or retrieval implementation is replaced.
        projected = project_private_error()
        assert PRIVATE_EXCEPTION not in json.dumps(projected)
        assert projected["traceback"]
        assert "transcript_error_privacy.py:" in projected["traceback"]
        assert " in project_private_error" in projected["traceback"]
        assert str(app.root) not in projected["traceback"]
        configure_provider(app, server)
        seed_session_ops_inputs(get_runtime_context().chat_store, vault.name)
        break_transcript_content(app)
        failed = await call_session_ops(
            app,
            server,
            vault.name,
            {
                "operation": "search_transcript",
                "session_id": "source",
                "query": PRIVATE_QUERY,
            },
        )
        metadata = json.loads(failed.result_metadata_json or "{}")
        assert metadata["status"] == "failed"
        assert metadata["error_type"] == "OperationalError"
        assert metadata["operation"] == "search_transcript"
        assert PRIVATE_QUERY not in failed.result_text
        assert PRIVATE_EXCEPTION not in failed.result_text
        activity = app.request("/api/system/activity-log", params={"limit": 200})
        assert activity.status_code == 200
        failures = [
            entry["data"]
            for entry in activity.json()["entries"]
            if entry.get("data", {}).get("event") == "session_ops_failed"
            and entry.get("data", {}).get("tool_call_id") == failed.tool_call_id
        ]
        assert len(failures) == 1
        data = failures[0]
        assert data["status"] == "failed" and data["error_type"] == "OperationalError"
        assert data["operation"] == "search_transcript"
        assert data["run_id"] and data["issue"]
        assert data["error"] == projected["error"]
        assert " in _search_authorized_sessions" in data["traceback"]
        assert 1 <= len(data["traceback"].splitlines()) <= 12
        assert str(app.root) not in data["traceback"]
        app.events.require(
            "tool_invoked", tool="session_ops", operation="search_transcript"
        )
        assert PRIVATE_QUERY not in json.dumps(data)
        assert PRIVATE_EXCEPTION not in json.dumps(data)
        # Inspect actual raw output before the evidence exporter redacts secrets.
        raw = (app.root / "system/activity.log").read_text()
        assert PRIVATE_EXCEPTION not in raw and PRIVATE_QUERY not in raw
        app.evidence.save_json(
            "transcript-error-projection.json",
            {
                "controlled_private_projection": projected,
                "actual_failure_diagnostics": data,
            },
        )
        assert server.calls
