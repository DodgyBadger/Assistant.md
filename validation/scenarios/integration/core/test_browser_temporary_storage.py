"""Real browser temporary storage placement, ownership and cleanup outcomes."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from validation.support.application import Application
from validation.support.browser.browser_runtime import descendant_identities
from validation.support.browser.retained_site import browser_turn, retained_browser_site

pytestmark = pytest.mark.scenario


@pytest.mark.parametrize(
    ("interactive", "ambient_location"), [(False, "data"), (True, "symlink")]
)
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


async def test_browser_spawn_failure_releases_owned_temporary_storage(
    app: Application, monkeypatch: pytest.MonkeyPatch
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
                interactive=True,
                url=site.url("/state"),
            )
        assert isinstance(result, ToolReturn) and result.metadata["status"] == "failed"
        assert len(observed) == 1 and (not observed[0].exists())
        assert {row["pid"] for row in descendant_identities()} == baseline
