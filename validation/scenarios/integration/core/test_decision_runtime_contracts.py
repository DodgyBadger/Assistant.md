"""Real configured typed decisions produce output, metadata and semantic activity."""

from typing import Literal

import httpx2
import pytest
from pydantic import BaseModel, Field

from validation.scenarios.support.decision_provider import (
    configure_decision,
    decision_provider,
)

pytestmark = pytest.mark.scenario


class Signals(BaseModel):
    needs_review: bool = Field(description="Review the supplied evidence")
    area: Literal["goal", "decision"] = Field(
        description="Select the affected dimension"
    )


async def test_configured_decision_factory_maps_protocol_and_preserves_metadata(app):
    await app.start()
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.llm.decision import DecisionRequest, build_decision_classifier

    with decision_provider() as server:
        configure_decision(app, server)
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            classifier = build_decision_classifier("decision-history")
            result = await classifier.classify(
                DecisionRequest(
                    state="Supplied decision evidence",
                    output_type=Signals,
                    instructions="Use only this evidence",
                )
            )
        assert result.output == Signals(needs_review=True, area="goal")
        assert (
            result.requested_model_alias,
            result.resolved_model_name,
            result.provider_name,
        ) == ("decision-history", "jev-1.13.0", "typesafe")
        assert (
            result.usage.requests,
            result.usage.input_tokens,
            result.usage.output_tokens,
        ) == (1, 18, 2)
        assert result.metadata.confidence == pytest.approx(
            {"needs_review": 0.6, "area": 0.7}
        )
        assert result.metadata.probabilities == {
            "area": {"goal": 0.85, "decision": 0.15}
        }
        assert result.metadata.scores == {} and result.latency_seconds >= 0
        assert len(server.calls) == 1
        sent = server.calls[0]
        assert sent["authorization"] == "Bearer decision-runtime-validation-key"
        assert sent["body"]["state"] == "Supplied decision evidence"
        assert sent["body"]["model"] == "jev-latest"
        assert set(sent["body"]["questions"]) == {"needs_review", "area"}
        assert "Use only this evidence" in str(sent["body"]["questions"])
    activity = app.request("/api/system/activity-log", params={"limit": 1000}).json()[
        "entries"
    ]
    event = next(
        r["data"]
        for r in activity
        if r.get("data", {}).get("event") == "decision_model_succeeded"
    )
    assert (event["status"], event["input_tokens"], event["output_tokens"]) == (
        "succeeded",
        18,
        2,
    )
    assert "decision-runtime-validation-key" not in str(activity)


async def test_external_decision_timeout_has_safe_error_and_real_failure_activity(app):
    await app.start()
    from pydantic_ai.models.typesafe import TypeSafeModel
    from pydantic_ai.providers.typesafe import TypeSafeProvider
    from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy

    from core.llm.decision import (
        DecisionProviderError,
        DecisionRequest,
        TypeSafeDecisionClassifier,
    )

    secret = "decision-timeout-validation-key"
    app.evidence.register_secret(secret)

    async def timeout(request):
        # External HTTP transport only: models, adapter and application logger
        # are real. This proves timeout classification, not elapsed-time policy.
        assert request.extensions["timeout"]["read"] == 1.0
        raise httpx2.ReadTimeout("external read deadline", request=request)

    async with AsyncTypeSafeClient(
        api_key=secret,
        transport=httpx2.MockTransport(timeout),
        retry=RetryPolicy(max_retries=0),
    ) as client:
        model = TypeSafeModel(
            "jev-latest",
            provider=TypeSafeProvider(typesafe_client=client),
            settings={"timeout": 1.0},
        )
        classifier = TypeSafeDecisionClassifier(
            model_alias="timeout-decision", model=model
        )
        with pytest.raises(DecisionProviderError, match="timeout") as failure:
            await classifier.classify(
                DecisionRequest(state="Timeout input", output_type=Signals)
            )
    activity = app.request("/api/system/activity-log", params={"limit": 1000}).json()[
        "entries"
    ]
    event = next(
        r["data"]
        for r in activity
        if r.get("data", {}).get("event") == "decision_model_failed"
    )
    assert (event["status"], event["error_category"]) == ("failed", "timeout")
    assert secret not in str(activity) + repr(failure.value)


async def test_configured_factory_applies_the_public_decision_timeout(app):
    await app.start()
    import asyncio

    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.llm.decision import (
        DecisionProviderError,
        DecisionRequest,
        build_decision_classifier,
    )

    with decision_provider() as server:
        configure_decision(app, server)
        response = app.request(
            "/api/system/settings/general/default_api_timeout", "PUT", {"value": "1"}
        )
        assert response.status_code == 200, response.text
        server.release.clear()
        # The external server accepts but withholds responses. The configured
        # factory, installed SDK and real network deadlines own the failure.
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            classifier = build_decision_classifier("decision-history")
            async with asyncio.timeout(15):
                with pytest.raises(DecisionProviderError, match="timeout"):
                    await classifier.classify(
                        DecisionRequest(
                            state="Held provider response", output_type=Signals
                        )
                    )
        assert server.calls
    activity = app.request("/api/system/activity-log", params={"limit": 1000}).json()[
        "entries"
    ]
    assert any(
        row.get("data", {}).get("event") == "decision_model_failed"
        and row["data"].get("error_category") == "timeout"
        for row in activity
    )
