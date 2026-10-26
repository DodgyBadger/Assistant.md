"""Provider failure diagnostics retain useful reasons, not raw request bodies."""

from __future__ import annotations

import anthropic
import httpx
import openai
import pytest
from google.genai.errors import ClientError
from pydantic_ai.exceptions import ModelHTTPError

from core.utils.provider_errors import (
    ProviderRequestError,
    provider_exception_details,
    provider_response_error_details,
)


@pytest.mark.parametrize("provider", ["pydantic-ai", "openai", "anthropic", "google"])
def test_sdk_provider_rejections_retain_message_and_status(provider: str) -> None:
    body = {
        "error": {
            "message": "Invalid model: unavailable",
            "type": "invalid_model",
            "code": "1500",
            "input": "PRIVATE_INPUT",
        }
    }
    response = httpx.Response(
        400, request=httpx.Request("POST", "https://provider.example/api")
    )
    exc: Exception
    if provider == "pydantic-ai":
        exc = ModelHTTPError(400, "unavailable", body)
    elif provider == "openai":
        exc = openai.BadRequestError("raw error", response=response, body=body)
    elif provider == "anthropic":
        exc = anthropic.BadRequestError("raw error", response=response, body=body)
    else:
        exc = ClientError(400, body)
    details = provider_exception_details(exc)
    assert details["http_status"] == 400
    assert "Invalid model: unavailable" in str(details["error"])
    assert details["provider_error_type"] == "invalid_model"
    assert details["provider_error_code"] == "1500"
    assert "PRIVATE_INPUT" not in str(details)


def test_provider_message_redactions_and_bounding() -> None:
    details = provider_response_error_details(
        400,
        {
            "message": "Bad request PRIVATE_KEY data:application/pdf;base64,SECRET_BYTES Bearer TOKEN https://user:pass@example.org/report?token=SECRET_QUERY\n"
            + "x" * 2000,
            "document": "PRIVATE_DOCUMENT",
        },
        redactions=("PRIVATE_KEY",),
    )
    assert details["error"].startswith(
        "Provider request failed (HTTP 400): Bad request"
    )
    assert len(str(details["provider_error_message"])) <= 500
    assert all(
        value not in str(details)
        for value in (
            "PRIVATE_KEY",
            "SECRET_BYTES",
            "TOKEN",
            "user:pass",
            "SECRET_QUERY",
            "PRIVATE_DOCUMENT",
            "\n",
        )
    )


@pytest.mark.parametrize(
    "body",
    [
        "<html>PRIVATE_RESPONSE</html>",
        ["PRIVATE_RESPONSE"],
        {"detail": [{"input": "PRIVATE_RESPONSE"}]},
    ],
)
def test_unstructured_provider_responses_do_not_leak(body: object) -> None:
    details = provider_response_error_details(502, body)
    assert details["http_status"] == 502
    assert "PRIVATE_RESPONSE" not in str(details)
    assert "No structured provider error message" in str(details["error"])


def test_explicit_safe_errors_and_wrapped_sdk_failures() -> None:
    error = ProviderRequestError(
        provider="mistral", status_code=429, body='{"message":"Rate limit exceeded"}'
    )
    wrapper = RuntimeError("PRIVATE_WRAPPER")
    wrapper.__cause__ = error
    assert provider_exception_details(wrapper)["provider"] == "mistral"
    assert "Rate limit exceeded" in str(provider_exception_details(wrapper)["error"])
    assert provider_exception_details(RuntimeError("PRIVATE_DOCUMENT")) == {}
    cyclic = RuntimeError("PRIVATE")
    cyclic.__cause__ = cyclic
    assert provider_exception_details(cyclic) == {}
