"""Corrupt stored maps identify their authorized source without leaking text."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pydantic_ai import ModelRetry

from validation.support.application import Application

if TYPE_CHECKING:
    from core.authoring.contracts import ScriptToolResult

pytestmark = pytest.mark.scenario

_PRIVATE_MAP_TEXT = "private-malformed-map-value-76b3f8"


async def _lookup_map(
    vault: Path, *, operation: str, session_id: str
) -> ScriptToolResult:
    from core.authoring.helpers.runtime_common import (
        invoke_bound_tool,
        normalize_tool_result,
    )
    from core.authoring.shared.tool_binding import resolve_tool_binding
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.runtime.buffers import BufferStore

    with use_execution_authority(LOCAL_USER_AUTHORITY):
        binding = resolve_tool_binding(["session_ops"], vault_path=str(vault))
        raw = await invoke_bound_tool(
            binding.tool_functions[0],
            tool_name="session_ops",
            arguments={"operation": operation, "session_id": session_id},
            run_buffers=BufferStore(),
            session_buffers=BufferStore(),
            session_id="map-review-caller",
            vault_name=vault.name,
        )
    return normalize_tool_result("session_ops", raw, vault_path=str(vault))


async def test_corrupt_map_diagnostics_identify_authorized_checkpoint_without_text(
    app: Application,
) -> None:
    vault = app.vault("MapDiagnosticVault")
    await app.start()
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from core.memory.session_map.checkpoints import build_session_map_context_message
    from core.memory.session_map.models import (
        SessionMapDraft,
        SessionMapTrajectory,
        SourceRange,
    )
    from core.runtime.state import get_runtime_context

    store = get_runtime_context().chat_store
    store.ensure_session(
        "map-review-caller", vault.name, owner_principal_id="local-user"
    )
    store.ensure_session(
        "map-review-target", vault.name, owner_principal_id="local-user"
    )
    store.add_messages(
        "map-review-target",
        vault.name,
        [ModelRequest(parts=[UserPromptPart(content="Preserve this discussion.")])],
    )
    draft = SessionMapDraft(
        trajectory=SessionMapTrajectory(
            text="A valid checkpoint before storage corruption.",
            sources=(SourceRange(start=0, end=0),),
        ),
    )
    context = build_session_map_context_message(draft)
    store.add_context_checkpoint(
        session_id="map-review-target",
        vault_name=vault.name,
        checkpoint_id="map-review-corrupt-checkpoint",
        checkpoint_kind="session_map",
        source="validation",
        message_count_before=1,
        last_message_sequence_index=0,
        summary_message=context,
        replacement_history=[context],
        metadata={"map": draft.model_dump(mode="json")},
    )
    # A malformed typed value would appear in a raw Pydantic validation error.
    # The real loader and tool must suppress that value and its exception chain.
    app.evidence.register_secret(_PRIVATE_MAP_TEXT)
    with sqlite3.connect(app.root / "system/chat_sessions.db") as connection:
        connection.execute(
            "UPDATE chat_compaction_checkpoints SET metadata_json = ? WHERE checkpoint_id = ?",
            (
                json.dumps(
                    {"map": {"schema_version": 3, "trajectory": _PRIVATE_MAP_TEXT}}
                ),
                "map-review-corrupt-checkpoint",
            ),
        )
    for operation in ("list_session_maps", "get_session_map"):
        result = await _lookup_map(
            vault, operation=operation, session_id="map-review-target"
        )
        assert result.metadata["status"] == "failed"
        assert _PRIVATE_MAP_TEXT not in str(result.return_value)
        activity = app.request("/api/system/activity-log", params={"limit": 200})
        assert activity.status_code == 200
        failures = [
            row["data"]
            for row in activity.json()["entries"]
            if row.get("data", {}).get("event") == "session_ops_failed"
            and row["data"].get("operation") == operation
        ]
        assert len(failures) == 1
        failure = failures[0]
        assert failure["checkpoint_id"] == "map-review-corrupt-checkpoint"
        assert failure["checkpoint_session_id"] == "map-review-target"
        assert failure["session_id"] == "map-review-caller"
        assert failure["status"] == "failed" and failure["issue"]
        assert _PRIVATE_MAP_TEXT not in activity.text
    assert _PRIVATE_MAP_TEXT not in (app.root / "system/activity.log").read_text()

    # Diagnostic source identities are only added after authorization succeeds.
    store.ensure_session(
        "map-review-hidden", vault.name, owner_principal_id="other-user"
    )
    with pytest.raises(ModelRetry) as hidden:
        await _lookup_map(
            vault, operation="list_session_maps", session_id="map-review-hidden"
        )
    assert "map-review-hidden" not in str(hidden.value)
    activity = app.request("/api/system/activity-log", params={"limit": 200})
    assert "map-review-hidden" not in activity.text
