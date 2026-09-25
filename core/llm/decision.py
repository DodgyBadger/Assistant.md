"""Provider-neutral execution for typed decision models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from time import perf_counter
from typing import Protocol

import httpx2
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelAPIError, UnexpectedModelBehavior, UserError
from pydantic_ai.models.typesafe import TypeSafeModel
from pydantic_ai.providers.typesafe import TypeSafeProvider

from core.llm.model_selection import resolve_model_execution_spec
from core.llm.model_utils import (
    get_provider_config,
    model_supports_capability,
    resolve_model,
    validate_api_keys,
)
from core.llm.provider_policy import (
    provider_has_configured_base_url,
    resolve_provider_base_url,
)
from core.logger import UnifiedLogger
from core.secrets import require_secrets_ready
from core.settings import get_default_api_timeout
from core.settings.secrets_store import get_secret_value
from core.utils.value_parser import DirectiveValueParser

logger = UnifiedLogger(tag="decision-model")


@dataclass(frozen=True)
class DecisionRequest[DecisionOutputT: BaseModel]:
    """One bounded state and typed question schema to classify."""

    state: str
    output_type: type[DecisionOutputT]
    instructions: str | None = None

    def __post_init__(self) -> None:
        if not self.state.strip():
            raise ValueError("Decision state cannot be empty")


@dataclass(frozen=True)
class DecisionUsage:
    """Provider-neutral token and request accounting for one classification."""

    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    details: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionMetadata:
    """Optional calibrated metadata exposed by a decision adapter."""

    confidence: Mapping[str, float] = field(default_factory=dict)
    probabilities: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    scores: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionResult[DecisionOutputT: BaseModel]:
    """Typed classification output plus portable execution diagnostics."""

    output: DecisionOutputT
    requested_model_alias: str
    resolved_model_name: str
    provider_name: str
    latency_seconds: float
    usage: DecisionUsage
    metadata: DecisionMetadata


class DecisionClassifier(Protocol):
    """Contract implemented by hosted, local, and in-process classifiers."""

    async def classify[DecisionOutputT: BaseModel](
        self, request: DecisionRequest[DecisionOutputT]
    ) -> DecisionResult[DecisionOutputT]: ...


class DecisionRuntimeError(RuntimeError):
    """Base error for a decision request that could not produce an output."""


class DecisionProviderError(DecisionRuntimeError):
    """A configured decision provider failed or returned an invalid response."""


class DecisionRequestError(DecisionRuntimeError):
    """The typed decision request cannot be represented by the adapter."""


class TypeSafeDecisionClassifier:
    """Pydantic AI adapter for TypeSafe's Jev decision models."""

    def __init__(self, *, model_alias: str, model: TypeSafeModel) -> None:
        self._model_alias = model_alias
        self._model = model

    async def classify[DecisionOutputT: BaseModel](
        self, request: DecisionRequest[DecisionOutputT]
    ) -> DecisionResult[DecisionOutputT]:
        """Run one typed decision without exposing provider response objects."""
        started = perf_counter()
        output_name = request.output_type.__name__
        logger.info(
            "decision_model_started",
            data={
                "event": "decision_model_started",
                "status": "started",
                "model_alias": self._model_alias,
                "provider": "typesafe",
                "output_type": output_name,
            },
        )
        try:
            agent: Agent[None, DecisionOutputT] = Agent(
                self._model,
                output_type=request.output_type,
                instructions=request.instructions,
                name=f"decision_{self._model_alias}",
            )
            run_result = await agent.run(request.state)
        except UserError as exc:
            _log_decision_failure(
                self._model_alias,
                output_name,
                perf_counter() - started,
                exc,
                category="request",
            )
            raise DecisionRequestError(
                f"Decision request for model '{self._model_alias}' is not supported"
            ) from exc
        except (ModelAPIError, UnexpectedModelBehavior) as exc:
            category = "timeout" if _is_timeout_error(exc) else "provider"
            _log_decision_failure(
                self._model_alias,
                output_name,
                perf_counter() - started,
                exc,
                category=category,
            )
            raise DecisionProviderError(
                f"Decision model '{self._model_alias}' failed ({category})"
            ) from exc

        elapsed = perf_counter() - started
        response = run_result.response
        usage = run_result.usage
        provider_details = response.provider_details or {}
        result = DecisionResult(
            output=run_result.output,
            requested_model_alias=self._model_alias,
            resolved_model_name=response.model_name or self._model.model_name,
            provider_name=response.provider_name or self._model.system,
            latency_seconds=elapsed,
            usage=DecisionUsage(
                requests=usage.requests,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                details=dict(usage.details),
            ),
            metadata=DecisionMetadata(
                confidence=_numeric_mapping(provider_details.get("confidence")),
                probabilities=_nested_numeric_mapping(
                    provider_details.get("probabilities")
                ),
                scores=_numeric_mapping(provider_details.get("scores")),
            ),
        )
        logger.info(
            "decision_model_succeeded",
            data={
                "event": "decision_model_succeeded",
                "status": "succeeded",
                "model_alias": self._model_alias,
                "resolved_model": result.resolved_model_name,
                "provider": result.provider_name,
                "output_type": output_name,
                "latency_seconds": elapsed,
                "input_tokens": result.usage.input_tokens,
                "output_tokens": result.usage.output_tokens,
            },
        )
        return result


