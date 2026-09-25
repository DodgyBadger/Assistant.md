"""Deterministic contracts for provider-neutral decision execution."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx2
import pytest
from pydantic import BaseModel, Field
from pydantic_ai.models.typesafe import TypeSafeModel
from pydantic_ai.providers.typesafe import TypeSafeProvider
from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy

from core.runtime.paths import set_bootstrap_roots

_TEST_ROOT = tempfile.TemporaryDirectory(prefix="assistantmd-decision-runtime-")
_TEST_ROOT_PATH = Path(_TEST_ROOT.name)
set_bootstrap_roots(_TEST_ROOT_PATH / "data", _TEST_ROOT_PATH / "system")

from core.llm import decision as decision_module  # noqa: E402
from core.llm.decision import (  # noqa: E402
    DecisionMetadata,
    DecisionProviderError,
    DecisionRequest,
    DecisionResult,
    DecisionUsage,
    TypeSafeDecisionClassifier,
    build_decision_classifier,
)


class ChangeSignals(BaseModel):
    """Identify material changes in a conversation delta."""

    goal_changed: float = Field(
        ge=0,
        le=1,
        description="Did the user's active goal materially change?",
    )
    decision_changed: float = Field(
        ge=0,
        le=1,
        description="Did the conversation establish a new decision?",
    )


class MixedSignals(BaseModel):
    """Exercise Jev answer metadata that is distinct from raw probabilities."""

    needs_review: bool = Field(description="Does this change need human review?")
    area: Literal["goal", "decision"] = Field(
        description="Which map dimension changed most?"
    )


@dataclass
class RecordingLogger:
    events: list[tuple[str, Mapping[str, object]]] = field(default_factory=list)

    def info(self, message: str, *, data: dict[str, Any] | None = None) -> None:
        self.events.append((message, data or {}))

    def warning(self, message: str, *, data: dict[str, Any] | None = None) -> None:
        self.events.append((message, data or {}))


class FakeDecisionClassifier:
    """In-process adapter used by decision-runtime consumers in tests."""

    async def classify[OutputT: BaseModel](
        self, request: DecisionRequest[OutputT]
    ) -> DecisionResult[OutputT]:
        output = request.output_type.model_validate(
            {"goal_changed": 0.91, "decision_changed": 0.12}
        )
        return DecisionResult(
            output=output,
            requested_model_alias="fake",
            resolved_model_name="fake-v1",
            provider_name="in-process",
            latency_seconds=0.0,
            usage=DecisionUsage(),
            metadata=DecisionMetadata(),
        )


@pytest.mark.asyncio
async def test_fake_adapter_satisfies_decision_contract() -> None:
    result = await FakeDecisionClassifier().classify(_change_request())
    _assert_change_result(result)
    assert result.provider_name == "in-process"


@pytest.mark.asyncio
async def test_typesafe_adapter_maps_questions_and_normalizes_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    secret = "typesafe-test-secret"
    recording_logger = RecordingLogger()
    monkeypatch.setattr(decision_module, "logger", recording_logger)

    async def handler(request: httpx2.Request) -> httpx2.Response:
        captured["body"] = json.loads(request.content)
        captured["authorization"] = request.headers.get("authorization")
        captured["timeout"] = request.extensions.get("timeout")
        return httpx2.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {
                    "goal_changed": {"type": "noul", "noul": 0.91},
                    "decision_changed": {"type": "noul", "noul": 0.12},
                },
                "usage": {"input_tokens": 27, "output_tokens": 2},
            },
        )

    classifier, client = _typesafe_classifier(
        secret=secret,
        handler=handler,
        timeout=7.0,
    )
    try:
        result = await classifier.classify(_change_request())
    finally:
        await client.aclose()

    _assert_change_result(result)
    assert result.requested_model_alias == "jev"
    assert result.resolved_model_name == "jev-1.13.0"
    assert result.provider_name == "typesafe"
    assert result.usage == DecisionUsage(
        requests=1,
        input_tokens=27,
        output_tokens=2,
        details={},
    )
    assert result.latency_seconds >= 0

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["state"] == "User replaced the active goal and accepted option B."
    assert body["model"] == "jev-latest"
    questions = body["questions"]
    assert isinstance(questions, dict)
    assert set(questions) == {"goal_changed", "decision_changed"}
    assert "Did the user's active goal materially change?" in str(
        questions["goal_changed"]
    )
    assert "Judge only the supplied conversation delta." in str(
        questions["decision_changed"]
    )
    assert captured["authorization"] == f"Bearer {secret}"
    assert "7.0" in str(captured["timeout"])
    assert secret not in repr(recording_logger.events)


@pytest.mark.asyncio
async def test_typesafe_adapter_exposes_supported_confidence_metadata() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {
                    "needs_review": {"type": "noul", "noul": 0.8},
                    "area": {
                        "type": "choice",
                        "choice": "goal",
                        "confidence": 0.7,
                        "probabilities": {"goal": 0.85, "decision": 0.15},
                    },
                },
                "usage": {"input_tokens": 18, "output_tokens": 2},
            },
        )

    classifier, client = _typesafe_classifier(
        secret="metadata-secret",
        handler=handler,
    )
    try:
        result = await classifier.classify(
            DecisionRequest(
                state="The user chose a different objective.",
                output_type=MixedSignals,
            )
        )
    finally:
        await client.aclose()

    assert result.output == MixedSignals(needs_review=True, area="goal")
    assert result.metadata.confidence["needs_review"] == pytest.approx(0.6)
    assert result.metadata.confidence["area"] == pytest.approx(0.7)
    assert result.metadata.probabilities == {"area": {"goal": 0.85, "decision": 0.15}}
    assert result.metadata.scores == {}


@pytest.mark.asyncio
async def test_typesafe_timeout_is_sanitized_and_categorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "never-log-this-secret"
    recording_logger = RecordingLogger()
    monkeypatch.setattr(decision_module, "logger", recording_logger)

    async def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("synthetic timeout", request=request)

    classifier, client = _typesafe_classifier(
        secret=secret,
        handler=handler,
    )
    try:
        with pytest.raises(DecisionProviderError, match=r"failed \(timeout\)") as error:
            await classifier.classify(_change_request())
    finally:
        await client.aclose()

    serialized = f"{error.value!r} {recording_logger.events!r}"
    assert secret not in serialized
    assert any(
        data.get("error_category") == "timeout" for _, data in recording_logger.events
    )


def test_configured_factory_builds_typesafe_without_exposing_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "factory-test-secret"
    monkeypatch.setattr(decision_module, "require_secrets_ready", lambda: None)
    monkeypatch.setattr(
        decision_module,
        "model_supports_capability",
        lambda alias, capability: alias == "jev" and capability == "decision",
    )
    monkeypatch.setattr(decision_module, "validate_api_keys", lambda alias: None)
    monkeypatch.setattr(
        decision_module,
        "resolve_model",
        lambda alias: ("typesafe", "jev-latest"),
    )
    monkeypatch.setattr(
        decision_module,
        "get_provider_config",
        lambda provider: {
            "api_key": "TYPESAFE_API_KEY",
            "base_url": "https://typesafe.invalid/api",
        },
    )
    monkeypatch.setattr(
        decision_module,
        "get_secret_value",
        lambda name: secret if name == "TYPESAFE_API_KEY" else None,
    )
    monkeypatch.setattr(decision_module, "get_default_api_timeout", lambda: 11)

    classifier = build_decision_classifier(" JEV ")

    assert isinstance(classifier, TypeSafeDecisionClassifier)
    assert classifier._model.model_name == "jev-latest"
    assert classifier._model.base_url == "https://typesafe.invalid/api"
    assert secret not in repr(classifier)


def _change_request() -> DecisionRequest[ChangeSignals]:
    return DecisionRequest(
        state="User replaced the active goal and accepted option B.",
        output_type=ChangeSignals,
        instructions="Judge only the supplied conversation delta.",
    )


def _assert_change_result(result: DecisionResult[ChangeSignals]) -> None:
    assert result.output == ChangeSignals(
        goal_changed=0.91,
        decision_changed=0.12,
    )


def _typesafe_classifier(
    *,
    secret: str,
    handler: Any,
    timeout: float = 3.0,
) -> tuple[TypeSafeDecisionClassifier, AsyncTypeSafeClient]:
    client = AsyncTypeSafeClient(
        api_key=secret,
        transport=httpx2.MockTransport(handler),
        retry=RetryPolicy(max_retries=0),
    )
    provider = TypeSafeProvider(typesafe_client=client)
    model = TypeSafeModel(
        "jev-latest",
        provider=provider,
        settings={"timeout": timeout},
    )
    return TypeSafeDecisionClassifier(model_alias="jev", model=model), client
