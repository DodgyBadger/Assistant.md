"""Durable canonical chat history, tool results and explicit transcript export."""

from __future__ import annotations

import json

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.authoring import run_assembly
from validation.scenarios.support.chat_persistence import (
    HISTORY_CODE,
    PERSISTENCE_CONTEXT,
    message_rows,
    seed_uncommitted_tool_call,
    tool_rows,
)
from validation.scenarios.support.chat_tasks import chat_turn
from validation.scenarios.support.settings import general_settings

pytestmark = pytest.mark.scenario


async def test_chat_session_persistence_and_export(app: Application) -> None:
    vault = app.vault("ChatSessionPersistenceContractVault")
    other_vault = app.vault("ChatSessionPersistenceOtherVault")
    # This explicit input keeps context assembly scoped to canonical history;
    # standing-file context composition has its own scenario contract.
    app.write(
        vault, "AssistantMD/Authoring/persistence-context.md", PERSISTENCE_CONTEXT
    )
    await app.start()
    from core.authoring.runtime import WorkflowAuthoringHost
    from core.constants import ASSISTANTMD_ROOT_DIR, CHAT_SESSIONS_DIR

    tool_choices = general_settings(app)["disabled_tools"]["editor"]["choices"]
    policy = app.request(
        "/api/system/settings/general/disabled_tools",
        method="PUT",
        data={
            "value": json.dumps(
                [
                    choice["value"]
                    for choice in tool_choices
                    if choice["value"] != "chat_history_compact"
                ]
            )
        },
    )
    assert policy.status_code == 200, policy.text

    session_id = "chat_session_persistence_contract_session"
    first_prompt = "Check this session's compaction status and answer briefly."
    follow_up_prompt = "Check the compaction status again and answer with the result."
    # The built-in deterministic model calls the real tool with its default status
    # operation. No agent configuration or tool implementation is replaced.
    turn = await chat_turn(
        app,
        {
            "vault_name": vault.name,
            "prompt": first_prompt,
            "session_id": session_id,
            "model": "test",
            "context_template": "persistence-context.md",
        },
    )
    assert turn["session_id"] == session_id
    assert turn["terminal_event"]["event"] == "done"
    assert turn["task"]["status"] == "completed"

    mismatch = app.request(
        "/api/chat/tasks",
        method="POST",
        data={
            "vault_name": other_vault.name,
            "prompt": "Reject a different vault.",
            "session_id": session_id,
            "model": "test",
            "context_template": "persistence-context.md",
        },
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["error"] == "ChatSessionVaultMismatch"
    assert mismatch.json()["details"]["bound_vault"] == vault.name
    assert mismatch.json()["details"]["requested_vault"] == other_vault.name

    messages = message_rows(app, vault.name, session_id)
    assert len(messages) >= 2
    first_rows = [
        row for row in messages if row[1] == "user" and row[2] == first_prompt
    ]
    assert len(first_rows) == 1
    assert "run_id" in json.loads(first_rows[0][3])
    events = tool_rows(app, vault.name, session_id)
    assert [row[0] for row in events] == ["call", "result"]
    assert all(row[1] == "chat_history_compact" for row in events)
    result_text = events[1][2]
    status = json.loads(result_text)
    assert status["session_id"] == session_id
    assert status["vault_name"] == vault.name
    assert status["already_compacted"] is False
    assert events[1][3] is None
    assert any(result_text in str(row[2] or "") for row in messages)
    # TestModel serializes tool output into its deterministic final answer.
    assert json.loads(turn["text"])["chat_history_compact"] == result_text
    call_id = events[1][4]
    detail = app.request(
        f"/api/chat/sessions/{session_id}/tools/{call_id}",
        params={"vault_name": vault.name},
    )
    assert detail.status_code == 200
    assert detail.json()["result_text"] == result_text
    assert [event["event_type"] for event in detail.json()["events"]] == [
        "call",
        "result",
    ]
    assert (
        app.request(
            f"/api/chat/sessions/{session_id}/tools/{call_id}",
            params={"vault_name": other_vault.name},
        ).status_code
        == 409
    )
    assert (
        app.request(
            f"/api/chat/sessions/{session_id}/tools/missing-call",
            params={"vault_name": vault.name},
        ).status_code
        == 404
    )

    transcript_dir = vault / ASSISTANTMD_ROOT_DIR / CHAT_SESSIONS_DIR
    assert not (transcript_dir / f"{session_id}.md").exists()
    titled = app.request(
        f"/api/chat/sessions/{session_id}/title",
        method="PATCH",
        data={"vault_name": vault.name, "title": "Session Probe Title"},
    )
    assert titled.status_code == 200
    workspace = vault / "Projects/ForkProbe"
    workspace.mkdir(parents=True)
    selected = app.request(
        f"/api/chat/sessions/{session_id}/workspace",
        method="PATCH",
        data={"vault_name": vault.name, "path": "Projects/ForkProbe"},
    )
    assert selected.status_code == 200
    assert selected.json()["exists"] is True
    workspace.rmdir()
    missing = app.request(
        f"/api/chat/sessions/{session_id}", params={"vault_name": vault.name}
    )
    assert missing.status_code == 200
    assert missing.json()["workspace"] == {
        "path": "Projects/ForkProbe",
        "exists": False,
    }

    exported = app.request(
        f"/api/chat/sessions/{session_id}/export",
        method="POST",
        data={"vault_name": vault.name},
    )
    assert exported.status_code == 200
    assert exported.json()["filename"] == f"{session_id} - Session_Probe_Title.md"
    transcript = transcript_dir / exported.json()["filename"]
    text = transcript.read_text(encoding="utf-8")
    assert "**User:**" in text and "**Assistant:**" in text
    assert first_prompt in text
    assert "[chat_history_compact]" not in text

    follow_up = await chat_turn(
        app,
        {
            "vault_name": vault.name,
            "prompt": follow_up_prompt,
            "session_id": session_id,
            "model": "test",
            "context_template": "persistence-context.md",
        },
    )
    assert follow_up["terminal_event"]["event"] == "done"
    assert follow_up["task"]["status"] == "completed"
    second_export = app.request(
        f"/api/chat/sessions/{session_id}/export",
        method="POST",
        data={"vault_name": vault.name},
    )
    assert second_export.status_code == 200
    assert second_export.json()["filename"] == exported.json()["filename"]
    assert follow_up_prompt in transcript.read_text(encoding="utf-8")
    assert len(list(transcript_dir.glob(f"{session_id}*.md"))) == 1
    messages = message_rows(app, vault.name, session_id)
    assert sum(row[1] == "user" and row[2] == first_prompt for row in messages) == 1
    assert sum(row[1] == "user" and row[2] == follow_up_prompt for row in messages) == 1

    seed_uncommitted_tool_call(app, vault.name, session_id)
    after_orphan = app.request(
        f"/api/chat/sessions/{session_id}", params={"vault_name": vault.name}
    )
    assert after_orphan.status_code == 200
    visible_calls = {call["tool_call_id"] for call in after_orphan.json()["tool_calls"]}
    assert visible_calls
    assert "cancelled-turn-call" not in visible_calls

    await app.controller.restart_system()
    workflow_id = f"{vault.name}/chat/{session_id}"
    host = WorkflowAuthoringHost(
        workflow_id=workflow_id,
        vault_path=str(vault),
        session_key=session_id,
        chat_session_id=session_id,
        message_history=[],
    )
    checkpoint = app.events.checkpoint()
    history = await run_assembly(
        app, workflow_id=workflow_id, code=HISTORY_CODE, host=host
    )
    app.events.require(
        "authoring_retrieve_history_completed",
        after=checkpoint,
        workflow_id=workflow_id,
        source="sqlite_chat_sessions",
    )
    assert history["source"] == "sqlite_chat_sessions"
    assert history["history_source"] == "chat_messages"
    assert history["history_message_filter"] == "all"
    assert history["item_count"] >= 2
    assert history["last_content"]
    assert history["user_prompts"].count(first_prompt) == 1
    assert history["user_prompts"].count(follow_up_prompt) == 1
