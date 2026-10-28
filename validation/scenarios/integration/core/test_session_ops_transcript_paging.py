"""Bounded transcript continuation rejects stale or forged cursors."""

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.transcript_paging_inputs import seed_paging

pytestmark = pytest.mark.scenario


async def test_transcript_paging_cursor_and_echo_exclusion(app: Application) -> None:
    vault = app.vault("TranscriptPagingVault")
    await app.start()
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from core.chat.transcript_retrieval import TranscriptRetrievalService
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.runtime.state import get_runtime_context

    runtime = get_runtime_context()
    text = seed_paging(runtime.chat_store, vault.name)
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        retrieval = TranscriptRetrievalService(
            runtime.chat_store, runtime.chat_session_access
        )
        hits = retrieval.search(
            vault_name=vault.name,
            session_id="echo-starvation",
            query="ember beacon",
            limit=5,
        )
        assert [hit.anchor.sequence_index for hit in hits] == [101]
        neighboring = retrieval.get_window(
            vault_name=vault.name,
            session_id="echo-starvation",
            sequence_index=101,
            before=10,
            after=0,
            max_tokens=1000,
        )
        assert [row.sequence_index for row in neighboring.messages] == [101]
        page = retrieval.get_window(
            vault_name=vault.name,
            session_id="paging",
            sequence_index=1,
            before=0,
            after=0,
            max_tokens=600,
        )
        first_cursor = page.next_cursor
        assert first_cursor and page.estimated_tokens <= page.max_tokens
        around = retrieval.get_window(
            vault_name=vault.name,
            session_id="paging",
            sequence_index=1,
            before=2,
            after=2,
            max_tokens=600,
        )
        assert around.truncated_before is True and around.truncated_after is False
        parts = [page.messages[0].content]
        while page.next_cursor:
            page = retrieval.get_window(
                vault_name=vault.name,
                session_id="paging",
                sequence_index=1,
                before=0,
                after=0,
                max_tokens=600,
                cursor=page.next_cursor,
            )
            assert page.estimated_tokens <= page.max_tokens
            parts.append(page.messages[0].content)
        assert "".join(parts) == text
        with pytest.raises(ValueError):
            retrieval.get_window(
                vault_name=vault.name,
                session_id="paging",
                sequence_index=1,
                before=1,
                after=0,
                max_tokens=600,
                cursor=first_cursor,
            )
        with pytest.raises(ValueError):
            retrieval.get_window(
                vault_name=vault.name,
                session_id="paging",
                sequence_index=1,
                before=0,
                after=0,
                max_tokens=600,
                cursor=first_cursor[:-1] + ("A" if first_cursor[-1] != "A" else "B"),
            )
        runtime.chat_store.add_messages(
            "paging",
            vault.name,
            [
                ModelRequest(
                    parts=[
                        UserPromptPart(
                            content="A later revision invalidates prior cursors."
                        )
                    ]
                )
            ],
        )
        with pytest.raises(ValueError):
            retrieval.get_window(
                vault_name=vault.name,
                session_id="paging",
                sequence_index=1,
                before=0,
                after=0,
                max_tokens=600,
                cursor=first_cursor,
            )
        with pytest.raises(ValueError):
            retrieval.get_window(
                vault_name=vault.name, session_id="paging", sequence_index=1, before=11
            )
        with pytest.raises(LookupError):
            retrieval.get_window(
                vault_name=vault.name, session_id="paging", sequence_index=9999
            )
