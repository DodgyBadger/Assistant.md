"""Observation preserves application logging while exported evidence is redacted."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest
import yaml

from validation.pytest_scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_logging_capture_preserves_application_records(app: Application) -> None:
    from core.logger import UnifiedLogger

    await app.start()
    secrets = ("synthetic-warning-alpha", "synthetic-warning-beta")
    app.evidence.register_secret(*secrets)
    logger = UnifiedLogger("capture_contract", default_sinks=["activity", "validation"])
    checkpoint = app.events.checkpoint()
    data = {"event": "capture_contract_warning", "nested": {"value": secrets[0]}}
    original = deepcopy(data)
    # Distinct messages and distinct issues must retain their production dedupe keys,
    # even when all registered values have the same exported redaction marker.
    for secret in secrets:
        logger.warning(secret, data=data, issue="message_case")
        logger.warning("issue_case", data=data, issue=secret)
    logger.warning(secrets[0], data=data, issue="message_case")
    assert data == original

    records = app.events.select("capture_contract_warning", after=checkpoint)
    assert len(records) == 4
    app.events.require(
        "capture_contract_warning",
        after=checkpoint,
        fields={"nested": {"value": secrets[0]}, "issue": secrets[1]},
    )
    assert not app.events.contains(
        "capture_contract_warning",
        after=checkpoint,
        fields={"nested": {"value": "[REDACTED]"}},
    ), "The safety oracle must observe the deliberately unsafe application value"
    activity = [
        json.loads(line)
        for line in (app.root / "system" / "activity.log").read_text().splitlines()
    ]
    emitted = [row for row in activity if row["tag"] == "capture_contract"]
    assert len(emitted) == 4
    assert {row["message"] for row in emitted} == {*secrets, "issue_case"}
    assert {row["data"]["issue"] for row in emitted} == {*secrets, "message_case"}
    assert all(row["data"]["nested"]["value"] == secrets[0] for row in emitted)

    exported = [
        yaml.safe_load(path.read_text())
        for path in (app.evidence.artifacts / "validation_events").glob("*.yaml")
    ]
    exported_records = [row for row in exported if row["tag"] == "capture_contract"]
    assert len(exported_records) == 4
    assert all(row["name"] == "capture_contract_warning" for row in exported_records)
    assert all(
        row["data"]["nested"]["value"] == "[REDACTED]" for row in exported_records
    )
    assert all(secret not in json.dumps(exported_records) for secret in secrets)
    exported_observations = (app.evidence.artifacts / "observations.jsonl").read_text()
    assert all(secret not in exported_observations for secret in secrets)
    assert "[REDACTED]" in exported_observations


async def test_logging_capture_callbacks_cannot_mutate_application_data(
    app: Application,
) -> None:
    from core.logger import UnifiedLogger, capture_validation_events

    await app.start()
    observed: list[dict[str, Any]] = []

    def project(record: dict[str, Any]) -> dict[str, Any]:
        record["data"]["nested"]["value"] = "exported"
        return record

    def observe(record: dict[str, Any]) -> None:
        observed.append(deepcopy(record))
        record["data"]["nested"]["value"] = "observer-mutated"

    payload = {"event": "capture_copy_contract", "nested": {"value": "original"}}
    # Nested capture supplies deliberately mutating callbacks without replacing any
    # application logger handlers or outputs; the outer fixture owns runtime setup.
    nested = app.evidence.artifacts / "callback_capture"
    with capture_validation_events(nested, sink=observe, projector=project):
        logger = UnifiedLogger("capture_copy", default_sinks=["activity", "validation"])
        logger.info("copy_contract", data=payload)
    assert payload["nested"]["value"] == "original"
    assert observed[0]["data"]["nested"]["value"] == "original"
    activity = [
        json.loads(line)
        for line in (app.root / "system" / "activity.log").read_text().splitlines()
    ]
    record = next(row for row in activity if row["tag"] == "capture_copy")
    assert record["data"]["nested"]["value"] == "original"
    exported = list((nested / "validation_events").glob("*.yaml"))
    assert len(exported) == 1
    assert (
        yaml.safe_load(exported[0].read_text())["data"]["nested"]["value"] == "exported"
    )
