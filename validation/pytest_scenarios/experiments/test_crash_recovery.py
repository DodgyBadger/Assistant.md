"""Intentional failures: run explicitly with -n 1 --max-worker-restart=1; expect exit 1."""

from __future__ import annotations

import os

import pytest

from validation.pytest_scenarios.application import Application

pytestmark = pytest.mark.scenario


def _identity(app: Application) -> None:
    app.evidence.save(
        "process.json",
        {"pid": os.getpid(), "worker": os.environ.get("PYTEST_XDIST_WORKER", "serial")},
    )


async def _healthy_application(app: Application, name: str) -> None:
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.tools.file_write import FileWrite

    _identity(app)
    vault = app.vault(name)
    await app.start()
    response = await app.request_async("/api/status")
    assert response.status_code == 200, response.text
    assert response.json()["scheduler"]["running"] is True
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        result = FileWrite.get_tool(str(vault)).function(
            operation="write",
            path="recovered.md",
            content="real application recovered\n",
        )
    assert result.metadata["status"] == "completed"
    assert (vault / "recovered.md").read_text() == "real application recovered\n"
    app.events.require("tool_invoked", tool="file_write", operation="write")
    app.evidence.step(
        "recovery_verified",
        pid=os.getpid(),
        worker=os.environ.get("PYTEST_XDIST_WORKER"),
    )


async def test_01_unhandled_application_startup_exception(app: Application) -> None:
    _identity(app)
    root = app.root / "system"
    root.mkdir()
    (root / "settings.db").write_bytes(
        b"deliberately corrupt isolated settings database"
    )
    app.evidence.step("corrupt_database_input_seeded")
    # Let the real bootstrap exception escape into pytest, including its traceback.
    await app.start()
    pytest.fail("Corrupt settings unexpectedly permitted application startup")


async def test_02_real_app_recovers_after_exception(app: Application) -> None:
    await _healthy_application(app, "AfterApplicationException")


async def test_03_abort_running_application_worker(
    app: Application, request: pytest.FixtureRequest
) -> None:
    import faulthandler
    import resource

    if not hasattr(request.config, "workerinput"):
        raise RuntimeError("Fatal probe requires an xdist worker (-n 1)")
    _identity(app)
    app.vault("BeforeNativeAbort")
    await app.start()
    response = await app.request_async("/api/status")
    assert response.status_code == 200, response.text
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    app.evidence.step("abort_injected", pid=os.getpid(), signal="SIGABRT")
    # Write actual fatal-signal stacks, not a fabricated exception traceback.
    with (app.evidence.artifacts / "fatal-stack.txt").open("w") as output:
        faulthandler.enable(file=output, all_threads=True)
        os.abort()


async def test_04_real_app_recovers_after_worker_crash(app: Application) -> None:
    await _healthy_application(app, "AfterWorkerCrash")
