"""Native startup and parent-loss faults must not leave owned children alive."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import sys
from pathlib import Path

import playwright
import pytest

from core.browser.driver import BrowserDriver
from core.browser.operations import BrowserOperations, finish_cleanup
from validation.support.core_processes import declare_core_command

pytestmark = pytest.mark.scenario


async def test_closing_during_unpublished_driver_spawn_prevents_a_late_child(
    app, monkeypatch
):
    await app.start()
    node = str(Path(playwright.__file__).parent / "driver" / "node")
    declare_core_command(
        [node, str(Path(node).parent / "package" / "cli.js"), "run-driver"]
    )
    entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    spawned = []
    original_spawn = asyncio.create_subprocess_exec

    async def delayed_spawn(program, *args, **kwargs):
        # Delay only the external OS spawn boundary. The real driver owner,
        # connection, storage and any eventual native Node remain unchanged.
        if program == node:
            entered.set()
            try:
                await release.wait()
                process = await original_spawn(program, *args, **kwargs)
                spawned.append(process)
                return process
            finally:
                finished.set()
        return await original_spawn(program, *args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
    driver = BrowserDriver()
    starting = asyncio.create_task(driver.start())
    storage = None
    try:
        async with asyncio.timeout(2):
            await entered.wait()
        storage = driver.temporary_directory
        closing = asyncio.create_task(driver.close(BrowserOperations()))
        async with asyncio.timeout(8):
            result = await asyncio.gather(closing, return_exceptions=True)
        release.set()
        async with asyncio.timeout(2):
            await finished.wait()
        late_live = any(process.returncode is None for process in spawned)
        assert (
            not late_live
        ), "Driver close allowed its pending spawn to create a late live child"
        assert result == [None], "Pending driver startup cleanup was not confirmed"
        await asyncio.wait({starting}, timeout=1)
        assert starting.done(), "Driver initialization was left unowned after close"
        assert not storage.exists()
    finally:

        async def cleanup():
            release.set()
            starting.cancel()
            await asyncio.gather(starting, return_exceptions=True)
            await asyncio.gather(
                driver.close(BrowserOperations()), return_exceptions=True
            )
            if entered.is_set():
                async with asyncio.timeout(3):
                    await finished.wait()
            # The external spawn boundary has now settled: no new handle can
            # arrive after this snapshot, including on an early assertion exit.
            for child in spawned:
                if child.returncode is None:
                    child.terminate()
                try:
                    async with asyncio.timeout(2):
                        await child.wait()
                except TimeoutError:
                    child.kill()
                    async with asyncio.timeout(2):
                        await child.wait()
            if driver._startup is not None:
                async with asyncio.timeout(2):
                    await asyncio.gather(driver._startup, return_exceptions=True)
            if storage is not None and storage.exists():
                shutil.rmtree(storage)

        await finish_cleanup(asyncio.create_task(cleanup()))


async def test_owned_display_exits_when_its_application_owner_is_killed(app):
    await app.start()
    # This standalone supervisor adopts only its own child's Xvfb. Its owner
    # uses the actual BrowserEngine display startup; the only fault is SIGKILL.
    command = declare_core_command(
        [
            sys.executable,
            "-m",
            "validation.support.browser.native_ownership",
            "supervise",
        ]
    )
    process = await asyncio.create_subprocess_exec(
        *command,
        start_new_session=True,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(15):
            stdout, stderr = await process.communicate()
    finally:

        async def cleanup():
            if process.returncode is None:
                # This newly created session contains only our supervisor and
                # its children. Its TERM handler reaps those exact descendants.
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    async with asyncio.timeout(3):
                        await process.wait()
                except TimeoutError:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    async with asyncio.timeout(3):
                        await process.wait()

        await finish_cleanup(asyncio.create_task(cleanup()))
    assert process.returncode == 0, stderr.decode()
    outcome = json.loads(stdout)
    assert outcome["test_cleanup_reaped"]
    assert outcome[
        "display_released_on_parent_loss"
    ], "Xvfb survived the loss of its application owner"
