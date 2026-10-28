"""Reasoning retention follows settings across canonical and checkpoint history."""

from __future__ import annotations

import pytest

from validation.pytest_scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_reasoning_retention_and_checkpoint_policy_transitions(
    app: Application,
) -> None:
    app.evidence.register_secret("private reasoning summary")
    vault = app.vault("ChatReasoningHistoryPolicyVault")
    await app.start()
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        ThinkingPart,
        ToolCallPart,
        UserPromptPart,
    )

    from core.chat.chat_store import ChatStore
    from core.identity import LOCAL_USER_PRINCIPAL_ID

    store = ChatStore(system_root=str(app.root / "system"))

    def response():
        return ModelResponse(
            parts=[
                ThinkingPart(
                    content="private reasoning summary",
                    id="reasoning_content",
                    provider_name="openai",
                ),
                TextPart(
                    content="Visible answer.",
                    id="visible_message",
                    provider_name="openai",
                ),
                ToolCallPart(
                    tool_name="probe",
                    args={"value": 1},
                    tool_call_id="probe-call",
                    id="probe-item",
                    provider_name="openai",
                ),
            ],
            provider_name="openai",
        )

    def thinking(history):
        return sum(
            isinstance(part, ThinkingPart)
            for message in history
            for part in message.parts
        )

    def set_policy(value):
        result = app.request(
            "/api/system/settings/general/persist_model_reasoning_parts",
            "PUT",
            {"value": str(value).lower()},
        )
        assert result.status_code == 200

    for enabled in (False, True):
        set_policy(enabled)
        session = f"reasoning-{enabled}"
        store.ensure_session(
            session, vault.name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
        )
        store.add_messages(session, vault.name, [response()])
        history = store.get_history(session, vault.name) or []
        parts = [part for message in history for part in message.parts]
        assert thinking(history) == int(enabled)
        assert any(
            isinstance(part, TextPart) and part.content == "Visible answer."
            for part in parts
        )
        assert any(
            isinstance(part, ToolCallPart) and part.tool_call_id == "probe-call"
            for part in parts
        )
        assert [part.id for part in parts if part.id] == (
            ["reasoning_content", "visible_message", "probe-item"] if enabled else []
        )
        detail = app.request(
            f"/api/chat/sessions/{session}", params={"vault_name": vault.name}
        )
        assert detail.status_code == 200
        if enabled:
            message = next(
                message
                for message in detail.json()["messages"]
                if message["role"] == "assistant"
            )
            assert (message["thinking_content"], message["content"]) == (
                "private reasoning summary",
                "Visible answer.",
            )
        else:
            assert "private reasoning summary" not in detail.text
            assert all(
                not message.get("thinking_content")
                for message in detail.json()["messages"]
            )
    for initial, current in ((True, False), (False, True)):
        session = f"reasoning-checkpoint-{initial}-{current}"
        set_policy(initial)
        store.ensure_session(
            session, vault.name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
        )
        store.add_messages(
            session,
            vault.name,
            [
                ModelRequest(parts=[UserPromptPart(content="Question")]),
                ModelResponse(
                    parts=[
                        ThinkingPart(
                            content="Canonical reasoning", id="canonical-reasoning"
                        ),
                        TextPart(content="Canonical answer", id="canonical-answer"),
                    ]
                ),
            ],
        )
        set_policy(current)
        canonical = [
            item.message
            for item in store.get_stored_messages(session, vault.name, mode="raw")
        ]
        synthetic = ModelResponse(
            parts=[
                ThinkingPart(content="Synthetic reasoning", id="synthetic-reasoning"),
                TextPart(content="Synthetic answer", id="synthetic-answer"),
            ]
        )
        store.add_compaction_checkpoint(
            session_id=session,
            vault_name=vault.name,
            checkpoint_id=f"{session}-checkpoint",
            source="validation",
            message_count_before=2,
            last_message_sequence_index=1,
            summary_message=synthetic,
            replacement_history=[synthetic, *canonical],
            replacement_source_sequence_indexes=[None, 0, 1],
        )
        effective = store.get_stored_messages(session, vault.name)
        assert [item.message for item in effective[1:]] == canonical
        assert thinking([effective[0].message]) == int(current)
        assert thinking(store.get_history(session, vault.name) or []) == int(
            current
        ) * (1 + int(initial))
        child = f"{session}-child"
        store.fork_session(
            source_session_id=session,
            new_session_id=child,
            vault_name=vault.name,
            through_sequence_index=1,
            title="Reasoning policy fork",
        )
        assert [
            item.message for item in store.get_stored_messages(child, vault.name)[1:]
        ] == canonical
