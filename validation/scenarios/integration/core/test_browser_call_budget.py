"""Browser call budgets reject excess work before any browser is launched."""

import asyncio

import pytest

from validation.support.application import Application

pytestmark = pytest.mark.scenario


async def test_browser_budget_is_scoped_to_actual_execution_task(
    app: Application,
) -> None:
    await app.start()
    from pydantic_ai.messages import ToolReturn

    from core.identity import LOCAL_USER_AUTHORITY
    from core.runtime.execution_tasks import ExecutionTaskKind, ExecutionTaskSource
    from core.runtime.state import get_runtime_context
    from core.tools.browser import BrowserTool

    for key, value in (
        ("browser_max_calls_per_turn", "2"),
        ("browser_min_memory_headroom_mb", "0"),
    ):
        response = app.request(
            f"/api/system/settings/general/{key}", "PUT", {"value": value}
        )
        assert response.status_code == 200
    tool = BrowserTool.get_tool()
    coordinator = get_runtime_context().task_coordinator
    checkpoint = app.events.checkpoint()
    async with coordinator.track_current_task(
        kind=ExecutionTaskKind.CHAT,
        scope="browser-budget:first",
        source=ExecutionTaskSource.API,
        label="browser budget",
        authority=LOCAL_USER_AUTHORITY,
    ):
        for _ in range(2):
            # URL validation rejects this input before Playwright is imported.
            result = await tool.function(url="ftp://invalid.example/resource")
            assert (
                isinstance(result, ToolReturn) and result.metadata["status"] == "failed"
            )
        result = await tool.function(url="ftp://invalid.example/resource")
        assert isinstance(result, ToolReturn) and result.metadata["status"] == "failed"
    app.events.require(
        "browser_launch_refused",
        after=checkpoint,
        reason="task_call_limit",
        call_count=3,
        call_limit=2,
    )
    checkpoint = app.events.checkpoint()
    async with coordinator.track_current_task(
        kind=ExecutionTaskKind.CHAT,
        scope="browser-budget:second",
        source=ExecutionTaskSource.API,
        label="new browser budget",
        authority=LOCAL_USER_AUTHORITY,
    ):
        result = await tool.function(url="ftp://invalid.example/resource")
        assert isinstance(result, ToolReturn) and result.metadata["status"] == "failed"
    assert not any(
        row["name"] == "browser_launch_refused"
        for row in app.events.select(after=checkpoint)
    )


async def test_browser_capacity_refuses_without_queueing_and_releases_after_denial(
    app: Application,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await app.start()
    from pydantic_ai.messages import ToolReturn

    from core.tools.browser import BrowserTool
    from validation.support.browser.admission import held_browser_dns

    for key, value in (
        ("browser_max_concurrent_sessions", "1"),
        ("browser_max_calls_per_turn", "0"),
        ("browser_min_memory_headroom_mb", "0"),
    ):
        assert (
            app.request(
                f"/api/system/settings/general/{key}", "PUT", {"value": value}
            ).status_code
            == 200
        )

    tool = BrowserTool.get_tool()
    checkpoint = app.events.checkpoint()
    with held_browser_dns(monkeypatch) as held:
        first = asyncio.create_task(asyncio.to_thread(held.call))
        try:
            assert await asyncio.to_thread(
                held.entered.wait, 5
            ), "First request did not reach DNS"
            # A held real admission must cause explicit refusal, not queueing
            # behind external I/O or consuming another browser session.
            async with asyncio.timeout(1):
                refused = await tool.function(url="ftp://invalid.example/second")
            assert isinstance(refused, ToolReturn)
            assert refused.metadata["status"] == "failed"
            assert refused.metadata["reason"] == "capacity_limit"
            activity = app.request("/api/system/activity-log", params={"limit": 100})
            assert activity.status_code == 200
            assert any(
                row.get("data", {}).get("event") == "browser_launch_refused"
                and row["data"].get("status") == "refused"
                and row["data"].get("reason") == "capacity_limit"
                for row in activity.json()["entries"]
            )
            app.events.require(
                "browser_launch_refused",
                after=checkpoint,
                reason="capacity_limit",
                session_limit=1,
            )
        finally:
            held.release.set()
            first_result = await first
        assert isinstance(first_result, ToolReturn)
        assert first_result.metadata["status"] == "failed"
        app.events.require(
            "browser_navigation_failed",
            after=checkpoint,
            url=held.url,
            result_type="blocked",
        )

    checkpoint = app.events.checkpoint()
    result = await tool.function(url="ftp://invalid.example/after-release")
    assert isinstance(result, ToolReturn) and result.metadata["status"] == "failed"
    assert not app.events.select("browser_launch_refused", after=checkpoint)
    app.events.require(
        "browser_navigation_failed",
        after=checkpoint,
        result_type="blocked",
    )
    app.evidence.save_json(
        "admission-boundary.json",
        {
            "external_substitution": "one delayed DNS answer resolving to a denied loopback address",
            "real_boundaries": [
                "runtime",
                "settings",
                "tool",
                "admission",
                "URL policy",
                "failure result",
            ],
            "does_not_prove": [
                "Chromium launch",
                "process cleanup",
                "interactive context retention",
            ],
        },
    )


@pytest.mark.parametrize("cancel_shutdown", [False, True])
async def test_runtime_shutdown_drains_inflight_browser_admission(
    app: Application,
    monkeypatch: pytest.MonkeyPatch,
    cancel_shutdown: bool,
) -> None:
    await app.start()
    from core.runtime.state import get_runtime_context, has_runtime_context
    from validation.support.browser.admission import held_browser_dns

    assert (
        app.request(
            "/api/system/settings/general/browser_min_memory_headroom_mb",
            "PUT",
            {"value": "0"},
        ).status_code
        == 200
    )
    runtime = get_runtime_context()
    shutdown = None
    with held_browser_dns(monkeypatch) as held:
        first = asyncio.create_task(asyncio.to_thread(held.call))
        try:
            assert await asyncio.to_thread(held.entered.wait, 5)
            shutdown = asyncio.create_task(runtime.shutdown())
            done, _ = await asyncio.wait({shutdown}, timeout=0.05)
            assert not done, "Shutdown must wait for in-flight browser resource release"
            if cancel_shutdown:
                shutdown.cancel()
                done, _ = await asyncio.wait({shutdown}, timeout=0.05)
                assert not done, "Cancellation must not abandon browser cleanup"
        finally:
            held.release.set()
            await first
            if shutdown is not None:
                if cancel_shutdown:
                    with pytest.raises(asyncio.CancelledError):
                        await shutdown
                else:
                    await shutdown
    assert not has_runtime_context()
    assert not runtime.scheduler.running
