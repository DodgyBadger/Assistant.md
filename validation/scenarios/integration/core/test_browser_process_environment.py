"""Browser children receive only runtime environment, never application secrets."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from validation.support.application import Application
from validation.support.browser.browser_runtime import descendant_identities
from validation.support.browser.retained_site import browser_turn, retained_browser_site

pytestmark = pytest.mark.scenario


async def test_retained_browser_children_exclude_application_and_control_environment(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = app.vault("BrowserChildEnvironmentVault")
    await app.start()
    assert (
        app.request(
            "/api/system/settings/general/browser_min_memory_headroom_mb",
            "PUT",
            {"value": "0"},
        ).status_code
        == 200
    )
    forbidden = {
        "ASSISTANTMD_SECRETS_KEY",
        "ASSISTANTMD_SECRETS_KEYS",
        "OPENAI_API_KEY",
        "SSH_AUTH_SOCK",
        "BROWSER_ENV_TEST_SECRET",
        "HTTPS_PROXY",
    }
    sentinel = "synthetic-child-environment-secret-91268"
    app.evidence.register_secret(sentinel)
    for name in forbidden - {"ASSISTANTMD_SECRETS_KEY", "ASSISTANTMD_SECRETS_KEYS"}:
        monkeypatch.setenv(name, sentinel)
    with retained_browser_site(app, monkeypatch) as site:
        # Declare the bundled dependency first, then exercise an ambient SDK
        # override which the application-owned transport must not consult.
        monkeypatch.setenv(
            "PLAYWRIGHT_NODEJS_PATH", "/tmp/blocked-browser-driver-override"
        )
        forbidden.add("PLAYWRIGHT_NODEJS_PATH")
        baseline = {record["pid"] for record in descendant_identities()}
        try:
            result = await browser_turn(
                app, vault.name, "environment-owner", url=site.url("/state")
            )
            assert (
                isinstance(result, str) and json.loads(result)["text"] == "Signed out"
            )
            owned = [
                record
                for record in descendant_identities()
                if record["pid"] not in baseline
            ]
            drivers = [record for record in owned if record["parent"] == os.getpid()]
            assert len(drivers) == 1
            browsers = [
                record
                for record in owned
                if record["parent"] == drivers[0]["pid"]
                and Path(f"/proc/{record['pid']}/comm").read_text().strip() == "chrome"
            ]
            assert len(browsers) == 1
            for record in drivers + browsers:
                # /proc belongs to actual newly created children. Keep only
                # names/boolean verdicts; never persist a populated environment.
                entries = (
                    Path(f"/proc/{record['pid']}/environ").read_bytes().split(b"\0")
                )
                names = {entry.split(b"=", 1)[0].decode() for entry in entries if entry}
                assert (
                    not names & forbidden
                ), "Application/control environment reached a browser child"
                assert not any(sentinel.encode() in entry for entry in entries)
        finally:
            await browser_turn(app, vault.name, "environment-owner", operation="close")


async def test_browser_protocol_ignores_parent_debug_dump_settings(
    app: Application, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    vault = app.vault("BrowserPrivateTransportVault")
    await app.start()
    assert (
        app.request(
            "/api/system/settings/general/browser_min_memory_headroom_mb",
            "PUT",
            {"value": "0"},
        ).status_code
        == 200
    )
    sentinel = "synthetic-protocol-payload-should-not-print-45812"
    app.evidence.register_secret(sentinel)
    monkeypatch.setenv("DEBUGP", "1")
    monkeypatch.setenv("DEBUG", "pw:protocol")
    monkeypatch.setenv("PWDEBUG", "0")
    with retained_browser_site(app, monkeypatch) as site:
        site.pages["/form"] = b'<label>Name<input name="name"></label>'
        try:
            result = await browser_turn(
                app, vault.name, "debug-owner", url=site.url("/form")
            )
            assert isinstance(result, str)
            reference = json.loads(result)["elements"][0]["ref"]
            filled = await browser_turn(
                app,
                vault.name,
                "debug-owner",
                operation="fill",
                reference=reference,
                value=sentinel,
            )
            assert isinstance(filled, str)
        finally:
            await browser_turn(app, vault.name, "debug-owner", operation="close")
    captured = capsys.readouterr()
    assert sentinel not in captured.out + captured.err
    assert "SEND>" not in captured.out and "RECV>" not in captured.out
    assert os.environ["DEBUGP"] == "1" and os.environ["DEBUG"] == "pw:protocol"


async def test_extraction_uses_same_private_driver_and_browser_environment(
    app: Application, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from core.logger import capture_validation_events
    from core.tools.browser import BrowserTool

    app.vault("BrowserExtractionEnvironmentVault")
    await app.start()
    assert (
        app.request(
            "/api/system/settings/general/browser_min_memory_headroom_mb",
            "PUT",
            {"value": "0"},
        ).status_code
        == 200
    )
    sentinel = "synthetic-extraction-parent-secret-13970"
    app.evidence.register_secret(sentinel)
    monkeypatch.setenv("BROWSER_ENV_TEST_SECRET", sentinel)
    monkeypatch.setenv("DEBUGP", "1")
    with retained_browser_site(app, monkeypatch) as site:
        baseline = {record["pid"] for record in descendant_identities()}
        verdicts = []

        def observe(record):
            app.events.capture(record)
            if record.get("name") != "browser_session_launch_completed":
                return
            owned = [
                row for row in descendant_identities() if row["pid"] not in baseline
            ]
            direct = [row for row in owned if row["parent"] == os.getpid()]
            for row in direct + [
                row for row in owned if direct and row["parent"] == direct[0]["pid"]
            ]:
                try:
                    entries = (
                        Path(f"/proc/{row['pid']}/environ").read_bytes().split(b"\0")
                    )
                except FileNotFoundError:
                    continue
                names = {entry.split(b"=", 1)[0].decode() for entry in entries if entry}
                verdicts.append(
                    not {"BROWSER_ENV_TEST_SECRET", "ASSISTANTMD_SECRETS_KEY", "DEBUGP"}
                    & names
                    and not any(sentinel.encode() in entry for entry in entries)
                )

        with capture_validation_events(
            app.evidence.artifacts / "extraction-environment",
            sink=observe,
            projector=app.evidence.redact,
        ):
            result = await BrowserTool.get_tool().function(url=site.url("/state"))
        assert isinstance(result, str) and "Signed out" in result
        assert len(verdicts) >= 2 and all(verdicts)
        captured = capsys.readouterr()
        assert "SEND>" not in captured.out and "RECV>" not in captured.out
        assert sentinel not in captured.out + captured.err


async def test_owned_display_uses_runtime_environment_and_is_reaped(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import shutil

    from validation.support.browser.retained_site import wait_processes_gone
    from validation.support.core_processes import declare_core_command

    vault = app.vault("BrowserDisplayEnvironmentVault")
    await app.start()
    assert (
        app.request(
            "/api/system/settings/general/browser_min_memory_headroom_mb",
            "PUT",
            {"value": "0"},
        ).status_code
        == 200
    )
    sentinel = "synthetic-owned-display-parent-secret-71309"
    app.evidence.register_secret(sentinel)
    monkeypatch.setenv("BROWSER_ENV_TEST_SECRET", sentinel)
    original = asyncio.create_subprocess_exec

    async def launch(*args, **kwargs):
        if args and args[0] == shutil.which("Xvfb"):
            assert (
                len(args) == 8 and args[1] == "-displayfd" and str(args[2]).isdecimal()
            )
            assert args[3:] == ("-screen", "0", "1280x800x24", "-nolisten", "tcp")
            declare_core_command(list(args))
        return await original(*args, **kwargs)

    with retained_browser_site(app, monkeypatch) as site:
        with monkeypatch.context() as scope:
            scope.delenv("DISPLAY", raising=False)
            scope.setattr(asyncio, "create_subprocess_exec", launch)
            baseline = {record["pid"] for record in descendant_identities()}
            try:
                result = await browser_turn(
                    app, vault.name, "display-environment", url=site.url("/state")
                )
                assert isinstance(result, str)
                owned = [
                    record
                    for record in descendant_identities()
                    if record["pid"] not in baseline
                ]
                displays = [
                    record
                    for record in owned
                    if record["parent"] == os.getpid()
                    and Path(f"/proc/{record['pid']}/comm").read_text().strip()
                    == "Xvfb"
                ]
                assert len(displays) == 1
                names = {
                    entry.split(b"=", 1)[0].decode()
                    for entry in Path(f"/proc/{displays[0]['pid']}/environ")
                    .read_bytes()
                    .split(b"\0")
                    if entry
                }
                assert (
                    not {
                        "BROWSER_ENV_TEST_SECRET",
                        "ASSISTANTMD_SECRETS_KEY",
                        "OPENAI_API_KEY",
                    }
                    & names
                )
                assert os.environ["BROWSER_ENV_TEST_SECRET"] == sentinel
            finally:
                await browser_turn(
                    app, vault.name, "display-environment", operation="close"
                )
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
        finally:
            paused.resume()
            if not operation.done():
                operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
