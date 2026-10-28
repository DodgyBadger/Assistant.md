"""Canonical transcript indexing, Unicode retrieval and authorization in a real app."""

from __future__ import annotations

import sqlite3

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.transcript_storage import message, tool_result

pytestmark = pytest.mark.scenario


async def test_transcript_index_lifecycle_and_authorization(app: Application) -> None:
    vault = app.vault("TranscriptVault")
    other = app.vault("OtherTranscriptVault")
    await app.start()
    from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart

    from core.chat.schema import ensure_chat_sessions_schema
    from core.chat.transcript_retrieval import (
        MAX_SEARCH_LIMIT,
        TranscriptRetrievalService,
    )
    from core.identity import (
        LOCAL_USER_PRINCIPAL_ID,
        ExecutionAuthority,
        use_execution_authority,
    )
    from core.runtime.state import get_runtime_context

    runtime = get_runtime_context()
    store = runtime.chat_store
    system_root = app.root / "system"
    owner = ExecutionAuthority(LOCAL_USER_PRINCIPAL_ID)
    attacker = ExecutionAuthority("transcript-attacker")
    vault_name = vault.name
    other_vault = other.name
    session_id = "transcript-primary"
    other_session_id = "transcript-other"
    store.ensure_session(session_id, vault_name, owner_principal_id=owner.principal_id)
    store.add_messages(
        session_id,
        vault_name,
        [
            message("Opening context without the target phrase."),
            message("The cobalt-lantern decision belongs to the primary vault."),
            message("Closing context after the target phrase."),
            tool_result(
                "source_probe",
                {"status": "failed", "error": "granite-signal primary failure"},
                "source-probe-call",
            ),
            tool_result(
                "session_ops",
                '{"operation":"search_transcript","matches":[{"excerpt":"granite-signal primary failure"}]}',
                "retrieval-call",
            ),
        ],
    )
    store.ensure_session(
        other_session_id, other_vault, owner_principal_id=owner.principal_id
    )
    store.add_messages(
        other_session_id,
        other_vault,
        [message("The cobalt-lantern phrase also exists in another vault.")],
    )
    ensure_chat_sessions_schema(str(system_root), apply_migrations=True)
    access = runtime.chat_session_access
    retrieval = TranscriptRetrievalService(store, access, excerpt_chars=80)
    with use_execution_authority(owner):
        backfilled = retrieval.search(
            vault_name=vault_name, session_id=session_id, query="cobalt lantern"
        )
        assert [
            (hit.anchor.session_id, hit.anchor.sequence_index) for hit in backfilled
        ] == [
            (session_id, 1)
        ], "Migration should backfill existing canonical messages with stable anchors"
        assert all(
            hit.vault_name == vault_name for hit in backfilled
        ), "Search SQL should scope matches to the requested vault before returning hits"
        assert all(
            len(hit.excerpt) <= 80 for hit in backfilled
        ), "Search excerpts should stay inside the configured character bound"
        structured = retrieval.search(
            vault_name=vault_name,
            session_id=session_id,
            query="granite signal primary failure",
        )
        assert (
            structured[0].anchor.sequence_index if structured else None
        ) == 3, "Structured primary tool evidence should be the highest-ranked source"
        assert all(
            hit.anchor.sequence_index != 4 for hit in structured
        ), "A later retrieval envelope quoting the evidence should not be a search candidate"
        assert (
            structured[0].source_kind if structured else None
        ) == "tool_result", "Search hits should distinguish direct tool evidence from user-authored text"
        assert (structured[0].tool_names if structured else None) == (
            "source_probe",
        ), "Search hits should identify the tool that produced direct evidence"
        structured_window = retrieval.get_window(
            vault_name=vault_name,
            session_id=session_id,
            sequence_index=3,
            before=0,
            after=0,
        )
        assert (
            "granite-signal primary failure" in structured_window.messages[0].content
        ), "Transcript windows should render structured tool evidence readably"
        assert (
            structured_window.messages[0].source_kind == "tool_result"
        ), "Transcript windows should retain source provenance"
        store.add_messages(
            session_id,
            vault_name,
            [message("A newly indexed zephyr-compass fact." * 20)],
        )
        immediate = retrieval.search(
            vault_name=vault_name, session_id=session_id, query="zephyr compass"
        )
        assert [hit.anchor.sequence_index for hit in immediate] == [
            5
        ], "New canonical writes should become searchable in the same transaction"
        assert (
            bool(immediate) and len(immediate[0].excerpt) <= 80
        ), "Long matching messages should return bounded excerpts"
        store.add_messages(
            session_id,
            vault_name,
            [
                message("The café résumé archive is stored in Montréal."),
                message("東京 京都の旅程はこのメッセージに記録されています。"),
            ],
        )
        accented = retrieval.search(
            vault_name=vault_name, session_id=session_id, query="café résumé"
        )
        assert [hit.anchor.sequence_index for hit in accented] == [
            6
        ], "Unicode-aware query tokenization should find accented transcript text"
        non_latin = retrieval.search(
            vault_name=vault_name, session_id=session_id, query="東京 京都"
        )
        assert [hit.anchor.sequence_index for hit in non_latin] == [
            7
        ], "Unicode-aware query tokenization should find non-Latin transcript text"
        exact_range = retrieval.get_range(
            vault_name=vault_name,
            session_id=session_id,
            after_sequence_index=0,
            through_sequence_index=2,
        )
        assert [message.sequence_index for message in exact_range] == [
            1,
            2,
        ], "Canonical interval reads should preserve exact chronological bounds"
        db_path = system_root / "chat_sessions.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "\n                    UPDATE chat_messages\n                    SET content_text = ?\n                    WHERE session_id = ? AND vault_name = ? AND sequence_index = 1\n                    ",
                (
                    "The amber-orbit decision replaced the earlier wording.",
                    session_id,
                    vault_name,
                ),
            )
            conn.commit()
        assert (
            retrieval.search(
                vault_name=vault_name, session_id=session_id, query="cobalt lantern"
            )
            == []
        ), "Updating canonical text should remove stale index terms"
        updated = retrieval.search(
            vault_name=vault_name, session_id=session_id, query="amber orbit"
        )
        assert [hit.anchor.sequence_index for hit in updated] == [
            1
        ], "Updating canonical text should index replacement terms"
        malformed = retrieval.search(
            vault_name=vault_name, session_id=session_id, query='" OR * NOT ( amber'
        )
        assert isinstance(
            malformed, list
        ), "Malformed FTS-shaped input should be normalized instead of leaking SQL errors"
        with pytest.raises(ValueError):
            retrieval.search(vault_name=vault_name, session_id=session_id, query="!")
        with pytest.raises(ValueError):
            retrieval.search(
                vault_name=vault_name,
                session_id=session_id,
                query="amber",
                limit=MAX_SEARCH_LIMIT + 1,
            )
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO chat_messages_fts(chat_messages_fts) VALUES ('delete-all')"
            )
            conn.commit()
        assert (
            retrieval.search(
                vault_name=vault_name, session_id=session_id, query="amber orbit"
            )
            == []
        ), "The test should be able to simulate a missing derived index"
        retrieval.rebuild_index()
        assert [
            hit.anchor.sequence_index
            for hit in retrieval.search(
                vault_name=vault_name, session_id=session_id, query="amber orbit"
            )
        ] == [1], "Index rebuild should deterministically restore canonical matches"
        with pytest.raises(LookupError):
            retrieval.search(
                vault_name=other_vault, session_id=session_id, query="amber"
            )
    for identity, count, principal in (
        ("aaa-long", 105, owner.principal_id),
        ("zzz-short", 1, owner.principal_id),
        ("hidden-match", 105, attacker.principal_id),
    ):
        store.ensure_session(identity, vault_name, owner_principal_id=principal)
        store.add_messages(
            identity, vault_name, [message("wetland") for _ in range(count)]
        )
    with use_execution_authority(owner):
        balanced = retrieval.search_vault(
            vault_name=vault_name, query="wetland", limit=2
        )
        assert [hit.anchor.session_id for hit in balanced] == ["aaa-long", "zzz-short"]
        assert all(hit.anchor.sequence_index == 0 for hit in balanced)
        assert [
            hit.anchor.session_id
            for hit in retrieval.search_vault(
                vault_name=vault_name,
                query="wetland",
                limit=2,
                session_ids={"zzz-short"},
            )
        ] == ["zzz-short"]
        store.add_messages(
            "aaa-long",
            vault_name,
            [
                ModelRequest(
                    parts=[
                        UserPromptPart(content="unrelated primary text"),
                        ToolReturnPart(
                            tool_name="session_ops",
                            tool_call_id="echo",
                            content="fallbackneedle " * 20,
                        ),
                    ]
                ),
                message("fallbackneedle"),
            ],
        )
        fallback = retrieval.search_vault(
            vault_name=vault_name, query="fallbackneedle", limit=2
        )
        assert len(fallback) == 1 and fallback[0].anchor.sequence_index == 106
    with use_execution_authority(attacker):
        with pytest.raises(LookupError):
            retrieval.search(
                vault_name=vault_name, session_id=session_id, query="amber"
            )
    store.delete_sessions(vault_name, session_id=session_id)
    with sqlite3.connect(system_root / "chat_sessions.db") as conn:
        remaining = conn.execute(
            "\n                SELECT COUNT(*)\n                FROM chat_messages_fts\n                WHERE chat_messages_fts MATCH 'amber'\n                "
        ).fetchone()
    assert (
        int(remaining[0] if remaining else -1) == 0
    ), "Cascade purge should remove deleted-session terms from the index"
