# Deferred reference only; see validation/registry/pytest-gaps.json.
# Original complete source is available in Git before the 2026-10-31 cleanup.
"""Provider failure diagnostics retain useful reasons, not raw request bodies."""

from __future__ import annotations


from core.utils.provider_errors import (
    ProviderRequestError,
    provider_exception_details,
    provider_response_error_details,
)


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
