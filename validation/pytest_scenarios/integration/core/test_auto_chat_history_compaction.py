"""A completed chat runs automatic compaction as an owned child task."""

import json

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.chat_persistence import PERSISTENCE_CONTEXT
from validation.pytest_scenarios.support.chat_tasks import chat_turn
from validation.pytest_scenarios.support.compaction_journeys import (
    add_turn,
    author_payload,
    compaction_provider,
    configure_compaction,
)

pytestmark = pytest.mark.scenario


async def test_automatic_compaction_is_child_of_chat(app: Application) -> None:
    vault = app.vault("AutoChatHistoryCompactionVault")
    app.write(vault, "AssistantMD/Authoring/full_history.md", PERSISTENCE_CONTEXT)
    with compaction_provider() as server:
        await app.start()
        from core.constants import CHAT_HISTORY_RECOVERY_CARD_PREAMBLE
        from core.runtime.execution_tasks import ExecutionTaskKind
        from core.runtime.state import get_runtime_context

        settings = app.request("/api/system/settings/general")
        assert settings.status_code == 200
        assert (
            next(row for row in settings.json() if row["key"] == "compaction_type")[
                "value"
            ]
            == "auto"
        )
        configure_compaction(app, server, mode="auto")
        metadata = app.request("/api/metadata")
        assert metadata.status_code == 200
        disabled = app.request(
            "/api/system/settings/general/disabled_tools",
            method="PUT",
            data={
                "value": json.dumps([row["name"] for row in metadata.json()["tools"]])
            },
        )
        assert disabled.status_code == 200
        runtime = get_runtime_context()
        store = runtime.chat_store
        session = "auto_chat_history_compaction_session"
        store.ensure_session(session, vault.name, owner_principal_id="local-user")
        add_turn(
            store, vault.name, session, "Earlier user turn.", "Earlier assistant turn."
        )
        result = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "prompt": "Trigger automatic compaction after this short answer.",
                "session_id": session,
                "model": "test",
                "context_template": "full_history.md",
            },
        )
        assert result["terminal_event"]["event"] == "done"
        payload = author_payload(server, 0)
        assert (
            len(payload["older_history"]) == 2
            and len(payload["retained_recent_history"]) == 2
            and payload["user_focus"] is None
        )
        record = store.get_session_metadata(session, vault.name)["last_compaction"]
        assert (
            record["source"],
            record["trigger"],
            record["reason"],
            record["prompt_contract_version"],
            record["compaction_type"],
        ) == ("system", "auto", "token_threshold", "recovery-card-v5", "auto")
        assert (
            record["author_model"],
            record["author_thinking"],
            record["compaction_high_watermark_tokens"],
            record["compaction_retained_turns"],
        ) == ("session-ops-model", "low", 1, 1)
        checkpoint = store.get_latest_compaction_checkpoint(session, vault.name)
        assert checkpoint is not None
        checkpoint_metadata = json.loads(checkpoint.metadata_json or "{}")
        assert (
            checkpoint_metadata["trigger"] == "auto"
            and checkpoint_metadata["reason"] == "token_threshold"
        )
        effective = store.get_stored_messages(session, vault.name)
        assert len(effective) == 3 and effective[0].role == "system"
        assert (
            "AssistantMD compacted chat history" in effective[0].content_text
            and CHAT_HISTORY_RECOVERY_CARD_PREAMBLE in effective[0].content_text
        )
        assert store.get_message_count(session, vault.name, mode="raw") == 4
        tasks = await runtime.task_coordinator.list_tasks(
            kind=ExecutionTaskKind.HISTORY_COMPACTION.value
        )
        assert len(tasks) == 1
        task = tasks[0]
        assert (
            task.source == "system"
            and task.parent_task_id == result["task"]["task_id"]
            and task.status == "completed"
        )
        assert (
            task.result is not None
            and task.result["strategy"] == "recovery_card"
            and task.result["checkpoint_id"] == checkpoint.checkpoint_id
        )
        app.evidence.save_json("auto-compaction-author-requests.json", server.requests)


async def test_automatic_compaction_commit_failure_is_safe(app: Application) -> None:
    import sqlite3

    vault = app.vault("AutoCompactionCommitFailureVault")
    app.write(vault, "AssistantMD/Authoring/full_history.md", PERSISTENCE_CONTEXT)
    private = "PRIVATE-AUTO-COMPACTION-SENTINEL"
    app.evidence.register_secret(private)
    with compaction_provider() as server:
        await app.start()
        from core.runtime.execution_tasks import ExecutionTaskKind
        from core.runtime.state import get_runtime_context

        configure_compaction(app, server, mode="auto")
        metadata = app.request("/api/metadata")
        assert metadata.status_code == 200
        disabled = app.request(
            "/api/system/settings/general/disabled_tools",
            method="PUT",
            data={
                "value": json.dumps([row["name"] for row in metadata.json()["tools"]])
            },
        )
        assert disabled.status_code == 200
        runtime = get_runtime_context()
        store = runtime.chat_store
        session = "auto-compaction-commit-failure"
        store.ensure_session(session, vault.name, owner_principal_id="local-user")
        add_turn(
            store, vault.name, session, "Earlier user turn.", "Earlier assistant turn."
        )
        with sqlite3.connect(app.root / "system/chat_sessions.db") as conn:
            conn.execute(
                "CREATE TRIGGER reject_validation_checkpoint BEFORE INSERT ON chat_compaction_checkpoints WHEN NEW.session_id = 'auto-compaction-commit-failure' BEGIN SELECT RAISE(ABORT, 'PRIVATE-AUTO-COMPACTION-SENTINEL'); END"
            )
        result = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "prompt": "Exercise automatic compaction commit failure.",
                "session_id": session,
                "model": "test",
                "context_template": "full_history.md",
            },
        )
        assert result["terminal_event"]["event"] == "done"
        assert server.requests
        assert store.get_latest_context_checkpoint(session, vault.name) is None
        assert store.get_message_count(session, vault.name, mode="raw") == 4
        tasks = await runtime.task_coordinator.list_tasks(
            kind=ExecutionTaskKind.HISTORY_COMPACTION.value
        )
        assert len(tasks) == 1
        failed = tasks[0]
        assert (
            failed.status == "failed"
            and failed.parent_task_id == result["task"]["task_id"]
        )
        assert failed.terminal_error_type == "IntegrityError" and private not in str(
            failed.terminal_reason
        )
        response = app.request(f"/api/tasks/{failed.task_id}")
        assert response.status_code == 200 and private not in response.text
        activity = app.request("/api/system/activity-log", params={"limit": 1000})
        assert activity.status_code == 200
        correlated = [
            row
            for row in activity.json()["entries"]
            if row.get("data", {}).get("task_id")
            in {failed.task_id, result["task"]["task_id"]}
        ]
        assert private not in json.dumps(correlated)
        assert (
            sum(
                row.get("data", {}).get("event") == "chat_compaction_failed"
                for row in correlated
            )
            == 1
        )
        assert not any(
            row.get("data", {}).get("event") == "chat_post_turn_compaction_failed"
            for row in correlated
        )
