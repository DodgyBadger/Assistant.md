"""Concurrent writes or deletion reject stale recovery-card checkpoints."""

import asyncio

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.compaction_journeys import (
    add_turn,
    author_payload,
    configure_compaction,
)
from validation.scenarios.support.gated_compaction_author import (
    gated_author,
    seed_conversation,
)

pytestmark = pytest.mark.scenario


@pytest.mark.parametrize("mutation", ["append", "delete"])
async def test_concurrent_history_mutation_rejects_stale_compaction(
    app: Application, mutation: str
) -> None:
    vault = app.vault("RecoveryCardConcurrencyVault")
    with gated_author() as server:
        await app.start()
        from core.chat.chat_store import ChatStore
        from core.chat.compaction import compact_chat_history
        from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
        from core.runtime.state import get_runtime_context

        configure_compaction(app, server)
        store = get_runtime_context().chat_store
        other = ChatStore(system_root=str(get_runtime_context().config.system_root))
        session = f"recovery-card-concurrent-{mutation}"
        store.ensure_session(session, vault.name, owner_principal_id="local-user")
        seed_conversation(store, vault.name, session)
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            running = asyncio.create_task(
                compact_chat_history(
                    session_id=session, vault_name=vault.name, store=store
                )
            )
            try:
                assert await asyncio.to_thread(server.started.wait, 30)
                payload = author_payload(server, 0)
                assert (
                    len(payload["older_history"]) == 4
                    and len(payload["retained_recent_history"]) == 2
                )
                if mutation == "append":
                    add_turn(
                        other,
                        vault.name,
                        session,
                        "A new decision arrived during authoring.",
                        "The new decision must remain visible.",
                    )
                else:
                    other.delete_sessions(vault.name, session_id=session)
                server.release.set()
                with pytest.raises(ValueError) as failure:
                    await asyncio.wait_for(running, 30)
            finally:
                server.release.set()
                if not running.done():
                    running.cancel()
                    await asyncio.gather(running, return_exceptions=True)
        assert not store.list_context_checkpoints(session, vault.name)
        if mutation == "append":
            raw = store.get_stored_messages(session, vault.name, mode="raw")
            effective = store.get_stored_messages(session, vault.name)
            assert len(raw) == 8 and [row.content_text for row in effective] == [
                row.content_text for row in raw
            ]
            assert store.get_session_history_revision(session, vault.name) == 2
            assert "history revision changed" in str(failure.value)
            assert "last_compaction" not in store.get_session_metadata(
                session, vault.name
            )
        else:
            assert store.get_session(session, vault.name) is None
