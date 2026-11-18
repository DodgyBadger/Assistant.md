"""Selected real process, transport and ownership faults for final hardening.

These cases are kept in core so the deterministic pre-merge gate includes them.
During development select affected normal cases or this file explicitly. Faults
pause/kill actual native peers or delay controlled external I/O, never replace
application outcomes. No screenshots or private input are retained.
"""

from __future__ import annotations

import asyncio
import json
import os
from functools import wraps
from pathlib import Path

import pytest
from pydantic_ai.messages import ToolReturn

from core.runtime.state import get_runtime_context
from validation.support.application import Application
from validation.support.browser.actions import FORM, configure, element, view
from validation.support.browser.browser_runtime import descendant_identities
from validation.support.browser.handoff import handoff_path, waiting_handoff
from validation.support.browser.human_transport import (
    claimed_browser,
    frame_metadata,
    fresh_frame,
)
from validation.support.browser.retained_site import (
    browser_turn,
    retained_browser_site,
    wait_browser_event,
)

pytestmark = pytest.mark.scenario


async def configure_deadline(app: Application, navigation_timeout: str = "1") -> None:
    await app.start()
    for key, value in (
        ("browser_min_memory_headroom_mb", "0"),
        ("browser_navigation_timeout_seconds", navigation_timeout),
    ):
        saved = app.request(
            f"/api/system/settings/general/{key}", "PUT", {"value": value}
        )
        assert saved.status_code == 200


