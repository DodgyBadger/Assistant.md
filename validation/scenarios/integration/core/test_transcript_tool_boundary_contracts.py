"""Session-ops tool enforces transcript access, output and input bounds."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.session_ops_chat import (
    configure_provider,
    session_ops_provider,
)
from validation.scenarios.support.transcript_boundary_inputs import (
    seed_boundaries,
    tool_context,
)
from validation.scenarios.support.transcript_chat_inputs import call_transcript_tool

pytestmark = pytest.mark.scenario


async def test_real_chat_tool_enforces_workspace_visibility_and_title_bound(
    app: Application,
) -> None:
    vault = app.vault("TranscriptToolBoundaryVault")
    (vault / "Projects/WorkspaceA").mkdir(parents=True)
    with session_ops_provider() as server:
        await app.start()
        from core.runtime.state import get_runtime_context

        configure_provider(app, server)
        seed_boundaries(get_runtime_context().chat_store, vault.name)
        result = await call_transcript_tool(
            app,
            server,
            vault.name,
            {
                "operation": "search_sessions",
                "query": "wetland",
                "filter": {"workspace": "current"},
            },
        )
        assert [item["session_id"] for item in result["matches"]] == ["source"]
        assert result["historical_content_is_untrusted"] is True
        all_visible = await call_transcript_tool(
            app,
            server,
            vault.name,
            {"operation": "search_sessions", "query": "wetland"},
        )
        assert {item["session_id"] for item in all_visible["matches"]} == {
            "source",
            "sibling",
        }
        listed = await call_transcript_tool(
            app, server, vault.name, {"operation": "list_sessions", "limit": 100}
        )
        assert "foreign" not in {item["session_id"] for item in listed["sessions"]}
        source = next(
            item for item in listed["sessions"] if item["session_id"] == "source"
        )
        assert len(source["title"]) <= 243
        assert source["workspace_path"] == "Projects/WorkspaceA"
        app.events.require(
            "tool_invoked", tool="session_ops", operation="search_sessions"
        )


async def test_real_chat_tool_continues_bounded_transcript_fragments(
    app: Application,
) -> None:
    vault = app.vault("TranscriptToolPaginationVault")
    (vault / "Projects/WorkspaceA").mkdir(parents=True)
    with session_ops_provider() as server:
        await app.start()
        from core.runtime.state import get_runtime_context

        configure_provider(app, server)
        expected = seed_boundaries(get_runtime_context().chat_store, vault.name)
        arguments = {
            "operation": "get_transcript_window",
            "session_id": "long",
            "sequence_index": 0,
            "before": 0,
            "after": 0,
            "max_tokens": 600,
        }
        pieces = []
        cursor = None
        for _ in range(20):
            page = await call_transcript_tool(
                app,
                server,
                vault.name,
                {**arguments, **({"cursor": cursor} if cursor else {})},
            )
            assert (
                page["status"] == "ok"
                and page["historical_content_is_untrusted"] is True
            )
            assert page["estimated_tokens"] <= 600 and page["max_tokens"] == 600
            assert (
                len(page["messages"]) == 1
                and page["messages"][0]["sequence_index"] == 0
            )
            message = page["messages"][0]
            assert message["content_start"] == sum(len(piece) for piece in pieces)
            pieces.append(message["content"])
            assert message["content_end"] == sum(len(piece) for piece in pieces)
            # A continuation fragment is not a complete message, even at its end.
            assert message["content_complete"] is False
            cursor = page["next_cursor"]
            if cursor is None:
                assert message["content_end"] == len(expected)
                break
        else:
            raise AssertionError("Transcript fragments did not terminate")
        assert len(pieces) > 1 and "".join(pieces) == expected
        assert server.calls


async def test_tool_rejects_oversized_query_and_foreign_session(
    app: Application,
) -> None:
    vault = app.vault("TranscriptToolRejectionVault")
    await app.start()
    from pydantic_ai import ModelRetry

    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.runtime.state import get_runtime_context
    from core.tools.session_ops import MAX_SESSION_SEARCH_QUERY_CHARS, SessionOps

    seed_boundaries(get_runtime_context().chat_store, vault.name)
    tool = SessionOps.get_tool(str(vault)).function
    context = tool_context(vault.name)
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        for operation in ("search_transcript", "search_sessions"):
            with pytest.raises(ModelRetry):
                await tool(
                    context,
                    operation=operation,
                    session_id="source",
                    query="q" * (MAX_SESSION_SEARCH_QUERY_CHARS + 1),
                )
        for operation, arguments in (
            ("search_transcript", {"query": "wetland"}),
            ("get_transcript_window", {"sequence_index": 0}),
        ):
            with pytest.raises(ModelRetry):
                await tool(
                    context, operation=operation, session_id="foreign", **arguments
                )
