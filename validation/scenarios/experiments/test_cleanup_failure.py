"""Real shutdown fault: run one fault case plus the following case explicitly."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application

pytestmark = pytest.mark.scenario


@pytest.mark.parametrize("catch_before_teardown", [False, True])
async def test_shutdown_failure_blocks_reuse(
    app: Application, catch_before_teardown: bool
) -> None:
    from core.logger import close_activity_log_for_root

    await app.start()
    close_activity_log_for_root(app.root / "system")
    activity = app.root / "system" / "activity.log"
    activity.rename(activity.with_suffix(".retained.log"))
    activity.mkdir()
    app.evidence.step("shutdown_log_path_obstructed")
    if catch_before_teardown:
        with pytest.raises(IsADirectoryError):
            await app.controller.close()
        with pytest.raises(RuntimeError, match="cleanup"):
            await app.start()
        app.evidence.step("failed_controller_restart_rejected")


async def test_following_application_must_not_start(app: Application) -> None:
    await app.start()
    app.evidence.step("UNSAFE_SUBSEQUENT_APPLICATION_STARTED")
    pytest.fail("This application must not start after unconfirmed shutdown")
