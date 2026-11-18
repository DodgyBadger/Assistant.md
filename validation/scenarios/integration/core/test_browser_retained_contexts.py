"""Real browser state belongs to an authorized chat and holds shared capacity."""

from __future__ import annotations

import asyncio
import json

import pytest

from validation.support.application import Application
from validation.support.browser.browser_runtime import descendant_identities
from validation.support.browser.handoff import handoff_path, waiting_handoff
from validation.support.browser.retained_site import (
    browser_turn,
    retained_browser_site,
    wait_processes_gone,
)

pytestmark = pytest.mark.scenario


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
        direct = await browser_turn(
            app,
            vault.name,
            "direct-private",
            url=f"http://127.0.0.1:{site.port}/denied-target",
        )
        assert (
            isinstance(direct, ToolReturn)
            and direct.metadata["reason"] == "destination_denied"
        )
        assert direct.metadata["help_eligible"] is False
        assert site.private_requests == []
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
        baseline = {row["pid"] for row in descendant_identities()}
        first = await browser_turn(
            app, vault.name, "deleted-chat", url=site.url("/prime")
        )
        assert isinstance(first, str)
        operation = asyncio.create_task(
            browser_turn(app, vault.name, "deleted-chat", operation="handoff")
        )
        state = await waiting_handoff(app, operation, "deleted-chat")
        path = handoff_path("deleted-chat") + "/" + state["handoff_id"]
        owned = [row for row in descendant_identities() if row["pid"] not in baseline]
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
        async with asyncio.timeout(5):
            with pytest.raises(asyncio.CancelledError):
                await operation
        assert operation.done()
        assert (await app.request_async(path + "/return", "POST")).status_code == 404
        await wait_processes_gone(owned)
        app.events.require(
            "browser_context_closed", after=checkpoint, session_id="deleted-chat"
        )
        fresh = await browser_turn(
            app, vault.name, "deleted-chat", url=site.url("/state")
        )
        assert isinstance(fresh, str) and json.loads(fresh)["text"] == "Signed out"
        assert json.loads(fresh)["browser_id"] != json.loads(first)["browser_id"]
        await browser_turn(app, vault.name, "deleted-chat", operation="close")
