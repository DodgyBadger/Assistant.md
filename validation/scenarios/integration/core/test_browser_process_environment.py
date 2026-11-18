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
    app: Application, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    import asyncio
    import shutil

    from validation.support.browser.retained_site import wait_processes_gone
    from validation.support.core_processes import declare_core_command

    # Admit this owned display's dynamic descriptor, without pinning argument layout.
    original = asyncio.create_subprocess_exec

    async def launch(*args, **kwargs):
        if args and args[0] == shutil.which("Xvfb"):
            assert any(
                args[i : i + 2] == ("-nolisten", "tcp") for i in range(len(args) - 1)
            )
            declare_core_command(list(args))
        return await original(*args, **kwargs)

    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    monkeypatch.setenv("DEBUGP", "1")
    monkeypatch.setenv("DEBUG", "pw:protocol")
    monkeypatch.setenv("PWDEBUG", "0")
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
    forbidden.update({"DEBUGP", "DEBUG", "PWDEBUG"})
    with retained_browser_site(app, monkeypatch) as site:
        # The external site fixture supplies a display for ordinary scenarios;
        # this case must exercise the application's own display lifecycle.
        monkeypatch.delenv("DISPLAY", raising=False)
        site.pages["/form"] = b'<label>Name<input name="name"></label>'
        # Declare the bundled dependency first, then exercise an ambient SDK
        # override which the application-owned transport must not consult.
        monkeypatch.setenv(
            "PLAYWRIGHT_NODEJS_PATH", "/tmp/blocked-browser-driver-override"
        )
        forbidden.add("PLAYWRIGHT_NODEJS_PATH")
        baseline = {record["pid"] for record in descendant_identities()}
        try:
            result = await browser_turn(
                app, vault.name, "environment-owner", url=site.url("/form")
            )
            assert isinstance(result, str)
            owned = [
                record
                for record in descendant_identities()
                if record["pid"] not in baseline
            ]
            drivers = [
                record
                for record in owned
                if record["parent"] == os.getpid()
                and Path(f"/proc/{record['pid']}/comm").read_text().strip() != "Xvfb"
            ]
            assert len(drivers) == 1
            browsers = [
                record
                for record in owned
                if record["parent"] == drivers[0]["pid"]
                and Path(f"/proc/{record['pid']}/comm").read_text().strip() == "chrome"
            ]
            assert len(browsers) == 1
            displays = [
                record
                for record in owned
                if record["parent"] == os.getpid()
                and Path(f"/proc/{record['pid']}/comm").read_text().strip() == "Xvfb"
            ]
            assert len(displays) == 1
            for record in drivers + browsers + displays:
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
            reference = json.loads(result)["elements"][0]["ref"]
            filled = await browser_turn(
                app,
                vault.name,
                "environment-owner",
                operation="fill",
                reference=reference,
                value=sentinel,
            )
            assert isinstance(filled, str)
        finally:
            await browser_turn(app, vault.name, "environment-owner", operation="close")
    await wait_processes_gone(owned)
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
