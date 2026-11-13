"""Real browser state belongs to an authorized chat and holds shared capacity."""

from __future__ import annotations

import json

import pytest

from validation.support.application import Application
from validation.support.browser.retained_site import browser_turn, retained_browser_site

pytestmark = pytest.mark.scenario


async def test_browser_retains_site_session_across_chat_turns(
    app: Application,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = app.vault("RetainedBrowserVault")
    await app.start()
    assert (
        app.request(
            "/api/system/settings/general/browser_min_memory_headroom_mb",
            "PUT",
            {"value": "0"},
        ).status_code
        == 200
    )
    with retained_browser_site(app, monkeypatch) as site:
        first = await browser_turn(
            app, vault.name, "retained-chat", url=site.url("/prime")
        )
        assert isinstance(
            first, str
        ), "Interactive browser must return a real page observation"
        first_view = json.loads(first)
        assert first_view["text"] == "Session established"
        second = await browser_turn(
            app, vault.name, "retained-chat", url=site.url("/state")
        )
        assert isinstance(second, str)
        second_view = json.loads(second)
        assert second_view["text"] == "Signed in"
        assert second_view["browser_id"] == first_view["browser_id"]
        closed = await browser_turn(app, vault.name, "retained-chat", operation="close")
        assert isinstance(closed, str) and json.loads(closed)["status"] == "closed"


async def test_browser_contexts_isolate_chats_and_close_discards_authentication(
    app: Application,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = app.vault("IsolatedBrowserVault")
    await app.start()
    for key, value in (
        ("browser_min_memory_headroom_mb", "0"),
        ("browser_max_concurrent_sessions", "2"),
    ):
        assert (
            app.request(
                f"/api/system/settings/general/{key}", "PUT", {"value": value}
            ).status_code
            == 200
        )
    with retained_browser_site(app, monkeypatch) as site:
        first = await browser_turn(app, vault.name, "chat-a", url=site.url("/prime"))
        assert isinstance(first, str)
        other = await browser_turn(app, vault.name, "chat-b", url=site.url("/state"))
        assert isinstance(other, str)
        assert json.loads(other)["text"] == "Signed out"
        assert json.loads(first)["browser_id"] != json.loads(other)["browser_id"]
        for _ in range(2):
            closed = await browser_turn(app, vault.name, "chat-a", operation="close")
            assert isinstance(closed, str) and json.loads(closed)["status"] == "closed"
        fresh = await browser_turn(app, vault.name, "chat-a", url=site.url("/state"))
        assert isinstance(fresh, str)
        assert json.loads(fresh)["text"] == "Signed out"
        assert json.loads(fresh)["browser_id"] != json.loads(first)["browser_id"]
        for chat in ("chat-a", "chat-b"):
            await browser_turn(app, vault.name, chat, operation="close")


async def test_retained_browser_holds_capacity_shared_with_extraction(
    app: Application,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pydantic_ai.messages import ToolReturn

    from core.tools.browser import BrowserTool

    vault = app.vault("BrowserCapacityVault")
    await app.start()
    assert (
        app.request(
            "/api/system/settings/general/browser_min_memory_headroom_mb",
            "PUT",
            {"value": "0"},
        ).status_code
        == 200
    )
    with retained_browser_site(app, monkeypatch) as site:
        first = await browser_turn(
            app, vault.name, "capacity-owner", url=site.url("/prime")
        )
        assert isinstance(first, str)
        # A retained lease stays admitted after its first task completes.
        extraction = await BrowserTool.get_tool().function(url="ftp://invalid.example/")
        assert (
            isinstance(extraction, ToolReturn)
            and extraction.metadata["reason"] == "capacity_limit"
        )
        refused = await browser_turn(
            app, vault.name, "capacity-other", url=site.url("/state")
        )
        assert (
            isinstance(refused, ToolReturn)
            and refused.metadata["reason"] == "capacity_limit"
        )
        again = await browser_turn(app, vault.name, "capacity-owner")
        assert (
            isinstance(again, str)
            and json.loads(again)["browser_id"] == json.loads(first)["browser_id"]
        )
        await browser_turn(app, vault.name, "capacity-owner", operation="close")
        fresh = await browser_turn(
            app, vault.name, "capacity-other", url=site.url("/state")
        )
        assert isinstance(fresh, str) and json.loads(fresh)["text"] == "Signed out"
        await browser_turn(app, vault.name, "capacity-other", operation="close")


async def test_browser_redirect_cannot_request_private_destination(
    app: Application,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pydantic_ai.messages import ToolReturn

    vault = app.vault("BrowserRedirectVault")
    await app.start()
    assert (
        app.request(
            "/api/system/settings/general/browser_min_memory_headroom_mb",
            "PUT",
            {"value": "0"},
        ).status_code
        == 200
    )
    with retained_browser_site(app, monkeypatch) as site:
        checkpoint = app.events.checkpoint()
        result = await browser_turn(
            app, vault.name, "redirect-chat", url=site.url("/redirect-private")
        )
        assert isinstance(result, ToolReturn) and result.metadata["status"] == "failed"
        assert site.private_requests == []
        app.events.require(
            "browser_connection_refused", after=checkpoint, reason="destination_denied"
        )
        # Failed first navigation must release capacity and leave retry possible.
        recovered = await browser_turn(
            app, vault.name, "recovered-chat", url=site.url("/state")
        )
        assert (
            isinstance(recovered, str) and json.loads(recovered)["text"] == "Signed out"
        )
        await browser_turn(app, vault.name, "recovered-chat", operation="close")


@pytest.mark.parametrize("purge", [False, True])
async def test_chat_deletion_releases_browser_and_prevents_state_resurrection(
    app: Application,
    monkeypatch: pytest.MonkeyPatch,
    purge: bool,
) -> None:
    vault = app.vault("BrowserDeletionVault")
    await app.start()
    assert (
        app.request(
            "/api/system/settings/general/browser_min_memory_headroom_mb",
            "PUT",
            {"value": "0"},
        ).status_code
        == 200
    )
    with retained_browser_site(app, monkeypatch) as site:
        first = await browser_turn(
            app, vault.name, "deleted-chat", url=site.url("/prime")
        )
        assert isinstance(first, str)
        checkpoint = app.events.checkpoint()
        if purge:
            response = await app.request_async(
                "/api/chat/sessions/purge", "POST", {"vault_name": vault.name}
            )
        else:
            response = await app.request_async(
                "/api/chat/sessions/deleted-chat",
                "DELETE",
                params={"vault_name": vault.name},
            )
        assert response.status_code == 200, response.text
        app.events.require(
            "browser_context_closed", after=checkpoint, session_id="deleted-chat"
        )
        fresh = await browser_turn(
            app, vault.name, "deleted-chat", url=site.url("/state")
        )
        assert isinstance(fresh, str) and json.loads(fresh)["text"] == "Signed out"
        assert json.loads(fresh)["browser_id"] != json.loads(first)["browser_id"]
        await browser_turn(app, vault.name, "deleted-chat", operation="close")


async def test_browser_shutdown_closes_stalled_proxy_handshake(
    app: Application,
) -> None:
    import asyncio
    from urllib.parse import urlsplit

    from core.runtime.state import get_runtime_context

    await app.start()
    service = get_runtime_context().browser
    proxy = urlsplit(await service.proxy_url())
    reader, writer = await asyncio.open_connection(proxy.hostname, proxy.port)
    writer.write(b"\x05")
    await writer.drain()
    shutdown = asyncio.create_task(service.shutdown())
    try:
        done, _ = await asyncio.wait({shutdown}, timeout=1)
        assert (
            shutdown in done
        ), "A stalled proxy client must not block its own shutdown"
        await shutdown
        assert await reader.read() == b""
    finally:
        writer.close()
        await writer.wait_closed()
        await shutdown


async def test_cancelled_browser_construction_preserves_stop_after_process_failure(
    app: Application,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    import os
    import signal

    from core.runtime.execution_tasks import chat_session_scope
    from core.runtime.state import get_runtime_context
    from validation.support.browser.retained_site import paused_browser_driver

    vault = app.vault("BrowserCancelledConstructionVault")
    await app.start()
    assert (
        app.request(
            "/api/system/settings/general/browser_min_memory_headroom_mb",
            "PUT",
            {"value": "0"},
        ).status_code
        == 200
    )
    with (
        retained_browser_site(app, monkeypatch) as site,
        paused_browser_driver(app) as paused,
    ):
        operation = asyncio.create_task(
            browser_turn(app, vault.name, "cancelled-build", url=site.url("/state"))
        )
        try:
            async with asyncio.timeout(8):
                await paused.ready.wait()
            runtime = get_runtime_context()
            tasks = await runtime.task_coordinator.list_tasks(
                scope=chat_session_scope("cancelled-build"), include_terminal=False
            )
            assert len(tasks) == 1
            await runtime.task_coordinator.cancel_task(tasks[0].task_id)
            assert paused.browser_pid is not None
            os.kill(paused.browser_pid, signal.SIGKILL)
            paused.resume()
            with pytest.raises(asyncio.CancelledError):
                await operation
        finally:
            paused.resume()
            if not operation.done():
                operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)


async def test_unresponsive_browser_construction_has_owned_deadline(
    app: Application,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from pydantic_ai.messages import ToolReturn

    from validation.support.browser.retained_site import paused_browser_driver

    vault = app.vault("BrowserConstructionDeadlineVault")
    await app.start()
    assert (
        app.request(
            "/api/system/settings/general/browser_min_memory_headroom_mb",
            "PUT",
            {"value": "0"},
        ).status_code
        == 200
    )
    with (
        retained_browser_site(app, monkeypatch) as site,
        paused_browser_driver(app) as paused,
    ):
        operation = asyncio.create_task(
            browser_turn(app, vault.name, "stalled-build", url=site.url("/state"))
        )
        try:
            async with asyncio.timeout(8):
                await paused.ready.wait()
            # The production context allocation deadline must fire while its
            # real IPC peer is unresponsive, before we permit cleanup to run.
            async with asyncio.timeout(7):
                await paused.construction_deadline.wait()
            paused.resume()
            async with asyncio.timeout(8):
                result = await operation
            assert (
                isinstance(result, ToolReturn) and result.metadata["status"] == "failed"
            )
            # An allocation with an uncertain remote outcome closes admission.
            retry = await browser_turn(
                app, vault.name, "after-stall", url=site.url("/state")
            )
            assert (
                isinstance(retry, ToolReturn)
                and retry.metadata["reason"] == "runtime_closed"
            )
        finally:
            paused.resume()
            if not operation.done():
                operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
