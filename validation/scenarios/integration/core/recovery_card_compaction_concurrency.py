"""Reject recovery-card checkpoints authored against stale canonical history."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai.messages import (  # noqa: E402
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from core.identity import LOCAL_USER_PRINCIPAL_ID  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class RecoveryCardCompactionConcurrencyScenario(BaseScenario):
    """Preserve appended messages and session deletion during V1 authoring."""

    async def test_scenario(self) -> None:
        vault = self.create_vault("RecoveryCardCompactionConcurrencyVault")
        await self.start_system()

        import core.chat.compaction as compaction
        from core.chat.chat_store import ChatStore
        from core.runtime.state import get_runtime_context

        runtime = get_runtime_context()
        store = runtime.chat_store
        other_store = ChatStore(system_root=str(runtime.config.system_root))
        for key, value in (
            ("compaction_author_model", "test"),
            ("compaction_retained_turns", "1"),
        ):
            response = self.call_api(
                f"/api/system/settings/general/{key}",
                method="PUT",
                data={"value": value},
            )
            assert response.status_code == 200, f"{key} should update"

        initial_messages = [
            _user("Earlier decision to preserve."),
            _assistant("Earlier decision recorded."),
            _user("Continue reviewing the plan."),
            _assistant("The review remains open."),
            _user("Most recent question."),
            _assistant("Most recent response."),
        ]
        original_generate_summary = compaction._generate_compaction_summary
        try:
            for mutation in ("append", "delete"):
                session_id = f"recovery-card-concurrent-{mutation}"
                store.ensure_session(
                    session_id,
                    vault.name,
                    owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
                )
                store.add_messages(session_id, vault.name, initial_messages)
                author_started = asyncio.Event()
                release_author = asyncio.Event()

                async def blocked_author(
                    *,
                    older_messages: list[ModelMessage],
                    recent_messages: list[ModelMessage],
                    focus: str | None,
                    author_started: asyncio.Event = author_started,
                    release_author: asyncio.Event = release_author,
                ) -> str:
                    del focus
                    assert len(older_messages) == 4
                    assert len(recent_messages) == 2
                    author_started.set()
                    await release_author.wait()
                    return "Preserve the earlier decision and continue the review."

                compaction._generate_compaction_summary = blocked_author
                compaction_task = asyncio.create_task(
                    compaction.compact_chat_history(
                        session_id=session_id,
                        vault_name=vault.name,
                        store=store,
                    )
                )
                try:
                    await asyncio.wait_for(author_started.wait(), timeout=5)
                    if mutation == "append":
                        other_store.add_messages(
                            session_id,
                            vault.name,
                            [
                                _user("A new decision arrived during authoring."),
                                _assistant("The new decision must remain visible."),
                            ],
                        )
                    else:
                        other_store.delete_sessions(vault.name, session_id=session_id)
                    release_author.set()
                    failure: ValueError | None = None
                    try:
                        await asyncio.wait_for(compaction_task, timeout=5)
                    except ValueError as exc:
                        failure = exc
                    assert (
                        failure is not None
                    ), f"Concurrent {mutation} must reject the stale checkpoint"
                finally:
                    release_author.set()
                    if not compaction_task.done():
                        compaction_task.cancel()
                    await asyncio.gather(compaction_task, return_exceptions=True)

                assert not store.list_context_checkpoints(
                    session_id, vault.name
                ), "Rejected compaction must not persist any checkpoint"
                if mutation == "append":
                    raw = store.get_stored_messages(session_id, vault.name, mode="raw")
                    effective = store.get_stored_messages(session_id, vault.name)
                    assert len(raw) == 8, "Every canonical message must survive"
                    assert [message.content_text for message in effective] == [
                        message.content_text for message in raw
                    ], "A stale checkpoint must not hide concurrent appended messages"
                    assert (
                        store.get_session_history_revision(session_id, vault.name) == 2
                    )
                    assert "history revision changed" in str(
                        failure
                    ), "Append rejection must identify the stale history revision"
                    assert "last_compaction" not in store.get_session_metadata(
                        session_id, vault.name
                    ), "Rejected compaction must leave no success audit metadata"
                else:
                    assert (
                        store.get_session(session_id, vault.name) is None
                    ), "Late author completion must not recreate a deleted session"
        finally:
            compaction._generate_compaction_summary = original_generate_summary

        await self.stop_system()
        self.teardown_scenario()


def _user(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _assistant(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])
