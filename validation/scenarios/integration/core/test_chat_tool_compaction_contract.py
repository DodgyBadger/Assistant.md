"""Real chat tool compaction retains a child task and a usable next turn."""

import json

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_persistence import PERSISTENCE_CONTEXT
from validation.scenarios.support.chat_tasks import chat_turn
from validation.scenarios.support.code_tool_chat import (
    code_tool_provider,
    configure_provider,
)
from validation.scenarios.support.compaction_journeys import (
    add_turn,
    compaction_provider,
    configure_compaction,
)

pytestmark = pytest.mark.scenario


async def test_tool_initiated_compaction_has_child_result_and_next_turn(
    app: Application,
) -> None:
    vault = app.vault("ChatToolCompactionVault")
    app.write(vault, "AssistantMD/Authoring/full_history.md", PERSISTENCE_CONTEXT)
    await app.start()
    from core.runtime.execution_tasks import ExecutionTaskKind
    from core.runtime.state import get_runtime_context

    with compaction_provider() as author, code_tool_provider() as model:
        configure_compaction(app, author)
        configure_provider(app, model)
        runtime = get_runtime_context()
        store = runtime.chat_store
        session = "manual-tool-compaction"
        store.ensure_session(session, vault.name, owner_principal_id="local-user")
        add_turn(store, vault.name, session, "Older request.", "Older response.")
        add_turn(store, vault.name, session, "Recent request.", "Recent response.")
        model.tool_name = "chat_history_compact"
        model.arguments = {"operation": "compact", "focus": "Preserve decisions."}
        result = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": session,
                "prompt": "Compact this session.",
                "model": "code-tool-model",
                "context_template": "full_history.md",
            },
        )
        assert result["terminal_event"]["event"] == "done"
        tool_results = [
            row
            for row in store.get_tool_events(session, vault.name)
            if row.tool_name == "chat_history_compact" and row.event_type == "result"
        ]
        assert len(tool_results) == 1
        output = json.loads(tool_results[0].result_text)
        assert output["status"] == "completed" and output["session_id"] == session
        checkpoint = store.get_latest_context_checkpoint(session, vault.name)
        assert (
            checkpoint is not None
            and output["compaction_id"] == checkpoint.checkpoint_id
        )
        tasks = await runtime.task_coordinator.list_tasks(
            kind=ExecutionTaskKind.HISTORY_COMPACTION.value
        )
        assert len(tasks) == 1 and tasks[0].status == "completed"
        assert (
            tasks[0].source == "tool"
            and tasks[0].parent_task_id == result["task"]["task_id"]
        )
        assert tasks[0].result["compaction_id"] == checkpoint.checkpoint_id
        assert store.get_message_count(session, vault.name, mode="raw") >= 6
        model.tool_name = "file_read"
        model.arguments = {"operation": "read", "path": "notes/followup.md"}
        app.write(vault, "notes/followup.md", "FOLLOWUP_AVAILABLE")
        follow = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": session,
                "prompt": "Read the follow-up note.",
                "model": "code-tool-model",
                "context_template": "full_history.md",
            },
        )
        assert (
            follow["terminal_event"]["event"] == "done"
            and "FOLLOWUP_AVAILABLE" in follow["text"]
        )
