"""Deliberately wrong oracles; explicit runs must fail, never join core."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_wrong_registered_value_is_rejected(app: Application) -> None:
    from core.logger import UnifiedLogger

    await app.start()
    actual, wrong = "synthetic-actual-value", "synthetic-wrong-value"
    app.evidence.register_secret(actual, wrong)
    UnifiedLogger("negative_control", default_sinks=["validation"]).info(
        "Observed actual value", data={"event": "negative_control", "value": actual}
    )
    app.events.require("negative_control", fields={"value": wrong})


async def test_unsafe_application_log_is_detected(app: Application) -> None:
    from core.logger import UnifiedLogger

    await app.start()
    marker = "synthetic-unsafe-log-value"
    app.evidence.register_secret(marker)
    UnifiedLogger("negative_control", default_sinks=["activity", "validation"]).info(
        marker, data={"event": "negative_control", "value": marker}
    )
    # An intentionally unsafe production emission must trip a confidentiality oracle.
    assert marker not in (app.root / "system" / "activity.log").read_text()