async def test_runtime_shutdown_drains_inflight_browser_admission(
    app: Application, monkeypatch: pytest.MonkeyPatch
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
            shutdown.cancel()
            done, _ = await asyncio.wait({shutdown}, timeout=0.05)
            assert not done, "Cancellation must not abandon browser cleanup"
        finally:
            held.release.set()
            await first
            if shutdown is not None:
                with pytest.raises(asyncio.CancelledError):
                    await shutdown
    assert not has_runtime_context()
    assert not runtime.scheduler.running


async def test_stop_during_browser_action_releases_lock_with_stalled_driver(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from core.runtime.execution_tasks import chat_session_scope
    from core.runtime.state import get_runtime_context
    from validation.support.browser.browser_runtime import descendant_identities
    from validation.support.browser.retained_site import (
        pause_driver_on_browser_event,
        wait_processes_gone,
    )

    vault = app.vault("BrowserStalledActionVault")
    await configure(app)
    with retained_browser_site(app, monkeypatch) as site:
        site.pages["/form"] = FORM
        baseline = {record["pid"] for record in descendant_identities()}
        first = view(
            await browser_turn(app, vault.name, "stalled-action", url=site.url("/form"))
        )
        owned = [
            record
            for record in descendant_identities()
            if record["pid"] not in baseline
        ]
        drivers = [record for record in owned if record["parent"] == os.getpid()]
        assert len(drivers) == 1
        with pause_driver_on_browser_event(
            app, drivers[0]["pid"], "browser_action_started"
        ) as paused:
            operation = asyncio.create_task(
                browser_turn(
                    app,
                    vault.name,
                    "stalled-action",
                    operation="click",
                    reference=element(first, "Save"),
                )
            )
            try:
                async with asyncio.timeout(5):
                    await paused.ready.wait()
                runtime = get_runtime_context()
                tasks = await runtime.task_coordinator.list_tasks(
                    scope=chat_session_scope("stalled-action"), include_terminal=False
                )
                assert len(tasks) == 1
                await runtime.task_coordinator.cancel_task(tasks[0].task_id)
                # Keep the actual IPC peer stopped throughout the assertion.
                # Cleanup's own deadlines must finish despite that condition.
                done, _ = await asyncio.wait({operation}, timeout=18)
                assert (
                    operation in done
                ), "Stop must release browser action ownership even when the driver never replies"
                with pytest.raises(asyncio.CancelledError):
                    await operation
                terminal = await runtime.task_coordinator.get_task(tasks[0].task_id)
                assert terminal is not None and terminal.status == "cancelled"
                assert site.submissions == []
            finally:
                paused.resume()
                await asyncio.gather(operation, return_exceptions=True)
        await wait_processes_gone(owned)


async def test_browser_owns_and_reaps_driver_stalled_before_initialization(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import signal

    from playwright._impl._driver import compute_driver_executable

    from validation.support.browser.browser_runtime import descendant_identities
    from validation.support.browser.retained_site import wait_processes_gone

    vault = app.vault("BrowserPartialStartupVault")
    await configure(app)
    original = asyncio.create_subprocess_exec
    ready = asyncio.Event()
    paused_pid = None
    driver, cli = compute_driver_executable()

    async def launch(*args, **kwargs):
        nonlocal paused_pid
        process = await original(*args, **kwargs)
        if tuple(map(str, args)) == (driver, cli, "run-driver") and paused_pid is None:
            # External process-boundary fault injection: the real SDK process
            # exists, but cannot consume its first initialization IPC message.
            paused_pid = process.pid
            os.kill(paused_pid, signal.SIGSTOP)
            ready.set()
        return process

    with retained_browser_site(app, monkeypatch) as site:
        with monkeypatch.context() as scope:
            scope.setattr(asyncio, "create_subprocess_exec", launch)
            operation = asyncio.create_task(
                browser_turn(app, vault.name, "partial-start", url=site.url("/state"))
            )
            try:
                async with asyncio.timeout(5):
                    await ready.wait()
                owned = [
                    record
                    for record in descendant_identities()
                    if record["pid"] == paused_pid
                ]
                assert len(owned) == 1
                done, _ = await asyncio.wait({operation}, timeout=12)
                assert (
                    operation in done
                ), "Partial driver initialization must have an owned deadline and cleanup"
                failed = await operation
                assert (
                    isinstance(failed, ToolReturn)
                    and failed.metadata["status"] == "failed"
                )

                await wait_processes_gone(owned)
                fresh = view(
                    await browser_turn(
                        app, vault.name, "partial-start", url=site.url("/state")
                    )
                )
                assert fresh["text"] == "Signed out"
            finally:
                if paused_pid is not None:
                    try:
                        os.kill(paused_pid, signal.SIGCONT)
                    except ProcessLookupError:
                        pass
                await asyncio.gather(operation, return_exceptions=True)
                await browser_turn(app, vault.name, "partial-start", operation="close")


async def test_unconfirmed_deadline_cleanup_never_advertises_human_help(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import os

    from core.runtime.execution_tasks import chat_session_scope
    from core.runtime.state import get_runtime_context
    from validation.support.browser.browser_runtime import descendant_identities
    from validation.support.browser.retained_site import (
        pause_driver_on_browser_event,
        wait_processes_gone,
    )

    vault = app.vault("BrowserUnconfirmedDeadlineVault")
    await configure_deadline(app)
    with retained_browser_site(app, monkeypatch) as site:
        baseline = {record["pid"] for record in descendant_identities()}
        first = await browser_turn(
            app, vault.name, "cleanup-deadline", url=site.url("/prime")
        )
        assert isinstance(first, str)
        owned = [
            record
            for record in descendant_identities()
            if record["pid"] not in baseline
        ]
        drivers = [record for record in owned if record["parent"] == os.getpid()]
        assert len(drivers) == 1
        with pause_driver_on_browser_event(
            app, drivers[0]["pid"], "browser_navigation_deadline"
        ) as paused:
            operation = asyncio.create_task(
                browser_turn(app, vault.name, "cleanup-deadline", url=site.url("/slow"))
            )
            try:
                async with asyncio.timeout(5):
                    await paused.ready.wait()
                runtime = get_runtime_context()
                tasks = await runtime.task_coordinator.list_tasks(
                    scope=chat_session_scope("cleanup-deadline"), include_terminal=False
                )
                assert len(tasks) == 1
                done, _ = await asyncio.wait({operation}, timeout=18)
                assert (
                    operation in done
                ), "Recovery cleanup must release ownership without an IPC reply"
                failed = await operation
                assert (
                    isinstance(failed, ToolReturn)
                    and failed.metadata["help_eligible"] is False
                )
                assert failed.metadata["reason"] == "browser_unresponsive"
                assert (
                    "browser_id" not in failed.metadata
                    and "page_id" not in failed.metadata
                )
                assert not app.events.select("browser_read_deadline")
            finally:
                paused.resume()
                site.release_slow.set()
                await asyncio.gather(operation, return_exceptions=True)
        await wait_processes_gone(owned)


async def test_history_change_on_old_document_cannot_attest_new_navigation_commit(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = app.vault("BrowserHistoryDeadlineVault")
    await configure_deadline(app)
    with retained_browser_site(app, monkeypatch) as site:
        site.pages["/history-owner"] = (
            b'<main>Previous document</main><script>setInterval(() => history.replaceState({}, "", "/changed-old"), 50);</script>'
        )
        try:
            first = await browser_turn(
                app, vault.name, "history-deadline", url=site.url("/history-owner")
            )
            assert isinstance(first, str)
            deadline = await browser_turn(
                app, vault.name, "history-deadline", url=site.url("/slow")
            )
            assert site.slow_started.is_set()
            assert (
                isinstance(deadline, ToolReturn)
                and deadline.metadata["reason"] == "read_deadline"
            )
            assert deadline.metadata["retained_url"] == site.url("/changed-old")
            assert deadline.metadata["help_eligible"] is False
            current = await browser_turn(app, vault.name, "history-deadline")
            assert (
                isinstance(current, str)
                and "Previous document" in json.loads(current)["text"]
            )
            assert json.loads(current)["browser_id"] == json.loads(first)["browser_id"]
        finally:
            site.release_slow.set()
            await browser_turn(app, vault.name, "history-deadline", operation="close")


async def test_stop_during_handback_inspection_cannot_return_control_to_the_agent(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from validation.support.browser.browser_runtime import descendant_identities
    from validation.support.browser.retained_site import pause_driver_on_browser_event

    vault = app.vault("BrowserHandoffReturnRaceVault")
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
        assert isinstance(
            await browser_turn(app, vault.name, "handoff-race", url=site.url("/prime")),
            str,
        )
        drivers = [
            row
            for row in descendant_identities()
            if row["pid"] not in baseline and row["parent"] == os.getpid()
        ]
        assert len(drivers) == 1
        operation = asyncio.create_task(
            browser_turn(app, vault.name, "handoff-race", operation="handoff")
        )
        returning = None
        try:
            state = await waiting_handoff(app, operation, "handoff-race")
            path = handoff_path("handoff-race") + "/" + state["handoff_id"]
            assert (await app.request_async(path + "/claim", "POST")).status_code == 200
            with pause_driver_on_browser_event(
                app, drivers[0]["pid"], "browser_handback_check_started"
            ) as paused:
                returning = asyncio.create_task(
                    app.request_async(path + "/return", "POST")
                )
                async with asyncio.timeout(5):
                    await paused.ready.wait()
                await get_runtime_context().task_coordinator.cancel_task(
                    state["task_id"]
                )
                with pytest.raises(asyncio.CancelledError):
                    await operation
                paused.resume()
                response = await returning
                assert response.status_code == 409
            current = await app.request_async(handoff_path("handoff-race"))
            assert current.status_code == 200 and current.json()["status"] == "stopped"
            refused = await browser_turn(app, vault.name, "handoff-race")
            assert (
                isinstance(refused, ToolReturn)
                and refused.metadata["reason"] == "human_control_active"
            )
        finally:
            if not operation.done():
                operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
            if returning is not None:
                await asyncio.gather(returning, return_exceptions=True)


async def test_handback_started_before_idle_expiry_finishes_as_an_active_operation(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from validation.support.browser.browser_runtime import descendant_identities
    from validation.support.browser.retained_site import pause_driver_on_browser_event

    vault = app.vault("BrowserHandoffActiveReturnVault")
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
        assert isinstance(
            await browser_turn(
                app, vault.name, "handoff-active-return", url=site.url("/prime")
            ),
            str,
        )
        drivers = [
            row
            for row in descendant_identities()
            if row["pid"] not in baseline and row["parent"] == os.getpid()
        ]
        assert len(drivers) == 1
        operation = asyncio.create_task(
            browser_turn(app, vault.name, "handoff-active-return", operation="handoff")
        )
        returning = None
        try:
            state = await waiting_handoff(app, operation, "handoff-active-return")
            path = handoff_path("handoff-active-return") + "/" + state["handoff_id"]
            assert (await app.request_async(path + "/claim", "POST")).status_code == 200
            with pause_driver_on_browser_event(
                app, drivers[0]["pid"], "browser_handback_check_started"
            ) as paused:
                assert (
                    app.request(
                        "/api/system/settings/general/browser_idle_timeout_seconds",
                        "PUT",
                        {"value": "1"},
                    ).status_code
                    == 200
                )
                returning = asyncio.create_task(
                    app.request_async(path + "/return", "POST")
                )
                async with asyncio.timeout(3):
                    await paused.ready.wait()
                # Real paused IPC crosses the idle interval, within the guard's
                # bounded deadline. Accepted work already owns the chat lock.
                await asyncio.sleep(1.2)
                paused.resume()
                response = await returning
                assert response.status_code == 200
                result = await operation
                assert (
                    isinstance(result, str)
                    and json.loads(result)["status"] == "returned"
                )
        finally:
            if not operation.done():
                operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
            if returning is not None:
                await asyncio.gather(returning, return_exceptions=True)


async def test_cancelled_or_stopped_capture_keeps_ipc_owned_and_discards_late_pixels(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import signal

    from core.runtime.state import get_runtime_context
    from validation.support.browser.human_transport import (
        assert_viewer_tasks_settled,
        claimed_browser,
        driver_processes,
    )

    async with claimed_browser(app, monkeypatch, session_id="private-capture") as (
        _site,
        client,
        operation,
        path,
        state,
    ):
        frame_metadata(await client.get(path + "/frame"))
        driver, _owned = driver_processes()
        os.kill(driver["pid"], signal.SIGSTOP)
        capturing = asyncio.create_task(client.get(path + "/frame"))
        try:
            await asyncio.sleep(0.1)
            async with asyncio.timeout(0.5):
                busy = await client.get(path + "/frame")
            assert busy.status_code == 409 and busy.json()["detail"] == "browser_busy"
            await get_runtime_context().task_coordinator.cancel_task(state["task_id"])
            with pytest.raises(asyncio.CancelledError):
                await operation
            os.kill(driver["pid"], signal.SIGCONT)
            response = await capturing
            assert (
                response.status_code == 409
                and response.headers["content-type"] != "image/jpeg"
            )
            assert (await client.get(path + "/frame")).status_code == 409
            await assert_viewer_tasks_settled()
        finally:
            try:
                os.kill(driver["pid"], signal.SIGCONT)
            except ProcessLookupError:
                pass
            if not capturing.done():
                capturing.cancel()
            await asyncio.gather(capturing, return_exceptions=True)


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
        pause_driver_on_browser_event,
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
        with pause_driver_on_browser_event(
            app, drivers[0]["pid"], "browser_context_closed"
        ) as paused:
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


async def test_popup_reservation_precedes_slow_dns_and_denies_other_windows(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket
    from threading import Event, Lock

    from validation.support.browser.retained_site import PUBLIC_IP

    html = b"""<style>body{margin:0}button{position:absolute;left:20px;top:20px;width:180px;height:40px}</style><button onclick="for(let i=0;i<4;i++)window.open('http://slow-popup-'+i+'.example.test:'+location.port+'/popup-login','popup-'+i)">Sign in</button>"""
    async with claimed_browser(app, monkeypatch, session_id="popup-dns", html=html) as (
        site,
        client,
        operation,
        path,
        state,
    ):
        site.pages["/popup-login"] = b"<main>Provider sign-in</main>"
        started, release = Event(), Event()
        guard = Lock()
        queries = []
        original_dns = socket.getaddrinfo

        def blocked_dns(host, port, *args, **kwargs):
            # Only external DNS is delayed; real reservation/SDK/request policy
            # and HTTP traffic remain application-produced outcomes.
            if str(host).startswith("slow-popup-"):
                with guard:
                    queries.append(host)
                started.set()
                release.wait(8)
                return [
                    (
                        socket.AF_INET,
                        socket.SOCK_STREAM,
                        socket.IPPROTO_TCP,
                        "",
                        (PUBLIC_IP, port or 80),
                    )
                ]
            return original_dns(host, port, *args, **kwargs)

        monkeypatch.setattr(socket, "getaddrinfo", blocked_dns)
        root = await fresh_frame(client, path)
        from pydantic_ai.messages import ToolReturn

        from validation.support.browser.human_transport import driver_processes
        from validation.support.browser.retained_site import wait_processes_gone

        _, owned = driver_processes()
        checkpoint = app.events.checkpoint()
        clicking = asyncio.create_task(
            client.post(
                path + "/input", json={**root, "kind": "click", "x": 50, "y": 40}
            )
        )
        try:
            assert await asyncio.to_thread(started.wait, 3)
            async with asyncio.timeout(0.5):
                busy = await client.get(path + "/frame")
            assert busy.status_code == 409 and busy.json()["detail"] == "browser_busy"
            async with asyncio.timeout(1.5):
                while (
                    len(
                        [
                            record
                            for record in app.events.select(
                                "browser_popup_bootstrap_reserved", after=checkpoint
                            )
                            if record.get("data", {}).get("browser_id")
                            == state["browser_id"]
                        ]
                    )
                    < 4
                ):
                    await asyncio.sleep(0.01)
            reservations = [
                record
                for record in app.events.select(
                    "browser_popup_bootstrap_reserved", after=checkpoint
                )
                if record.get("data", {}).get("browser_id") == state["browser_id"]
            ]
            assert sum(record["data"]["eligible"] for record in reservations) == 1
            with guard:
                observed_queries = len(queries)
            # Proxy CONNECT preconnect has its own shared four-job DNS bound;
            # it can run before HTTP interception. It sends no website HTTP.
            assert 1 <= observed_queries <= 4
            assert site.popup_requests == []
            release.set()
            await clicking
            async with asyncio.timeout(10):
                while not operation.done():
                    response = await client.get(path + "/frame")
                    if (
                        response.status_code == 200
                        and response.headers["x-browser-page"] != state["page_id"]
                    ):
                        break
                    await asyncio.sleep(0.05)
                else:
                    failed = await operation
                    assert (
                        isinstance(failed, ToolReturn)
                        and failed.metadata["reason"] == "browser_closed"
                    )
                    assert len(site.popup_requests) <= 1
                    await wait_processes_gone(owned)
                    return
            frame_metadata(response)
            assert site.popup_requests == ["/popup-login"]
            assert (await client.post(path + "/return")).status_code == 200
            assert isinstance(await operation, str)
            await browser_turn(
                app, "BrowserTransportBoundaryVault", "popup-dns", operation="close"
            )
            await wait_processes_gone(owned)
        finally:
            release.set()
            if not clicking.done():
                clicking.cancel()
            await asyncio.gather(clicking, return_exceptions=True)


async def test_closing_original_page_retires_popup_without_promoting_it(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic_ai.messages import ToolReturn

    from validation.support.browser.human_transport import driver_processes
    from validation.support.browser.retained_site import wait_processes_gone

    html = b"""<style>body{margin:0}button{position:absolute;left:20px;width:180px;height:40px}</style><button style="top:20px" onclick="window.open('/popup-login','sign-in')">Sign in</button>"""
    async with claimed_browser(
        app, monkeypatch, session_id="popup-root-close", html=html
    ) as (site, client, operation, path, state):
        site.pages["/popup-login"] = b"<main>Provider sign-in</main>"
        metadata = await fresh_frame(client, path)
        assert (
            await client.post(
                path + "/input", json={**metadata, "kind": "click", "x": 50, "y": 40}
            )
        ).status_code == 200
        async with asyncio.timeout(5):
            while True:
                response = await client.get(path + "/frame")
                if (
                    response.status_code == 200
                    and response.headers["x-browser-page"] != state["page_id"]
                ):
                    break
                await asyncio.sleep(0.05)
        _, owned = driver_processes()
        # Website window.close cannot close an automation-created root window.
        # Close the actual external Chromium Page through its real SDK instead;
        # no service state, authority, waiter or outcome is substituted.
        from core.runtime.state import get_runtime_context

        engine = get_runtime_context().browser._engine
        assert engine is not None and engine.browser is not None
        originals = [
            page
            for context in engine.browser.contexts
            for page in context.pages
            if page.url == site.url("/human")
        ]
        assert len(originals) == 1
        await originals[0].close()
        async with asyncio.timeout(10):
            failed = await operation
        assert (
            isinstance(failed, ToolReturn)
            and failed.metadata["reason"] == "browser_closed"
        )
        assert (await client.get(path + "/frame")).status_code == 404
        await wait_processes_gone(owned)


async def test_stopped_extraction_driver_cannot_block_task_cancellation(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from core.runtime.execution_tasks import chat_session_scope
    from core.runtime.state import get_runtime_context
    from validation.support.browser.retained_site import (
        paused_browser_driver,
        wait_processes_gone,
    )

    vault = app.vault("BrowserExtractionStopVault")
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
        paused_browser_driver(
            app, event="browser_navigation_response_received"
        ) as paused,
    ):
        baseline = {row["pid"] for row in descendant_identities()}
        operation = asyncio.create_task(
            browser_turn(
                app,
                vault.name,
                "extraction-stop",
                interactive=False,
                url=site.url("/state"),
            )
        )
        try:
            async with asyncio.timeout(8):
                await paused.ready.wait()
            owned = [
                row for row in descendant_identities() if row["pid"] not in baseline
            ]
            assert paused.driver_pid is not None
            environment = dict(
                entry.split(b"=", 1)
                for entry in Path(f"/proc/{paused.driver_pid}/environ")
                .read_bytes()
                .split(b"\0")
                if entry
            )
            temporary_root = Path(environment[b"TMPDIR"].decode())
            assert temporary_root.is_dir()
            coordinator = get_runtime_context().task_coordinator
            tasks = await coordinator.list_tasks(
                scope=chat_session_scope("extraction-stop"), include_terminal=False
            )
            assert len(tasks) == 1
            await coordinator.cancel_task(tasks[0].task_id)
            # Leave the real IPC peer stopped until production teardown finishes.
            async with asyncio.timeout(12):
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.shield(operation)
            assert operation.done(), "Stop did not finish owned extraction cleanup"
            terminal = await coordinator.get_task(tasks[0].task_id)
            assert terminal is not None and terminal.status == "cancelled"
            await wait_processes_gone(owned)
            assert (
                not temporary_root.exists()
            ), "Stopped extraction left browser temporary data"
        finally:
            paused.resume()
            if not operation.done():
                operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)


async def test_uncertain_extraction_storage_cleanup_closes_browser_admission(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import shutil
    from threading import Event

    from pydantic_ai.messages import ToolReturn

    from core.logger import capture_validation_events
    from validation.support.browser.retained_site import (
        wait_browser_event,
        wait_processes_gone,
    )

    vault = app.vault("BrowserUncertainStorageAdmissionVault")
    await app.start()
    assert (
        app.request(
            "/api/system/settings/general/browser_min_memory_headroom_mb",
            "PUT",
            {"value": "0"},
        ).status_code
        == 200
    )
    observed: list[Path] = []
    release = Event()
    deletion_started = Event()
    original = shutil.rmtree
    with retained_browser_site(app, monkeypatch) as site:
        retained_processes: list[dict] = []
        assert (
            app.request(
                "/api/system/settings/general/browser_max_concurrent_sessions",
                "PUT",
                {"value": "2"},
            ).status_code
            == 200
        )
        before = {row["pid"] for row in descendant_identities()}
        signed_in = await browser_turn(
            app, vault.name, "retained-during-quarantine", url=site.url("/prime")
        )
        assert isinstance(signed_in, str)
        retained_processes = [
            row for row in descendant_identities() if row["pid"] not in before
        ]
        baseline = {row["pid"] for row in descendant_identities()}

        def observe(record):
            app.events.capture(record)
            if record.get("name") != "browser_session_launch_completed":
                return
            drivers = [
                row
                for row in descendant_identities()
                if row["pid"] not in baseline and row["parent"] == os.getpid()
            ]
            assert len(drivers) == 1
            environment = dict(
                entry.split(b"=", 1)
                for entry in Path(f"/proc/{drivers[0]['pid']}/environ")
                .read_bytes()
                .split(b"\x00")
                if entry
            )
            observed.append(Path(environment[b"TMPDIR"].decode()))

        @wraps(original)
        def remove(path, *args, **kwargs):
            if observed and Path(path) == observed[0]:
                deletion_started.set()
                release.wait(12)
            return original(path, *args, **kwargs)

        monkeypatch.setattr(shutil, "rmtree", remove)
        with capture_validation_events(
            app.evidence.artifacts / "uncertain-storage",
            sink=observe,
            projector=app.evidence.redact,
        ):
            operation = asyncio.create_task(
                browser_turn(
                    app,
                    vault.name,
                    "uncertain-storage",
                    interactive=False,
                    url=site.url("/prime"),
                )
            )
            try:
                assert await asyncio.to_thread(deletion_started.wait, 6)
                async with asyncio.timeout(2):
                    response = await app.request_async("/api/system/settings/general")
                assert response.status_code == 200 and not operation.done()
                done, _ = await asyncio.wait({operation}, timeout=10)
                assert (
                    operation in done
                ), "Unconfirmed deletion did not return within its deadline"
                failed = await operation
                assert (
                    isinstance(failed, ToolReturn)
                    and failed.metadata["status"] == "failed"
                )
                assert len(observed) == 1 and observed[0].is_dir()
                checkpoint = app.events.checkpoint()
                refused = await browser_turn(
                    app,
                    vault.name,
                    "uncertain-storage",
                    interactive=False,
                    url=site.url("/state"),
                )
                assert (
                    isinstance(refused, ToolReturn)
                    and refused.metadata["reason"] == "runtime_closed"
                )
                assert not app.events.select(
                    "browser_session_launch_started", after=checkpoint
                )
                assert (
                    app.request(
                        "/api/system/settings/general/browser_idle_timeout_seconds",
                        "PUT",
                        {"value": "1"},
                    ).status_code
                    == 200
                )
                await wait_browser_event(
                    app,
                    "browser_context_expired",
                    session_id="retained-during-quarantine",
                )
                await wait_processes_gone(retained_processes)
            finally:
                release.set()
                await asyncio.gather(operation, return_exceptions=True)
                if observed:
                    async with asyncio.timeout(6):
                        while observed[0].exists():
                            await asyncio.sleep(0.02)


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


async def test_unexpected_driver_death_preserves_storage_and_reports_uncertainty(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil
    import signal

    from core.browser.engine import BrowserEngine
    from core.runtime.state import get_runtime_context
    from validation.support.browser.retained_site import wait_processes_gone

    app.vault("BrowserUnexpectedDriverStorageVault")
    await app.start()
    # This infrastructure contract deliberately owns its real engine directly.
    # Unconfirmed close is the expected result; the application fixture's
    # service and its shutdown accounting are neither replaced nor reset.
    with retained_browser_site(app, monkeypatch) as site:
        baseline = {row["pid"] for row in descendant_identities()}
        engine = BrowserEngine()
        killed = False
        owned: list[dict] = []
        directory: Path | None = None
        try:
            browser = await engine.start(
                await get_runtime_context().browser.proxy_url()
            )
            context = await browser.new_context(
                accept_downloads=False, service_workers="block"
            )
            page = await context.new_page()
            await page.goto(site.url("/prime"))
            await page.goto(site.url("/state"))
            assert await page.locator("body").inner_text() == "Signed in"
            owned = [
                row for row in descendant_identities() if row["pid"] not in baseline
            ]
            drivers = [row for row in owned if row["parent"] == os.getpid()]
            assert len(drivers) == 1
            environment = dict(
                entry.split(b"=", 1)
                for entry in Path(f"/proc/{drivers[0]['pid']}/environ")
                .read_bytes()
                .split(b"\0")
                if entry
            )
            directory = Path(environment[b"TMPDIR"].decode())
            os.kill(drivers[0]["pid"], signal.SIGKILL)
            killed = True
            await wait_processes_gone(drivers)
            with pytest.raises(RuntimeError, match="cleanup is unconfirmed"):
                await engine.close()
            assert (
                directory.is_dir()
            ), "Unexpected driver death was treated as confirmed cleanup"
        finally:
            if not killed:
                await engine.close()
            # Test-owned synthetic data only: establish actual process release
            # independently before cleaning the intentionally uncertain root.
            await wait_processes_gone(owned)
            if directory is not None and directory.exists():
                shutil.rmtree(directory)
