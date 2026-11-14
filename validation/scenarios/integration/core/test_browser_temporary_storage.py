"""Real browser temporary storage placement, ownership and cleanup outcomes."""

from __future__ import annotations

import json
import os
from functools import wraps
from pathlib import Path

import pytest

from validation.support.application import Application
from validation.support.browser.browser_runtime import descendant_identities
from validation.support.browser.retained_site import browser_turn, retained_browser_site

pytestmark = pytest.mark.scenario


@pytest.mark.parametrize("interactive", [False, True])
@pytest.mark.parametrize("ambient_location", ["data", "system", "symlink"])
async def test_browser_profiles_stay_outside_persistent_roots_and_are_removed(
    app: Application,
    monkeypatch: pytest.MonkeyPatch,
    browser_temporary_alias: Path,
    interactive: bool,
    ambient_location: str,
) -> None:
    from core.logger import capture_validation_events
    from core.runtime.state import get_runtime_context

    vault = app.vault("BrowserEphemeralProfileVault")
    await app.start()
    assert (
        app.request(
            "/api/system/settings/general/browser_min_memory_headroom_mb",
            "PUT",
            {"value": "0"},
        ).status_code
        == 200
    )
    config = get_runtime_context().config
    persistent_roots = [
        Path(config.data_root).resolve(),
        Path(config.system_root).resolve(),
    ]
    # New top-level data directories are legitimately initialized as vaults.
    # Use the existing vault to isolate browser writes from that behavior.
    target = (
        vault if ambient_location == "data" else persistent_roots[1]
    ) / "ambient-browser-temp"
    target.mkdir()
    # A short external filesystem alias prevents long harness paths from
    # obscuring placement acceptance, preserving the persistent destination.
    ambient = browser_temporary_alias / "tmp"
    destination = target
    if ambient_location == "symlink":
        destination = browser_temporary_alias / "indirect"
        destination.symlink_to(target, target_is_directory=True)
    ambient.symlink_to(destination, target_is_directory=True)
    for key in ("TMPDIR", "TMP", "TEMP"):
        monkeypatch.setenv(key, str(ambient))
    observed: list[tuple[Path, Path, int, bool]] = []
    with retained_browser_site(app, monkeypatch) as site:
        baseline = {row["pid"] for row in descendant_identities()}

        def observe(record):
            app.events.capture(record)
            event = record.get("data", {}).get("event") or record.get("name")
            if event not in {
                "browser_engine_started",
                "browser_session_launch_completed",
            }:
                return
            owned = [
                row for row in descendant_identities() if row["pid"] not in baseline
            ]
            drivers = [row for row in owned if row["parent"] == os.getpid()]
            assert len(drivers) == 1
            browsers = [
                row
                for row in owned
                if row["parent"] == drivers[0]["pid"]
                and Path(f"/proc/{row['pid']}/comm").read_text().strip() == "chrome"
            ]
            assert len(browsers) == 1
            arguments = (
                Path(f"/proc/{browsers[0]['pid']}/cmdline").read_bytes().split(b"\0")
            )
            profiles = [
                Path(arg.decode().split("=", 1)[1]).resolve()
                for arg in arguments
                if arg.startswith(b"--user-data-dir=")
            ]
            assert len(profiles) == 1 and profiles[0].is_dir()
            profile = profiles[0]
            environment = dict(
                entry.split(b"=", 1)
                for entry in Path(f"/proc/{drivers[0]['pid']}/environ")
                .read_bytes()
                .split(b"\0")
                if entry
            )
            temporary_root = Path(environment[b"TMPDIR"].decode()).resolve()
            child_temporary_settings_match = True
            for row in drivers + browsers:
                child_environment = dict(
                    entry.split(b"=", 1)
                    for entry in Path(f"/proc/{row['pid']}/environ")
                    .read_bytes()
                    .split(b"\0")
                    if entry
                )
                child_temporary_settings_match &= all(
                    child_environment.get(key) == environment[b"TMPDIR"]
                    for key in (b"TMPDIR", b"TMP", b"TEMP")
                )
            observed.append(
                (
                    profile,
                    temporary_root,
                    temporary_root.stat().st_mode & 0o777,
                    child_temporary_settings_match,
                )
            )

        with capture_validation_events(
            app.evidence.artifacts / "ephemeral-profile",
            sink=observe,
            projector=app.evidence.redact,
        ):
            try:
                result = await browser_turn(
                    app,
                    vault.name,
                    "ephemeral-owner",
                    interactive=interactive,
                    url=site.url("/prime"),
                )
                assert isinstance(result, str)
                assert len(observed) == 1
                profile, temporary_root, mode, child_temporary_settings_match = (
                    observed[0]
                )
                assert child_temporary_settings_match
                assert all(
                    not profile.is_relative_to(root) for root in persistent_roots
                ), "Browser profile reached persistent runtime state"
                assert profile.is_relative_to(temporary_root)
                assert all(
                    not temporary_root.is_relative_to(root) for root in persistent_roots
                )
                assert mode == 0o700
                if interactive:
                    result = await browser_turn(
                        app, vault.name, "ephemeral-owner", url=site.url("/state")
                    )
                    assert (
                        isinstance(result, str)
                        and json.loads(result)["text"] == "Signed in"
                    )
            finally:
                if interactive:
                    await browser_turn(
                        app, vault.name, "ephemeral-owner", operation="close"
                    )
        assert len(observed) == 1
        assert not observed[0][
            1
        ].exists(), "Owned browser temporary data remains after close"
        assert (
            list(target.iterdir()) == []
        ), "Ambient persistent temporary location was used"
        assert os.environ["TMPDIR"] == str(ambient)


