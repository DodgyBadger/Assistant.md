"""Actual typed decision rejection preserves safe provider diagnostics."""

import json

import httpx2
import pytest
from pydantic import BaseModel

from validation.scenarios.application import Application
from validation.scenarios.support.provider_denial_http import denial_provider

pytestmark = pytest.mark.scenario


class Decision(BaseModel):
    accepted: bool


async def test_decision_denial_and_generic_compaction_diagnostics(
    app: Application,
) -> None:
    app.evidence.register_secret("validation-decision-key")
    await app.start()
    from pydantic_ai.exceptions import ModelHTTPError
    from pydantic_ai.models.typesafe import TypeSafeModel
    from pydantic_ai.providers.typesafe import TypeSafeProvider

    from core.chat.compaction import _compaction_failure_details
    from core.llm.decision import (
        DecisionProviderError,
        DecisionRequest,
        TypeSafeDecisionClassifier,
    )

    with denial_provider() as server:
        async with httpx2.AsyncClient(trust_env=False) as client:
            classifier = TypeSafeDecisionClassifier(
                model_alias="local-denial",
                model=TypeSafeModel(
                    "jev-1",
                    provider=TypeSafeProvider(
                        api_key="validation-decision-key",
                        base_url=f"http://127.0.0.1:{server.server_port}",
                        http_client=client,
                    ),
                ),
            )
            with pytest.raises(DecisionProviderError) as failure:
                await classifier.classify(
                    DecisionRequest(
                        state="Check bounded decision output", output_type=Decision
                    )
                )
        assert len(server.calls) == 1
        cause = failure.value.__cause__
        assert isinstance(cause, ModelHTTPError) and cause.status_code == 403
        details = _compaction_failure_details(cause, reason="authoring_failed")
        assert "Access denied" in details["error"]
        activity = app.request("/api/system/activity-log", params={"limit": 1000})
        assert activity.status_code == 200
        event = next(
            row["data"]
            for row in activity.json()["entries"]
            if row.get("data", {}).get("event") == "decision_model_failed"
        )
        assert event["http_status"] == 403
        assert "Access denied" in event["error"]
        assert "validation-decision-key" not in json.dumps(event)

    # Real malformed input supplies a generic failure; the app must hide its
    # diagnostic internals just as it does other non-provider exceptions.
    with pytest.raises(ValueError) as malformed:
        int("PRIVATE")
    assert "PRIVATE" in str(malformed.value)
    assert "PRIVATE" not in str(
        _compaction_failure_details(malformed.value, reason="authoring_failed")
    )