def build_decision_classifier(model_alias: str) -> DecisionClassifier:
    """Build the adapter for one configured decision-capable model alias."""
    execution = resolve_model_execution_spec(model_alias)
    normalized_alias = execution.base_alias
    if execution.mode == "skip" or not normalized_alias:
        raise ValueError("A decision-capable model alias is required")
    _, parameters = DirectiveValueParser.parse_value_with_parameters(
        model_alias.strip()
    )
    if parameters:
        raise ValueError("Decision model alias parameters are not supported")

    require_secrets_ready()
    if not model_supports_capability(normalized_alias, "decision"):
        raise ValueError(
            f"Model '{normalized_alias}' does not declare the 'decision' capability"
        )
    validate_api_keys(normalized_alias)
    provider_name, model_name = resolve_model(normalized_alias)
    if provider_name != "typesafe":
        raise ValueError(
            f"Decision provider '{provider_name}' has no configured runtime adapter"
        )

    provider_config = get_provider_config(provider_name)
    secret_name = provider_config.get("api_key")
    if not isinstance(secret_name, str) or not secret_name.strip():
        raise ValueError(f"Provider '{provider_name}' must reference an API-key secret")
    api_key = get_secret_value(secret_name.strip())
    if not api_key:
        raise ValueError(
            f"Model '{normalized_alias}' requires secret '{secret_name}' to be configured"
        )

    base_url = resolve_provider_base_url(
        provider_config,
        get_secret_value=get_secret_value,
    )
    if provider_has_configured_base_url(provider_config) and base_url is None:
        raise ValueError(
            f"Provider '{provider_name}' has an invalid configured base_url"
        )
    provider = TypeSafeProvider(api_key=api_key, base_url=base_url)
    model = TypeSafeModel(
        model_name,
        provider=provider,
        settings={"timeout": float(get_default_api_timeout())},
    )
    return TypeSafeDecisionClassifier(model_alias=normalized_alias, model=model)


def _numeric_mapping(value: object) -> dict[str, float]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): float(number)
        for key, number in value.items()
        if isinstance(number, int | float) and not isinstance(number, bool)
    }


def _nested_numeric_mapping(value: object) -> dict[str, dict[str, float]]:
    if not isinstance(value, Mapping):
        return {}
    normalized: dict[str, dict[str, float]] = {}
    for key, distribution in value.items():
        numeric_distribution = _numeric_mapping(distribution)
        if numeric_distribution:
            normalized[str(key)] = numeric_distribution
    return normalized


def _is_timeout_error(exc: BaseException) -> bool:
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, TimeoutError | httpx2.TimeoutException):
            return True
        current = current.__cause__
    return False


def _log_decision_failure(
    model_alias: str,
    output_type: str,
    latency_seconds: float,
    exc: BaseException,
    *,
    category: str,
) -> None:
    logger.warning(
        "decision_model_failed",
        data={
            "event": "decision_model_failed",
            "status": "failed",
            "model_alias": model_alias,
            "provider": "typesafe",
            "output_type": output_type,
            "latency_seconds": latency_seconds,
            "error_category": category,
            "error_type": type(exc).__name__,
            "issue": f"decision_model:{model_alias}:{category}",
        },
    )
