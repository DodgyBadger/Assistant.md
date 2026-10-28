"""Retrieved windows admit verified canonical fragments, not raw tool envelopes."""

import sqlite3

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.session_map_retrieval_inputs import (
    append_fragment_retrieval,
    seed_retrieval,
)

pytestmark = pytest.mark.scenario


async def test_retrieved_evidence_verification_and_corruption(app: Application) -> None:
    vault = app.vault("SessionMapRetrievedEvidenceVault")
    await app.start()
    from core.chat.chat_store import ChatHistoryCorruptionError
    from core.memory.session_map.retained_evidence import (
        project_retained_session_map_evidence,
        project_retrieved_session_map_evidence,
    )
    from core.runtime.state import get_runtime_context

    store = get_runtime_context().chat_store
    session = "retrieval"
    original = seed_retrieval(store, vault.name, session)
    raw = store.get_stored_messages(session, vault.name, mode="raw")
    retained = project_retained_session_map_evidence(raw[2:])
    assert [item.sequence_index for item in retained] == [2, 3, 5]
    verified = project_retrieved_session_map_evidence(
        store=store, session_id=session, vault_name=vault.name, retained=raw[2:]
    )
    assert [item.sequence_index for item in verified.messages] == [0]
    assert verified.messages[0].content_text == original
    fork = "retrieval-fork"
    store.fork_session(
        source_session_id=session,
        new_session_id=fork,
        vault_name=vault.name,
        through_sequence_index=5,
        title="Inherited retrieval window",
    )
    fork_raw = store.get_stored_messages(fork, vault.name, mode="raw")
    inherited = project_retrieved_session_map_evidence(
        store=store, session_id=fork, vault_name=vault.name, retained=fork_raw[2:]
    )
    assert [item.sequence_index for item in inherited.messages] == [0]
    append_fragment_retrieval(store, vault.name, session, original, 4, 18)
    raw = store.get_stored_messages(session, vault.name, mode="raw")
    fragments = project_retrieved_session_map_evidence(
        store=store, session_id=session, vault_name=vault.name, retained=raw[6:]
    )
    assert len(fragments.messages) == 1
    fragment = fragments.messages[0]
    assert fragment.content_text == original[4:18] and (
        fragment.content_start,
        fragment.content_end,
    ) == (4, 18)
    assert fragment.content_complete is False and fragments.truncated is False
    with sqlite3.connect(app.root / "system/chat_sessions.db") as conn:
        conn.execute(
            "UPDATE chat_messages SET message_json = ? WHERE session_id = ? AND vault_name = ? AND sequence_index = 0",
            ("{", session, vault.name),
        )
    with pytest.raises(ChatHistoryCorruptionError):
        project_retrieved_session_map_evidence(
            store=store, session_id=session, vault_name=vault.name, retained=raw[6:]
        )
