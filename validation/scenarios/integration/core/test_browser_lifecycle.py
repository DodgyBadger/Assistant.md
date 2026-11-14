"""A real idle worker releases retained authentication and respects active use."""

from __future__ import annotations

import asyncio
import json

import pytest

from validation.support.application import Application
from validation.support.browser.retained_site import (
    browser_turn,
    retained_browser_site,
    wait_browser_event,
)

pytestmark = pytest.mark.scenario


async def test_browser_inactivity_expires_authentication_and_releases_capacity(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = app.vault("BrowserIdleExpiryVault")
    await app.start()
    for key, value in (
        ("browser_min_memory_headroom_mb", "0"),
        ("browser_idle_timeout_seconds", "1"),
    ):
        saved = app.request(
            f"/api/system/settings/general/{key}", "PUT", {"value": value}
        )
        assert saved.status_code == 200
    with retained_browser_site(app, monkeypatch) as site:
        first = await browser_turn(
            app, vault.name, "idle-owner", url=site.url("/prime")
        )
        assert isinstance(first, str)
        await wait_browser_event(
            app, "browser_context_expired", session_id="idle-owner"
        )
        fresh = await browser_turn(
            app, vault.name, "idle-owner", url=site.url("/state")
        )
        assert isinstance(fresh, str)
        assert json.loads(fresh)["text"] == "Signed out"
        assert json.loads(fresh)["browser_id"] != json.loads(first)["browser_id"]
        await browser_turn(app, vault.name, "idle-owner", operation="close")
        other = await browser_turn(
            app, vault.name, "idle-other", url=site.url("/state")
        )
        assert isinstance(other, str)
        await browser_turn(app, vault.name, "idle-other", operation="close")


async def test_browser_activity_refreshes_idle_clock_beyond_total_interval(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = app.vault("BrowserIdleActivityVault")
    await app.start()
    for key, value in (
        ("browser_min_memory_headroom_mb", "0"),
        ("browser_idle_timeout_seconds", "2"),
    ):
        assert (
            app.request(
                f"/api/system/settings/general/{key}", "PUT", {"value": value}
            ).status_code
            == 200
        )
    with retained_browser_site(app, monkeypatch) as site:
        first = await browser_turn(
            app, vault.name, "active-owner", url=site.url("/prime")
        )
        assert isinstance(first, str)
        for _ in range(4):
            await asyncio.sleep(0.7)
            current = await browser_turn(
                app, vault.name, "active-owner", url=site.url("/state")
            )
            assert isinstance(current, str)
            assert json.loads(current)["text"] == "Signed in"
            assert json.loads(current)["browser_id"] == json.loads(first)["browser_id"]
        assert not app.events.select("browser_context_expired")
        await wait_browser_event(
            app, "browser_context_expired", session_id="active-owner"
        )


async def test_browser_restart_reaps_owned_processes_and_discards_authentication(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    from validation.support.browser.browser_runtime import descendant_identities
    from validation.support.browser.retained_site import wait_processes_gone

    vault = app.vault("BrowserRestartVault")
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
        baseline = {r["pid"] for r in descendant_identities()}
        first = await browser_turn(
            app, vault.name, "restart-owner", url=site.url("/prime")
        )
        assert isinstance(first, str)
        owned = [r for r in descendant_identities() if r["pid"] not in baseline]
        assert owned, "A real browser process tree must have started"
        app.evidence.save_json("restart-browser-processes.json", owned)
        await app.controller.restart_system()
        await wait_processes_gone(owned)
        fresh = await browser_turn(
            app, vault.name, "restart-owner", url=site.url("/state")
        )
        assert isinstance(fresh, str)
        assert json.loads(fresh)["text"] == "Signed out"
        assert json.loads(fresh)["browser_id"] != json.loads(first)["browser_id"]
        await browser_turn(app, vault.name, "restart-owner", operation="close")


@pytest.mark.parametrize("contexts", [1, 2])
async def test_crashed_browser_releases_dead_context_for_explicit_retry(
    app: Application, monkeypatch: pytest.MonkeyPatch, contexts: int
) -> None:
    import os
    import signal
    from pathlib import Path

    from pydantic_ai.messages import ToolReturn

    from validation.support.browser.browser_runtime import descendant_identities
    from validation.support.browser.retained_site import wait_processes_gone

    vault = app.vault("BrowserCrashVault")
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
            {"value": str(contexts)},
        ).status_code
        == 200
    )
    with retained_browser_site(app, monkeypatch) as site:
        baseline = {r["pid"] for r in descendant_identities()}
        first = await browser_turn(
            app, vault.name, "crash-owner", url=site.url("/prime")
        )
        assert isinstance(first, str)
        if contexts == 2:
            other = await browser_turn(
                app, vault.name, "other-crash-owner", url=site.url("/prime")
            )
            assert isinstance(other, str)
        owned = [r for r in descendant_identities() if r["pid"] not in baseline]
        drivers = [r for r in owned if r["parent"] == os.getpid()]
        assert len(drivers) == 1
        browsers = [
            r
            for r in owned
            if r["parent"] == drivers[0]["pid"]
            and Path(f"/proc/{r['pid']}/comm").read_text().strip() == "chrome"
        ]
        assert len(browsers) == 1
        # Kill only this scenario's main browser; its real driver remains able
        # to observe the exit and clean up its children and pipe transport.
        os.kill(browsers[0]["pid"], signal.SIGKILL)
        await wait_processes_gone(browsers)
        failed = await browser_turn(app, vault.name, "crash-owner")
        assert (
            isinstance(failed, ToolReturn)
            and failed.metadata["reason"] == "browser_closed"
        )
        await wait_processes_gone(owned)
        fresh = await browser_turn(
            app, vault.name, "crash-owner", url=site.url("/state")
        )
        assert isinstance(fresh, str) and json.loads(fresh)["text"] == "Signed out"
        assert json.loads(fresh)["browser_id"] != json.loads(first)["browser_id"]
        if contexts == 2:
            other_fresh = await browser_turn(
                app, vault.name, "other-crash-owner", url=site.url("/state")
            )
            assert (
                isinstance(other_fresh, str)
                and json.loads(other_fresh)["text"] == "Signed out"
            )
            await browser_turn(app, vault.name, "other-crash-owner", operation="close")
        await browser_turn(app, vault.name, "crash-owner", operation="close")


async def test_active_browser_does_not_delay_another_chats_expiry(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = app.vault("BrowserIndependentExpiryVault")
    await app.start()
    for key, value in (
        ("browser_min_memory_headroom_mb", "0"),
        ("browser_max_concurrent_sessions", "2"),
        ("browser_idle_timeout_seconds", "2"),
    ):
        assert (
            app.request(
                f"/api/system/settings/general/{key}", "PUT", {"value": value}
            ).status_code
            == 200
        )
    with retained_browser_site(app, monkeypatch) as site:
        active = await browser_turn(
            app, vault.name, "busy-first", url=site.url("/prime")
        )
        idle = await browser_turn(
            app, vault.name, "idle-second", url=site.url("/prime")
        )
        assert isinstance(active, str) and isinstance(idle, str)
        operation = asyncio.create_task(
            browser_turn(app, vault.name, "busy-first", url=site.url("/slow"))
        )
        try:
            async with asyncio.timeout(4):
                while not site.slow_started.is_set():
                    await asyncio.sleep(0.02)
            await wait_browser_event(
                app, "browser_context_expired", session_id="idle-second"
            )
            assert not operation.done(), "Expiry must not terminate active navigation"
            site.release_slow.set()
            current = await operation
            assert (
                isinstance(current, str) and json.loads(current)["text"] == "Signed in"
            )
            assert json.loads(current)["browser_id"] == json.loads(active)["browser_id"]
        finally:
            site.release_slow.set()
            await asyncio.gather(operation, return_exceptions=True)
            for chat in ("busy-first", "idle-second"):
                await browser_turn(app, vault.name, chat, operation="close")


async def test_browser_timeout_begins_idle_wait_when_operation_finishes(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic_ai.messages import ToolReturn

    vault = app.vault("BrowserFailedActivityVault")
    await app.start()
    for key, value in (
        ("browser_min_memory_headroom_mb", "0"),
        ("browser_idle_timeout_seconds", "1"),
        ("browser_navigation_timeout_seconds", "2"),
    ):
        assert (
            app.request(
                f"/api/system/settings/general/{key}", "PUT", {"value": value}
            ).status_code
            == 200
        )
    with retained_browser_site(app, monkeypatch) as site:
        first = await browser_turn(
            app, vault.name, "timeout-owner", url=site.url("/prime")
        )
        assert isinstance(first, str)
        try:
            failed = await browser_turn(
                app, vault.name, "timeout-owner", url=site.url("/slow")
            )
            assert (
                isinstance(failed, ToolReturn) and failed.metadata["status"] == "failed"
            )
            current = await browser_turn(app, vault.name, "timeout-owner")
            assert isinstance(current, str)
            assert json.loads(current)["browser_id"] == json.loads(first)["browser_id"]
        finally:
            site.release_slow.set()
            await browser_turn(app, vault.name, "timeout-owner", operation="close")


async def test_shortened_idle_setting_cannot_revive_old_authentication(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = app.vault("BrowserChangedDeadlineVault")
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
            app, vault.name, "changed-deadline", url=site.url("/prime")
        )
        assert isinstance(first, str)
        await asyncio.sleep(1.1)
        assert (
            app.request(
                "/api/system/settings/general/browser_idle_timeout_seconds",
                "PUT",
                {"value": "1"},
            ).status_code
            == 200
        )
        # Access immediately; correctness must not depend on the worker having
        # reached this chat since the setting was changed.
        fresh = await browser_turn(
            app, vault.name, "changed-deadline", url=site.url("/state")
        )
        assert isinstance(fresh, str) and json.loads(fresh)["text"] == "Signed out"
        assert json.loads(fresh)["browser_id"] != json.loads(first)["browser_id"]
        await browser_turn(app, vault.name, "changed-deadline", operation="close")


async def test_stop_during_shared_crash_retirement_remains_task_cancellation(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import signal
    from pathlib import Path

    from core.runtime.execution_tasks import chat_session_scope
    from core.runtime.state import get_runtime_context
    from validation.support.browser.browser_runtime import descendant_identities
    from validation.support.browser.retained_site import (
        pause_driver_on_browser_close,
        wait_processes_gone,
    )

    vault = app.vault("BrowserRetirementStopVault")
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
        baseline = {r["pid"] for r in descendant_identities()}
        for chat in ("retirement-stop", "retirement-other"):
            assert isinstance(
                await browser_turn(app, vault.name, chat, url=site.url("/prime")), str
            )
        owned = [r for r in descendant_identities() if r["pid"] not in baseline]
        drivers = [r for r in owned if r["parent"] == os.getpid()]
        assert len(drivers) == 1
        browsers = [
            r
            for r in owned
            if r["parent"] == drivers[0]["pid"]
            and Path(f"/proc/{r['pid']}/comm").read_text().strip() == "chrome"
        ]
        assert len(browsers) == 1
        os.kill(browsers[0]["pid"], signal.SIGKILL)
        await wait_processes_gone(browsers)
        # OS process exit can precede the SDK's disconnected notification.
        # Establish shared-engine retirement before pausing its cleanup IPC.
        async with asyncio.timeout(6):
            while not app.events.select("browser_engine_disconnected"):
                await asyncio.sleep(0.01)
        with pause_driver_on_browser_close(app, drivers[0]["pid"]) as paused:
            operation = asyncio.create_task(
                browser_turn(app, vault.name, "retirement-stop")
            )
            try:
                async with asyncio.timeout(6):
                    await paused.ready.wait()
                runtime = get_runtime_context()
                tasks = await runtime.task_coordinator.list_tasks(
                    scope=chat_session_scope("retirement-stop"), include_terminal=False
                )
                assert len(tasks) == 1
                await runtime.task_coordinator.cancel_task(tasks[0].task_id)
                paused.resume()
                with pytest.raises(asyncio.CancelledError):
                    await operation
                terminal = await runtime.task_coordinator.get_task(tasks[0].task_id)
                assert terminal is not None and terminal.status == "cancelled"
                await wait_processes_gone(owned)
            finally:
                paused.resume()
                await asyncio.gather(operation, return_exceptions=True)