@pytest.fixture
def browser_temporary_alias():
    from tempfile import TemporaryDirectory

    with TemporaryDirectory(prefix="browser-alias-", dir="/tmp") as directory:
        yield Path(directory)


@pytest.mark.parametrize("interactive", [False, True])
async def test_browser_spawn_failure_releases_owned_temporary_storage(
    app: Application, monkeypatch: pytest.MonkeyPatch, interactive: bool
) -> None:
    import asyncio

    import playwright
    from pydantic_ai.messages import ToolReturn

    vault = app.vault("BrowserFailedSpawnStorageVault")
    await app.start()
    assert (
        app.request(
            "/api/system/settings/general/browser_min_memory_headroom_mb",
            "PUT",
            {"value": "0"},
        ).status_code
        == 200
    )
    original = asyncio.create_subprocess_exec
    node = str(Path(playwright.__file__).parent / "driver" / "node")
    observed: list[Path] = []

    async def launch(*args, **kwargs):
        if args and args[0] == node:
            # External OS creation fails before any child exists. The real
            # SDK, transport initialization and application cleanup still run.
            assert args[2:] == ("run-driver",)
            directory = Path(kwargs["env"]["TMPDIR"])
            assert directory.is_dir()
            observed.append(directory)
            raise OSError("Synthetic operating-system browser spawn refusal")
        return await original(*args, **kwargs)

    with retained_browser_site(app, monkeypatch) as site:
        monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
        baseline = {row["pid"] for row in descendant_identities()}
        async with asyncio.timeout(8):
            result = await browser_turn(
                app,
                vault.name,
                "failed-spawn-storage",
                interactive=interactive,
                url=site.url("/state"),
            )
        assert isinstance(result, ToolReturn) and result.metadata["status"] == "failed"
        assert len(observed) == 1 and not observed[0].exists()
        assert {row["pid"] for row in descendant_identities()} == baseline


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


async def test_browser_profile_removal_does_not_block_application_requests(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import shutil
    from threading import Event

    vault = app.vault("BrowserStorageResponsivenessVault")
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
        result = await browser_turn(
            app, vault.name, "storage-responsive", url=site.url("/prime")
        )
        assert isinstance(result, str)
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
            .split(b"\0")
            if entry
        )
        directory = Path(environment[b"TMPDIR"].decode())
        entered, release = Event(), Event()
        original = shutil.rmtree

        @wraps(original)
        def remove(path, *args, **kwargs):
            # External filesystem latency only, for this exact owned root.
            # All production shutdown ownership and deletion logic stays real.
            if Path(path) == directory:
                entered.set()
                release.wait(5)
            return original(path, *args, **kwargs)

        monkeypatch.setattr(shutil, "rmtree", remove)
        started = asyncio.get_running_loop().time()
        operation = asyncio.create_task(
            browser_turn(app, vault.name, "storage-responsive", operation="close")
        )
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            assert (
                asyncio.get_running_loop().time() - started < 2
            ), "Profile removal blocked the application event loop"
            assert not operation.done()
            response = app.request("/api/system/settings/general")
            assert response.status_code == 200
        finally:
            release.set()
            async with asyncio.timeout(8):
                closed = await operation
        assert isinstance(closed, str) and json.loads(closed)["status"] == "closed"
        assert not directory.exists()


@pytest.mark.parametrize("with_retained_context", [False, True])
async def test_uncertain_extraction_storage_cleanup_closes_browser_admission(
    app: Application, monkeypatch: pytest.MonkeyPatch, with_retained_context: bool
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
    original = shutil.rmtree
    with retained_browser_site(app, monkeypatch) as site:
        retained_processes: list[dict] = []
        if with_retained_context:
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
                .split(b"\0")
                if entry
            )
            observed.append(Path(environment[b"TMPDIR"].decode()))

        @wraps(original)
        def remove(path, *args, **kwargs):
            # A slow external filesystem outlives the owned deletion deadline.
            if observed and Path(path) == observed[0]:
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
                if with_retained_context:
                    # Admission refusal must not disable the other context's
                    # real inactivity cleanup. Change its live setting now,
                    # after uncertainty, so early expiry cannot satisfy this.
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
