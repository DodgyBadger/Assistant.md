"""Oversized real tool output and repeated local exploration in chat."""

from datetime import datetime

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.chat_cache_journeys import code_turn
from validation.pytest_scenarios.support.code_tool_chat import (
    code_tool_provider,
    configure_provider,
)

pytestmark = pytest.mark.scenario


async def test_chat_tool_overflow_cache_and_readback(app: Application) -> None:
    vault = app.vault("ChatToolOverflowCacheVault")
    with code_tool_provider() as server:
        await app.start()
        from core.authoring.cache import get_cache_artifact
        from core.runtime.state import get_runtime_context

        configure_provider(app, server)
        updated = app.request(
            "/api/system/settings/general/auto_cache_max_tokens",
            method="PUT",
            data={"value": "50"},
        )
        assert updated.status_code == 200
        session = "chat_tool_overflow_cache_session"
        first = await code_turn(
            app, server, vault.name, session, '"OVERFLOW_SEGMENT " * 1200'
        )
        store = get_runtime_context().chat_store
        events = store.get_tool_events(
            session_id=session, vault_name=vault.name, limit=20
        )
        cached = [event for event in events if event.event_type == "overflow_cached"]
        assert len(cached) == 1
        event = cached[0]
        ref = event.artifact_ref
        assert ref and ref.startswith("tool/code_execution/")
        notice = event.result_text or ""
        assert "stored in cache ref '" in notice and ref in notice
        assert (
            "Preview:" in notice
            and "artifact.content" in notice
            and "code_execution" in notice
        )
        assert "buffer" not in notice.lower()
        assert ref in first["text"]
        artifact = get_cache_artifact(
            owner_id=f"{vault.name}/chat/{session}",
            session_key=session,
            artifact_ref=ref,
            now=datetime.now(),
            week_start_day=0,
            system_root=get_runtime_context().config.system_root,
        )
        assert artifact is not None
        assert (
            artifact["origin"] == "chat_tool_overflow"
            and artifact["metadata"]["tool_name"] == "code_execution"
        )
        assert "OVERFLOW_SEGMENT" in artifact["raw_content"] and len(
            artifact["raw_content"]
        ) > len(notice)
        second = await code_turn(
            app,
            server,
            vault.name,
            session,
            f'artifact = await read_cache(ref={ref!r})\nstr(artifact.content.count("OVERFLOW_SEGMENT")) if artifact.exists else "CACHE_NOT_FOUND"',
        )
        assert "1200" in second["text"] and "CACHE_NOT_FOUND" not in second["text"]


async def test_chat_cache_multi_pass_file_exploration(app: Application) -> None:
    vault = app.vault("ChatCacheMultiPassVault")
    app.write(vault, "notes/repeated.md", "OVERFLOW_SEGMENT " * 1200)
    with code_tool_provider() as server:
        await app.start()
        configure_provider(app, server)
        updated = app.request(
            "/api/system/settings/general/auto_cache_max_tokens",
            method="PUT",
            data={"value": "50"},
        )
        assert updated.status_code == 200
        session = "chat_cache_multi_pass_session"
        checkpoint = app.events.checkpoint()
        first = await code_turn(
            app, server, vault.name, session, '"OVERFLOW_SEGMENT " * 1200'
        )
        assert first["terminal_event"]["event"] == "done"
        second = await code_turn(
            app,
            server,
            vault.name,
            session,
            'artifact = await file_read(operation="read", path="notes/repeated.md")\nartifact.return_value[:80]',
        )
        assert "OVERFLOW_SEGMENT" in second["text"]
        third = await code_turn(
            app,
            server,
            vault.name,
            session,
            'artifact = await file_read(operation="read", path="notes/repeated.md")\nstr(artifact.return_value.count("OVERFLOW_SEGMENT"))',
        )
        assert "1200" in third["text"]
        events = app.events.select(after=checkpoint)
        assert (
            sum(
                row["name"] == "tool_invoked"
                and row["data"].get("tool") == "code_execution"
                for row in events
            )
            == 3
        )
