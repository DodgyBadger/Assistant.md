"""Explicit browser termination releases human-owned sessions without handback."""

import asyncio
import json

import pytest

from validation.support.browser.handoff import handoff_path, waiting_handoff
from validation.support.browser.retained_site import browser_turn, retained_browser_site

pytestmark = pytest.mark.scenario


@pytest.mark.parametrize("owner", ["waiting", "human", "stopped"])
@pytest.mark.parametrize("actor", ["agent", "viewer"])
async def test_explicit_termination_releases_only_its_chat_and_cancels_waiting_task(
    app, monkeypatch, owner, actor
):
    from core.runtime.state import get_runtime_context

    vault = app.vault("BrowserTerminationVault")
    await app.start()
    assert (
        app.request(
            "/api/system/settings/general/browser_min_memory_headroom_mb",
            "PUT",
            {"value": "0"},
        ).status_code
        == 200
    )
    assert (
        app.request(
            "/api/system/settings/general/browser_max_concurrent_sessions",
            "PUT",
            {"value": "2"},
        ).status_code
        == 200
    )
    with retained_browser_site(app, monkeypatch) as site:
        original = json.loads(
            await browser_turn(app, vault.name, "terminate", url=site.url("/prime"))
        )
        other = json.loads(
            await browser_turn(app, vault.name, "other-chat", url=site.url("/other"))
        )
        waiting = asyncio.create_task(
            browser_turn(app, vault.name, "terminate", operation="handoff")
        )
        try:
            state = await waiting_handoff(app, waiting, "terminate")
            path = handoff_path("terminate") + "/" + state["handoff_id"]
            if owner != "waiting":
                assert (
                    await app.request_async(path + "/claim", "POST")
                ).status_code == 200
            if owner == "stopped":
                await get_runtime_context().task_coordinator.cancel_task(
                    state["task_id"]
                )
                with pytest.raises(asyncio.CancelledError):
                    await waiting
            if actor == "agent":
                result = await browser_turn(
                    app, vault.name, "terminate", operation="close"
                )
                assert isinstance(
                    result, str
                ), "The owning agent must be able to end a human-owned browser"
                assert json.loads(result)["status"] == "closed"
            else:
                result = await app.request_async(path + "/close", "POST")
                assert result.status_code == 200
                assert result.json()["status"] == "closed"
            with pytest.raises(asyncio.CancelledError):
                await waiting
            task = await get_runtime_context().task_coordinator.get_task(
                state["task_id"]
            )
            assert task is not None and task.status == "cancelled"
            assert (
                await app.request_async(path + "/return", "POST")
            ).status_code == 404
            assert (await app.request_async(path + "/claim", "POST")).status_code == 404
            assert (await app.request_async(path + "/frame")).status_code == 404
            retained_other = json.loads(
                await browser_turn(app, vault.name, "other-chat")
            )
            assert retained_other["browser_id"] == other["browser_id"]
            fresh = json.loads(
                await browser_turn(app, vault.name, "terminate", url=site.url("/new"))
            )
            assert fresh["browser_id"] != original["browser_id"]
            assert (await app.request_async(path + "/close", "POST")).status_code in {
                404,
                409,
            }
            still_fresh = json.loads(await browser_turn(app, vault.name, "terminate"))
            assert still_fresh["browser_id"] == fresh["browser_id"]
        finally:
            if not waiting.done():
                waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)
            for session in ("terminate", "other-chat"):
                await browser_turn(app, vault.name, session, operation="close")


async def test_cleanup_is_available_after_browser_call_budget_is_exhausted(
    app, monkeypatch
):
    from pydantic_ai.messages import ToolReturn

    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.runtime.execution_tasks import (
        ExecutionTaskKind,
        ExecutionTaskSource,
        chat_session_scope,
    )
    from core.runtime.state import get_runtime_context
    from core.tools.browser import BrowserTool

    vault = app.vault("BrowserCloseBudgetVault")
    await app.start()
    for key, value in (
        ("browser_min_memory_headroom_mb", "0"),
        ("browser_max_calls_per_turn", "1"),
    ):
        assert (
            app.request(
                f"/api/system/settings/general/{key}", "PUT", {"value": value}
            ).status_code
            == 200
        )
    runtime = get_runtime_context()
    with (
        retained_browser_site(app, monkeypatch) as site,
        use_execution_authority(LOCAL_USER_AUTHORITY),
    ):
        runtime.chat_session_access.ensure_session("close-budget", vault.name)
        async with runtime.task_coordinator.track_current_task(
            kind=ExecutionTaskKind.CHAT,
            scope=chat_session_scope("close-budget"),
            source=ExecutionTaskSource.API,
            label="Browser cleanup budget",
            authority=LOCAL_USER_AUTHORITY,
        ):
            tool = BrowserTool.get_tool()
            assert isinstance(
                await tool.function(interactive=True, url=site.url("/prime")), str
            )
            refused = await tool.function(interactive=True)
            assert (
                isinstance(refused, ToolReturn)
                and refused.metadata["reason"] == "task_call_limit"
            )
            closed = await tool.function(interactive=True, operation="close")
            assert isinstance(closed, str) and json.loads(closed)["status"] == "closed"
            app.events.require("browser_session_close_requested", source="agent")
            activity = app.request(
                "/api/system/activity-log", params={"limit": 100}
            ).json()
            assert any(
                row.get("data", {}).get("event") == "browser_session_close_requested"
                for row in activity["entries"]
            )


async def test_old_human_end_cannot_close_a_returned_browser(app, monkeypatch):
    from validation.support.browser.human_transport import claimed_browser

    async with claimed_browser(app, monkeypatch, session_id="returned-browser") as (
        site,
        client,
        waiting,
        path,
        state,
    ):
        assert (await client.post(path + "/return")).status_code == 200
        assert isinstance(await waiting, str)
        assert (await client.post(path + "/close")).status_code == 409
        page = json.loads(
            await browser_turn(
                app, app.vault("BrowserTransportBoundaryVault").name, "returned-browser"
            )
        )
        assert page["browser_id"] == state["browser_id"]
