"""Manual compaction retains replay and shapes tool results for the author."""

import json
import sqlite3

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.compaction_continuation_inputs import (
    seed_tool_history,
    seed_tool_result_shapes,
)
from validation.scenarios.support.compaction_journeys import (
    author_payload,
    compaction_provider,
    configure_compaction,
)

pytestmark = pytest.mark.scenario


async def test_second_compaction_and_post_checkpoint_history(app: Application) -> None:
    vault = app.vault("CompactionContinuationVault")
    with compaction_provider() as server:
        await app.start()
        from pydantic_ai.messages import ModelRequest, UserPromptPart

        from core.chat.history_service import ChatHistoryContext, ChatHistoryService
        from core.runtime.state import get_runtime_context

        configure_compaction(app, server)
        store = get_runtime_context().chat_store
        session = "continuation"
        seed_tool_history(store, vault.name, session)
        results = []
        for focus in (
            "Keep decisions and tool outcomes.",
            "Keep the compacted decision and current tool outcome.",
        ):
            response = await app.request_async(
                f"/api/chat/sessions/{session}/compact",
                method="POST",
                data={"vault_name": vault.name, "focus": focus},
            )
            assert response.status_code == 200, response.text
            results.append(response.json())
        assert [row["messages_before"] for row in results] == [6, 5]
        assert store.get_message_count(session, vault.name, mode="raw") == 6
        assert store.get_message_count(session, vault.name) == 5
        assert store.get_session_history_revision(session, vault.name) == 3
        assert "Recovery card round 1" in json.dumps(
            author_payload(server, 1)["older_history"]
        )
        store.add_messages(
            session,
            vault.name,
            [
                ModelRequest(
                    parts=[UserPromptPart(content="Post-compaction follow-up.")]
                )
            ],
        )
        assert store.get_message_count(session, vault.name, mode="raw") == 7
        assert store.get_session_history_revision(session, vault.name) == 4
        effective = store.get_stored_messages(session, vault.name)
        assert (
            len(effective) == 6
            and effective[-1].content_text == "Post-compaction follow-up."
        )
        detail = app.request(
            f"/api/chat/sessions/{session}", params={"vault_name": vault.name}
        )
        assert detail.status_code == 200
        assert detail.json()["messages"][-1]["content"] == "Post-compaction follow-up."
        assert detail.json()["messages"][-1]["fork_sequence_index"] == 6
        broker = ChatHistoryService(chat_store=store).get_conversation_history(
            context=ChatHistoryContext(session_id=session, vault_name=vault.name),
            scope="session",
            session_id=session,
            limit="all",
        )
        assert broker.item_count == 6
        assert all(item.content != "First user decision." for item in broker.items)
        with sqlite3.connect(app.root / "system/chat_sessions.db") as conn:
            rows = conn.execute(
                "SELECT version FROM schema_migrations WHERE namespace='chat_sessions' ORDER BY version"
            ).fetchall()
        assert [row[0] for row in rows] == list(range(1, 11))
        app.evidence.save_json("continuation-author-requests.json", server.requests)


async def test_compaction_author_receives_shaped_tool_results(app: Application) -> None:
    vault = app.vault("CompactionToolShapeVault")
    with compaction_provider() as server:
        await app.start()
        from core.runtime.state import get_runtime_context

        configure_compaction(app, server)
        session = "shaped-tool-history"
        seed_tool_result_shapes(get_runtime_context().chat_store, vault.name, session)
        response = await app.request_async(
            f"/api/chat/sessions/{session}/compact",
            method="POST",
            data={"vault_name": vault.name},
        )
        assert response.status_code == 200, response.text
        payload = author_payload(server, 0)
        older = json.dumps(payload["older_history"])
        assert "[tool result omitted] empty_probe: empty result" in older
        assert "long failure details should not enter compaction prompt" not in older
        assert older.count("large successful result") == 100
        app.evidence.save_json("tool-shape-author-requests.json", server.requests)
