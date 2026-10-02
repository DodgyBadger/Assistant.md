"""Integration scenario for repeated chat history compaction mechanics."""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from validation.core.base_scenario import BaseScenario


class RepeatedChatHistoryCompactionScenario(BaseScenario):
    """Validate repeated compaction feeds prior cards forward deterministically."""

    async def test_scenario(self):
        vault = self.create_vault("RepeatedChatHistoryCompactionVault")

        await self.start_system()

        response = self.call_api(
            "/api/system/settings/general/compaction_author_model",
            method="PUT",
            data={"value": "test"},
        )
        assert (
            response.status_code == 200
        ), "Deterministic compaction needs a test author"

        import core.chat.compaction as compaction
        from core.chat.chat_store import ChatStore
        from core.constants import CHAT_HISTORY_RECOVERY_CARD_PREAMBLE
        from core.identity import LOCAL_USER_PRINCIPAL_ID
        from core.runtime.state import get_runtime_context

        runtime = get_runtime_context()
        store = ChatStore(system_root=str(runtime.config.system_root))
        session_id = "repeated_chat_history_compaction_session"
        store.ensure_session(
            session_id,
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        store.add_messages(
            session_id,
            vault.name,
            [
                _user("Objective: draft the Alfa client research memo."),
                ModelResponse(
                    parts=[TextPart(content="Progress: created the initial outline.")]
                ),
                _user("Constraint: cite sources in APA style."),
                ModelResponse(
                    parts=[TextPart(content="Next step: collect source notes.")]
                ),
            ],
        )

        captured_inputs: list[dict[str, object]] = []
        summaries = [
            (
                "Round 1 card\n"
                "Current objective: draft the Alfa client research memo.\n"
                "Progress: initial outline exists.\n"
                "Next step: collect source notes."
            ),
            (
                "Round 2 card\n"
                "Current objective: draft the Alfa client research memo.\n"
                "Progress: source notes collected.\n"
                "Constraint: use Chicago style.\n"
                "Next step: write synthesis section."
            ),
            (
                "Round 3 card\n"
                "Current objective: draft the Alfa client research memo.\n"
                "Progress: synthesis section drafted.\n"
                "Blocker: waiting for finance appendix.\n"
                "Next step: ask the user for appendix status."
            ),
        ]

        async def _summary_stub(*, older_messages, recent_messages, focus):
            captured_inputs.append(
                {
                    "older_text": "\n".join(
                        compaction._message_to_compaction_source(message)["content"]
                        for message in older_messages
                    ),
                    "recent_text": "\n".join(
                        compaction._message_to_compaction_source(message)["content"]
                        for message in recent_messages
                    ),
                    "focus": focus,
                }
            )
            return summaries[len(captured_inputs) - 1]

        original_retained_turns = compaction.get_compaction_retained_turns
        original_generate_summary = compaction._generate_compaction_summary
        compaction.get_compaction_retained_turns = lambda: 1
        compaction._generate_compaction_summary = _summary_stub
        try:
            first = await compaction.compact_chat_history(
                session_id=session_id,
                vault_name=vault.name,
                vault_path=str(vault),
                focus="Preserve objective, progress, constraints, blockers, and next step.",
            )
            store.add_messages(
                session_id,
                vault.name,
                [
                    _user("Update: source notes are collected."),
                    _assistant("Superseding constraint: use Chicago style."),
                ],
            )
            second = await compaction.compact_chat_history(
                session_id=session_id,
                vault_name=vault.name,
                vault_path=str(vault),
                focus="Merge the previous card with newer source-note progress.",
            )
            store.add_messages(
                session_id,
                vault.name,
                [
                    _user("Update: synthesis section is drafted."),
                    _assistant("Blocker: waiting for finance appendix."),
                ],
            )
            third = await compaction.compact_chat_history(
                session_id=session_id,
                vault_name=vault.name,
                vault_path=str(vault),
                focus="Carry forward only current objective, progress, blocker, and next step.",
            )
        finally:
            compaction.get_compaction_retained_turns = original_retained_turns
            compaction._generate_compaction_summary = original_generate_summary

        messages_before = [
            first.messages_before,
            second.messages_before,
            third.messages_before,
        ]
        assert messages_before == [
            4,
            5,
            5,
        ], "Each compaction should read the current effective history, not all raw archival rows"
        assert [first.messages_after, second.messages_after, third.messages_after] == [
            3,
            3,
            3,
        ], "Each compaction should rewrite to one card plus one preserved recent turn"

        assert "Objective: draft the Alfa client research memo." in str(
            captured_inputs[0]["older_text"]
        ), "First compaction should receive initial objective history"
        assert "AssistantMD compacted chat history" in str(
            captured_inputs[1]["older_text"]
        ), "Second compaction should receive the previous compaction card"
        assert "Round 1 card" in str(
            captured_inputs[1]["older_text"]
        ), "Second compaction should be able to merge prior card content"
        assert (
            str(captured_inputs[1]["older_text"]).count(
                CHAT_HISTORY_RECOVERY_CARD_PREAMBLE
            )
            == 1
        ), "Repeated compaction should receive one fixed recovery-card preamble"
        assert "Update: source notes are collected." in str(
            captured_inputs[1]["recent_text"]
        ), "Second compaction should retain the newest complete raw turn"
        assert "Round 2 card" in str(
            captured_inputs[2]["older_text"]
        ), "Third compaction should receive the latest merged card"
        assert "Round 1 card" not in str(
            captured_inputs[2]["older_text"]
        ), "Third compaction should not replay stale checkpoint rows after replacement"
        assert "Blocker: waiting for finance appendix." in str(
            captured_inputs[2]["recent_text"]
        ), "Recent slice should preserve the newest assistant turn verbatim"

        effective_messages = store.get_stored_messages(session_id, vault.name)
        assert (
            len(effective_messages) == 3
        ), "Effective history should stay compact after three rounds"
        assert (
            "Round 3 card" in effective_messages[0].content_text
        ), "Latest effective history should expose the newest recovery card"
        assert (
            "Round 1 card" not in effective_messages[0].content_text
        ), "Latest recovery card should not be an accumulation of prior cards"
        assert (
            effective_messages[0].content_text.count(
                CHAT_HISTORY_RECOVERY_CARD_PREAMBLE
            )
            == 1
        ), "Latest recovery card should contain exactly one fixed preamble"
        assert (
            effective_messages[2].content_text
            == "Blocker: waiting for finance appendix."
        ), "Latest effective history should preserve the configured recent turn"
        assert (
            store.get_message_count(session_id, vault.name, mode="raw") == 8
        ), "Repeated compaction should preserve all raw archival messages"
        assert (
            store.get_message_count(session_id, vault.name) == 3
        ), "Default message count should use the latest effective checkpoint"
        assert (
            store.get_session_history_revision(session_id, vault.name) == 6
        ), "Raw appends and compactions should each advance effective-history revision"

        metadata = store.get_session_metadata(session_id, vault.name)
        assert (
            metadata["last_compaction"]["compaction_id"] == third.compaction_id
        ), "Session metadata should point at the newest compaction checkpoint"
        assert (
            metadata["last_compaction"]["prompt_contract_version"] == "recovery-card-v5"
        ), "Newest compaction metadata should retain the recovery-card contract"

        conn = sqlite3.connect(runtime.config.system_root / "chat_sessions.db")
        try:
            checkpoint_rows = conn.execute(
                """
                SELECT checkpoint_id, last_message_sequence_index
                FROM chat_compaction_checkpoints
                WHERE session_id = ? AND vault_name = ?
                ORDER BY id ASC
                """,
                (session_id, vault.name),
            ).fetchall()
        finally:
            conn.close()
        assert [row[0] for row in checkpoint_rows] == [
            first.compaction_id,
            second.compaction_id,
            third.compaction_id,
        ], "All compaction checkpoints should be retained for audit"
        assert [row[1] for row in checkpoint_rows] == [
            3,
            5,
            7,
        ], "Each checkpoint should record the raw high-water mark it compacted through"


def _user(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _assistant(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])
