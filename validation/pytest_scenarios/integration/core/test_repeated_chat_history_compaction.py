"""Three real authoring passes replace prior checkpoints without losing raw turns."""

import json
import sqlite3

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.compaction_journeys import (
    add_turn,
    author_payload,
    compaction_provider,
    configure_compaction,
)

pytestmark = pytest.mark.scenario


async def test_repeated_compaction_replaces_effective_history(app: Application) -> None:
    vault = app.vault("RepeatedChatHistoryCompactionVault")
    with compaction_provider() as server:
        await app.start()
        from pydantic_ai.messages import (
            ModelRequest,
            ModelResponse,
            TextPart,
            UserPromptPart,
        )

        from core.constants import CHAT_HISTORY_RECOVERY_CARD_PREAMBLE
        from core.runtime.state import get_runtime_context

        configure_compaction(app, server)
        store = get_runtime_context().chat_store
        session = "repeated_chat_history_compaction_session"
        store.ensure_session(session, vault.name, owner_principal_id="local-user")
        store.add_messages(
            session,
            vault.name,
            [
                ModelRequest(
                    parts=[
                        UserPromptPart(
                            content="Objective: draft the Alfa client research memo."
                        )
                    ]
                ),
                ModelResponse(
                    parts=[TextPart(content="Progress: created the initial outline.")]
                ),
                ModelRequest(
                    parts=[
                        UserPromptPart(content="Constraint: cite sources in APA style.")
                    ]
                ),
                ModelResponse(
                    parts=[TextPart(content="Next step: collect source notes.")]
                ),
            ],
        )
        results = []
        for index in range(3):
            if index == 1:
                add_turn(
                    store,
                    vault.name,
                    session,
                    "Update: source notes are collected.",
                    "Superseding constraint: use Chicago style.",
                )
            elif index == 2:
                add_turn(
                    store,
                    vault.name,
                    session,
                    "Update: synthesis section is drafted.",
                    "Blocker: waiting for finance appendix.",
                )
            response = await app.request_async(
                f"/api/chat/sessions/{session}/compact",
                method="POST",
                data={"vault_name": vault.name, "focus": f"Round {index + 1} focus"},
            )
            assert response.status_code == 200, response.text
            results.append(response.json())
        assert [row["messages_before"] for row in results] == [4, 5, 5]
        assert [row["messages_after"] for row in results] == [3, 3, 3]
        payloads = [author_payload(server, index) for index in range(3)]
        assert len(server.requests) == 3
        assert all(
            payload["prompt_contract_version"] == "recovery-card-v5"
            for payload in payloads
        )
        assert "Objective: draft the Alfa client research memo." in json.dumps(
            payloads[0]["older_history"]
        )
        assert "Recovery card round 1" in json.dumps(payloads[1]["older_history"])
        assert (
            sum(
                row["content"].count(CHAT_HISTORY_RECOVERY_CARD_PREAMBLE)
                for row in payloads[1]["older_history"]
            )
            == 1
        )
        assert "Update: source notes are collected." in json.dumps(
            payloads[1]["retained_recent_history"]
        )
        assert "Recovery card round 2" in json.dumps(payloads[2]["older_history"])
        assert "Recovery card round 1" not in json.dumps(payloads[2]["older_history"])
        assert "Blocker: waiting for finance appendix." in json.dumps(
            payloads[2]["retained_recent_history"]
        )
        effective = store.get_stored_messages(session, vault.name)
        assert (
            len(effective) == 3 and "Recovery card round 3" in effective[0].content_text
        )
        assert "Recovery card round 1" not in effective[0].content_text
        assert effective[0].content_text.count(CHAT_HISTORY_RECOVERY_CARD_PREAMBLE) == 1
        assert effective[-1].content_text == "Blocker: waiting for finance appendix."
        assert store.get_message_count(session, vault.name, mode="raw") == 8
        assert store.get_message_count(session, vault.name) == 3
        assert store.get_session_history_revision(session, vault.name) == 6
        metadata = store.get_session_metadata(session, vault.name)["last_compaction"]
        assert (
            metadata["compaction_id"] == results[-1]["compaction_id"]
            and metadata["prompt_contract_version"] == "recovery-card-v5"
        )
        with sqlite3.connect(app.root / "system/chat_sessions.db") as conn:
            rows = conn.execute(
                "SELECT checkpoint_id, last_message_sequence_index FROM chat_compaction_checkpoints WHERE session_id=? AND vault_name=? ORDER BY id",
                (session, vault.name),
            ).fetchall()
        assert [row[0] for row in rows] == [
            result["compaction_id"] for result in results
        ]
        assert [row[1] for row in rows] == [3, 5, 7]
        app.evidence.save_json("compaction-author-requests.json", server.requests)
