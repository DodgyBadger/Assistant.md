"""Explicit retained proposal records exercise the historical read-only API."""

from __future__ import annotations

import json
import sqlite3

from validation.scenarios.application import Application


def seed_historical_proposal(app: Application, vault_name: str) -> tuple[str, str]:
    from core.runtime.state import get_runtime_context

    session_id = "edit_proposal_artifacts_session"
    artifact_ref = "edit-proposals/historical-artifact"
    get_runtime_context().chat_store.ensure_session(
        session_id, vault_name, owner_principal_id="local-user"
    )
    proposal = {
        "artifact_ref": artifact_ref,
        "artifact_kind": "file_edit_proposal",
        "vault_name": vault_name,
        "session_id": session_id,
        "title": "Historical proposal",
        "summary": "Retained for chat rendering.",
        "status": "pending",
        "edits": [
            {
                "edit_id": "historical-edit",
                "operation": "replace_text",
                "path": "README.md",
                "original_text": "Before",
                "replacement_text": "After",
            }
        ],
    }
    with sqlite3.connect(app.root / "system/chat_sessions.db") as connection:
        connection.execute(
            "INSERT INTO chat_edit_proposals (artifact_ref, session_id, vault_name, status, proposal_json) VALUES (?, ?, ?, ?, ?)",
            (artifact_ref, session_id, vault_name, "pending", json.dumps(proposal)),
        )
    return session_id, artifact_ref
