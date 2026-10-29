"""Explicit fault profile: an application hangs, then a replacement worker recovers."""

import asyncio
import subprocess
import sys

import pytest

from validation.scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_01_hung_application_requires_worker_replacement(
    app: Application,
) -> None:
    app.vault("WatchdogHungVault")
    await app.start()
    descendant = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)",
        ]
    )
    app.evidence.save_json("owned-descendant.json", {"pid": descendant.pid})
    await asyncio.sleep(120)
    pytest.fail("The protected watchdog must stop the hung application worker")


async def test_02_replacement_worker_starts_clean_application(app: Application) -> None:
    app.vault("WatchdogRecoveredVault")
    await app.start()
    response = app.request("/api/status")
    assert response.status_code == 200
