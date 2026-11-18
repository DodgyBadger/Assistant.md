"""A real idle worker releases retained authentication and respects active use."""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic_ai.messages import ToolReturn

from validation.support.application import Application
from validation.support.browser.handoff import waiting_handoff
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
        assert (
            app.request(
                "/api/system/settings/general/browser_idle_timeout_seconds",
                "PUT",
                {"value": "600"},
            ).status_code
            == 200
        )
        first = await browser_turn(
            app, vault.name, "shortened-owner", url=site.url("/prime")
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
            app, vault.name, "shortened-owner", url=site.url("/state")
        )
        assert isinstance(fresh, str) and json.loads(fresh)["text"] == "Signed out"
        assert json.loads(fresh)["browser_id"] != json.loads(first)["browser_id"]
        await browser_turn(app, vault.name, "shortened-owner", operation="close")


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
        saved = app.request(
            "/api/system/settings/general/browser_navigation_timeout_seconds",
            "PUT",
            {"value": "3"},
        )
        assert saved.status_code == 200
        try:
            failed = await browser_turn(
                app, vault.name, "active-owner", url=site.url("/slow")
            )
            assert (
                isinstance(failed, ToolReturn) and failed.metadata["status"] == "failed"
            )
            current = await browser_turn(app, vault.name, "active-owner")
            assert (
                isinstance(current, str)
                and json.loads(current)["browser_id"] == json.loads(first)["browser_id"]
            )
        finally:
            site.release_slow.set()
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


async def test_crashed_browser_releases_dead_context_for_explicit_retry(
    app: Application, monkeypatch: pytest.MonkeyPatch
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
            {"value": "2"},
        ).status_code
        == 200
    )
    with retained_browser_site(app, monkeypatch) as site:
        baseline = {r["pid"] for r in descendant_identities()}
        first = await browser_turn(
            app, vault.name, "crash-owner", url=site.url("/prime")
        )
        assert isinstance(first, str)
        other = await browser_turn(
            app, vault.name, "other-crash-owner", url=site.url("/prime")
        )
        assert isinstance(other, str)
        waiting = asyncio.create_task(
            browser_turn(app, vault.name, "crash-owner", operation="handoff")
        )
        await waiting_handoff(app, waiting, "crash-owner")
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
        async with asyncio.timeout(6):
            failed = await waiting
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
        other_fresh = await browser_turn(
            app, vault.name, "other-crash-owner", url=site.url("/state")
        )
        assert (
            isinstance(other_fresh, str)
            and json.loads(other_fresh)["text"] == "Signed out"
        )
        await browser_turn(app, vault.name, "other-crash-owner", operation="close")
        await browser_turn(app, vault.name, "crash-owner", operation="close")
