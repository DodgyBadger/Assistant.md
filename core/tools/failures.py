"""Shared structured failure envelopes for tool results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
import openai
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.messages import ToolReturn

ToolTerminalState = Literal["completed", "failed", "interrupted"]


def classify_tool_result_state(
    *, outcome: Any = None, metadata: Any = None
) -> ToolTerminalState:
    """Classify a completed tool transport by its structured domain outcome."""
    normalized_outcome = str(outcome or "success").strip().lower()
    if normalized_outcome == "interrupted":
        return "interrupted"
    result_metadata = metadata if isinstance(metadata, dict) else {}
    status = (
        str(result_metadata.get("status") or result_metadata.get("state") or "")
        .strip()
        .lower()
    )
    if status in {"cancelled", "interrupted"}:
        return "interrupted"
    if normalized_outcome in {"failed", "denied"} or status in {
        "error",
        "failed",
        "failure",
        "denied",
    }:
        return "failed"
    return "completed"


@dataclass(frozen=True)
class FailureClassification:
    """Machine-readable classification for a tool/API failure."""

    error_type: str
    failure_kind: str
    retryable: bool
    suggested_action: str
    phase: str = "tool_execution"
    message: str = ""
    http_status: int | None = None
    retry_after: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_metadata(self) -> dict[str, Any]:
        """Return JSON-safe metadata for a ToolReturn envelope."""
        payload: dict[str, Any] = {
            "status": "failed",
            "error_type": self.error_type,
            "failure_kind": self.failure_kind,
            "retryable": self.retryable,
            "phase": self.phase,
            "suggested_action": self.suggested_action,
        }
        if self.http_status is not None:
            payload["http_status"] = self.http_status
        if self.retry_after:
            payload["retry_after"] = self.retry_after
        if self.metadata:
            payload.update(self.metadata)
        return payload


def classify_exception(
    exc: Exception, *, phase: str = "tool_execution"
) -> FailureClassification:
    """Classify common network/API/tool exceptions into a stable failure envelope."""
    if isinstance(exc, UsageLimitExceeded):
        return FailureClassification(
            error_type=type(exc).__name__,
            failure_kind="execution_limit",
            retryable=False,
            phase=phase,
            message=str(exc),
            suggested_action=(
                "Do not retry the same broad request. Split the work into smaller scoped steps "
                "or raise the configured execution limit if the scope is intentional."
            ),
        )
    if isinstance(exc, openai.APIStatusError):
        status_code = exc.status_code
        retry_after = exc.response.headers.get("Retry-After")
        if status_code == 429:
            return FailureClassification(
                error_type=type(exc).__name__,
                failure_kind="rate_limited",
                retryable=True,
                phase=phase,
                message=str(exc),
                http_status=status_code,
                retry_after=retry_after,
                suggested_action="Retry after the provider rate-limit window.",
            )
        if status_code in {408, 409} or 500 <= status_code <= 599:
            return FailureClassification(
                error_type=type(exc).__name__,
                failure_kind="provider_unavailable",
                retryable=True,
                phase=phase,
                message=str(exc),
                http_status=status_code,
                retry_after=retry_after,
                suggested_action=(
                    "Retry the request; use another model/provider if the failure continues."
                ),
            )
        if status_code in {401, 403}:
            return FailureClassification(
                error_type=type(exc).__name__,
                failure_kind="configuration",
                retryable=False,
                phase=phase,
                message=str(exc),
                http_status=status_code,
                suggested_action=(
                    "Check the model provider secret and account access before retrying."
                ),
            )
        return FailureClassification(
            error_type=type(exc).__name__,
            failure_kind="bad_request",
            retryable=False,
            phase=phase,
            message=str(exc),
            http_status=status_code,
            suggested_action="Change the model request or selected model before retrying.",
        )
    if isinstance(exc, openai.APIError):
        return FailureClassification(
            error_type=type(exc).__name__,
            failure_kind="transient_provider",
            retryable=True,
            phase=phase,
            message=str(exc),
            suggested_action=(
                "Retry the request; use another model/provider if the failure continues."
            ),
        )
    if isinstance(exc, ModelHTTPError):
        status_code = exc.status_code
        body_text = str(exc.body or "")
        lowered = body_text.lower()
        if status_code == 429:
            return FailureClassification(
                error_type=type(exc).__name__,
                failure_kind="rate_limited",
                retryable=True,
                phase=phase,
                message=str(exc),
                http_status=status_code,
                suggested_action="Retry after the provider rate-limit window.",
                metadata={"model_name": exc.model_name},
            )
        if 500 <= status_code <= 599:
            return FailureClassification(
                error_type=type(exc).__name__,
                failure_kind="provider_unavailable",
                retryable=True,
                phase=phase,
                message=str(exc),
                http_status=status_code,
                suggested_action="Retry with backoff; use another model/provider if the outage continues.",
                metadata={"model_name": exc.model_name},
            )
        if status_code in {401, 403} or any(
            token in lowered
            for token in ("api key", "unauthorized", "forbidden", "authentication")
        ):
            return FailureClassification(
                error_type=type(exc).__name__,
                failure_kind="configuration",
                retryable=False,
                phase=phase,
                message=str(exc),
                http_status=status_code,
                suggested_action="Check the model provider secret and account access before retrying.",
                metadata={"model_name": exc.model_name},
            )
        if any(token in lowered for token in ("credit", "billing", "balance", "quota")):
            return FailureClassification(
                error_type=type(exc).__name__,
                failure_kind="billing",
                retryable=False,
                phase=phase,
                message=str(exc),
                http_status=status_code,
                suggested_action=(
                    "Check provider billing, credits, or quota before retrying; "
                    "switch models/providers if another configured option is available."
                ),
                metadata={"model_name": exc.model_name},
            )
        return FailureClassification(
            error_type=type(exc).__name__,
            failure_kind="bad_request",
            retryable=False,
            phase=phase,
            message=str(exc),
            http_status=status_code,
            suggested_action="Change the model request or selected model before retrying.",
            metadata={"model_name": exc.model_name},
        )
    if isinstance(exc, ModelAPIError):
        lowered = str(exc).lower()
        if any(
            token in lowered
            for token in (
                "connection error",
                "connection closed",
                "connection reset",
                "disconnected",
                "network error",
            )
        ):
            return FailureClassification(
                error_type=type(exc).__name__,
                failure_kind="transient_network",
                retryable=True,
                phase=phase,
                message=str(exc),
                suggested_action=(
                    "Retry with backoff; check network connectivity if failures continue."
                ),
                metadata={"model_name": exc.model_name},
            )
    if isinstance(exc, UnexpectedModelBehavior) and (
        "streamed response ended without content or tool calls" in str(exc).lower()
    ):
        return FailureClassification(
            error_type=type(exc).__name__,
            failure_kind="transient_provider",
            retryable=True,
            phase=phase,
            message=str(exc),
            suggested_action=(
                "Retry with backoff; use another model/provider if the stream remains incomplete."
            ),
        )
    if isinstance(exc, httpx.TimeoutException):
        return FailureClassification(
            error_type=type(exc).__name__,
            failure_kind="transient_network",
            retryable=True,
            phase=phase,
            message=str(exc),
            suggested_action="Retry with backoff, or narrow the request if repeated timeouts occur.",
        )
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        retry_after = exc.response.headers.get("Retry-After")
        if status_code == 429:
            return FailureClassification(
                error_type=type(exc).__name__,
                failure_kind="rate_limited",
                retryable=True,
                phase=phase,
                message=str(exc),
                http_status=status_code,
                retry_after=retry_after,
                suggested_action="Retry after the provider rate-limit window, respecting Retry-After when present.",
            )
        if 500 <= status_code <= 599:
            return FailureClassification(
                error_type=type(exc).__name__,
                failure_kind="provider_unavailable",
                retryable=True,
                phase=phase,
                message=str(exc),
                http_status=status_code,
                retry_after=retry_after,
                suggested_action="Retry with backoff; use another source if the provider remains unavailable.",
            )
        if status_code in {401, 403}:
            return FailureClassification(
                error_type=type(exc).__name__,
                failure_kind="configuration",
                retryable=False,
                phase=phase,
                message=str(exc),
                http_status=status_code,
                suggested_action="Check the provider secret, account access, or tool configuration before retrying.",
            )
        return FailureClassification(
            error_type=type(exc).__name__,
            failure_kind="bad_request",
            retryable=False,
            phase=phase,
            message=str(exc),
            http_status=status_code,
            suggested_action="Change the request parameters before retrying.",
        )
    if isinstance(exc, httpx.RequestError):
        return FailureClassification(
            error_type=type(exc).__name__,
            failure_kind="transient_network",
            retryable=True,
            phase=phase,
            message=str(exc),
            suggested_action="Retry with backoff; check network connectivity if failures continue.",
        )

    lowered = str(exc).lower()
    if "overloaded" in lowered:
        return FailureClassification(
            error_type=type(exc).__name__,
            failure_kind="provider_overloaded",
            retryable=True,
            phase=phase,
            message=str(exc),
            suggested_action=(
                "Retry with backoff; use another model/provider if capacity pressure continues."
            ),
        )
    if any(
        token in lowered
        for token in ("timeout", "temporarily", "rate limit", "too many requests")
    ):
        return FailureClassification(
            error_type=type(exc).__name__,
            failure_kind="transient_provider",
            retryable=True,
            phase=phase,
            message=str(exc),
            suggested_action="Retry with backoff; reduce request scope if the failure repeats.",
        )
    if any(
        token in lowered
        for token in (
            "api key",
            "unauthorized",
            "forbidden",
            "authentication",
            "oauth",
            "connected account",
        )
    ):
        return FailureClassification(
            error_type=type(exc).__name__,
            failure_kind="configuration",
            retryable=False,
            phase=phase,
            message=str(exc),
            suggested_action="Check the provider secret, account access, or tool configuration before retrying.",
        )
    if any(token in lowered for token in ("credit", "billing", "balance", "quota")):
        return FailureClassification(
            error_type=type(exc).__name__,
            failure_kind="billing",
            retryable=False,
            phase=phase,
            message=str(exc),
            suggested_action=(
                "Check provider billing, credits, or quota before retrying; "
                "switch providers if another configured option is available."
            ),
        )
    return FailureClassification(
        error_type=type(exc).__name__,
        failure_kind="unknown",
        retryable=False,
        phase=phase,
        message=str(exc),
        suggested_action="Inspect the error details and adjust the request or configuration before retrying.",
    )


def tool_failure_return(
    *,
    tool_name: str,
    message: str,
    classification: FailureClassification,
    metadata: dict[str, Any] | None = None,
    include_suggested_action: bool = False,
) -> ToolReturn:
    """Build a ToolReturn with stable structured failure metadata."""
    payload = classification.to_metadata()
    payload["tool_name"] = tool_name
    if metadata:
        payload.update(metadata)
    detail = classification.message.strip()
    return_value = message if not detail else f"{message}: {detail}"
    if include_suggested_action and classification.suggested_action.strip():
        return_value = f"{return_value} {classification.suggested_action.strip()}"
    return ToolReturn(return_value=return_value, content=None, metadata=payload)
