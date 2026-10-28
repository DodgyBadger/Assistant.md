"""Real chat tool searches canonical transcript and bounded windows."""

import json

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.session_ops_chat import (
    configure_provider,
    session_ops_provider,
)
from validation.pytest_scenarios.support.transcript_chat_inputs import (
    call_transcript_tool,
    seed_transcripts,
)

pytestmark = pytest.mark.scenario


async def test_session_ops_transcript_search_and_windows(app: Application) -> None:
    vault = app.vault("SessionOpsTranscriptVault")
    (vault / "Projects/WorkspaceA").mkdir(parents=True)
    with session_ops_provider() as server:
        await app.start()
        from core.chat.transcript_retrieval import TranscriptRetrievalService
        from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
        from core.runtime.state import get_runtime_context

        configure_provider(app, server)
        runtime = get_runtime_context()
        seed_transcripts(runtime.chat_store, vault.name)

        search = await call_transcript_tool(
            app,
            server,
            vault.name,
            {
                "operation": "search_transcript",
                "session_id": "source",
                "query": "aurora covenant",
                "limit": 5,
            },
        )
        assert search["operation"] == "search_transcript"
        assert [row["sequence_index"] for row in search["matches"]] == [1]
        assert search["matches"][0]["is_in_compacted_prefix"] is True
        window = await call_transcript_tool(
            app,
            server,
            vault.name,
            {
                "operation": "get_transcript_window",
                "session_id": "source",
                "sequence_index": 1,
                "before": 1,
                "after": 1,
                "max_tokens": 1000,
            },
        )
        assert [row["sequence_index"] for row in window["messages"]] == [0, 1, 2]
        assert (
            window["messages"][1]["content"]
            == "The aurora-covenant decision was to preserve the original stone facade."
        )
        assert window["historical_content_is_untrusted"] is True
        structured = await call_transcript_tool(
            app,
            server,
            vault.name,
            {
                "operation": "search_transcript",
                "session_id": "structured",
                "query": "granite signal primary failure",
                "limit": 5,
            },
        )
        assert [row["sequence_index"] for row in structured["matches"]] == [0]
        assert structured["matches"][0]["source_kind"] == "tool_result" and structured[
            "matches"
        ][0]["tool_names"] == ["mail_probe"]
        mixed = await call_transcript_tool(
            app,
            server,
            vault.name,
            {
                "operation": "search_transcript",
                "session_id": "mixed",
                "query": "cedar invoice",
                "limit": 5,
            },
        )
        assert [row["sequence_index"] for row in mixed["matches"]] == [0]
        assert mixed["matches"][0]["tool_names"] == [
            "mail_probe"
        ] and "retrieval-only-marker" not in json.dumps(mixed)
        deep = await call_transcript_tool(
            app,
            server,
            vault.name,
            {"operation": "search_sessions", "query": "aurora covenant", "limit": 5},
        )
        found = next(row for row in deep["matches"] if row["session_id"] == "source")
        assert [
            row["sequence_index"]
            for row in found["evidence"]
            if row["source"] == "transcript"
        ] == [1]
        assert all(row["session_id"] != "foreign" for row in deep["matches"])
        assert deep["historical_content_is_untrusted"] is True
        listing = await call_transcript_tool(
            app, server, vault.name, {"operation": "list_sessions"}
        )
        assert all(row["session_id"] != "foreign" for row in listing["sessions"])
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            retrieval = TranscriptRetrievalService(
                runtime.chat_store, runtime.chat_session_access
            )
            assert (
                retrieval.search(
                    vault_name=vault.name,
                    session_id="mixed",
                    query="retrieval only marker",
                )
                == []
            )
            page = retrieval.get_window(
                vault_name=vault.name,
                session_id="mixed",
                sequence_index=0,
                before=0,
                after=0,
                max_tokens=512,
            )
            parts = [page.messages[0].content]
            while page.next_cursor:
                page = retrieval.get_window(
                    vault_name=vault.name,
                    session_id="mixed",
                    sequence_index=0,
                    before=0,
                    after=0,
                    max_tokens=512,
                    cursor=page.next_cursor,
                )
                parts.append(page.messages[0].content)
            assert "safe-tail-terminus" in "".join(
                parts
            ) and "retrieval-only-marker" not in "".join(parts)
