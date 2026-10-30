"""Bounded provider rejection diagnostics without raw response payloads."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence

from anthropic import APIStatusError as AnthropicAPIStatusError
from google.genai.errors import APIError as GoogleAPIError
from openai import APIStatusError as OpenAIAPIStatusError
from pydantic_ai.exceptions import ModelHTTPError

from core.web.security import sanitize_urls_in_text_for_log


def _safe_provider_text(value: str, redactions: Sequence[str]) -> str:
    for private_value in redactions:
        if private_value:
            value = value.replace(private_value, "[redacted]")
    value = re.sub(r"data:[^\s\"'<>]+", "[redacted document]", value, flags=re.I)
    value = re.sub(r"\bBearer\s+[^\s,;\"']+", "Bearer [redacted]", value, flags=re.I)
    value = re.sub(r"\bsk-[\w-]+", "[redacted]", value)
    value = re.sub(
        r"(?i)\b(api[_-]?key|access_token|refresh_token|authorization)[\"']?\s*[=:]\s*[\"']?[^\s,;\"'}]+",
        r"\1=[redacted]",
        value,
    )
    value = sanitize_urls_in_text_for_log(value)
    value = " ".join("".join(c if c.isprintable() else " " for c in value).split())
    return value[:500]


def provider_response_error_details(
    status_code: int, body: object, *, redactions: Sequence[str] = ()
) -> dict[str, object]:
    """Project only the provider's error message/type/code, never its input fields."""
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            body = None
    details: dict[str, object] = {"http_status": status_code}
    if isinstance(body, Mapping):
        error = body.get("error", body)
        if isinstance(error, str):
            error = {"type": error, "message": body.get("error_description", error)}
        if isinstance(error, Mapping):
            for source, target in (
                ("message", "provider_error_message"),
                ("type", "provider_error_type"),
                ("code", "provider_error_code"),
            ):
                value = error.get(source)
                if isinstance(value, str | int) and not isinstance(value, bool):
                    text = _safe_provider_text(str(value), redactions)
                    details[target] = text if source == "message" else text[:100]
    message = (
        details.get("provider_error_message")
        or "No structured provider error message was returned."
    )
    details["error"] = f"Provider request failed (HTTP {status_code}): {message}"
    return details


class ProviderRequestError(RuntimeError):
    """An API boundary has projected a provider rejection into safe diagnostics."""

    def __init__(
        self,
        *,
        provider: str,
        status_code: int,
        body: object,
        redactions: Sequence[str] = (),
    ) -> None:
        self.details = provider_response_error_details(
            status_code, body, redactions=redactions
        )
        self.details["provider"] = provider
        super().__init__(str(self.details["error"]))


def provider_exception_details(
    exc: BaseException, *, include_transport_errors: bool = True
) -> dict[str, object]:
    """Find structured HTTP diagnostics in SDK exceptions and explicit causes.

    Shared task/tool callers can exclude bare transport exceptions so ordinary
    HTTP failures retain their diagnostics rather than becoming provider errors.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(seen) < 8:
        seen.add(id(current))
        if isinstance(current, ProviderRequestError):
            return dict(current.details)
        if not include_transport_errors and not isinstance(
            current,
            AnthropicAPIStatusError
            | GoogleAPIError
            | OpenAIAPIStatusError
            | ModelHTTPError,
        ):
            current = current.__cause__
            continue
        status = getattr(current, "status_code", None)
        if not isinstance(status, int):
            status = getattr(getattr(current, "response", None), "status_code", None)
        if not isinstance(status, int) and isinstance(
            getattr(current, "details", None), Mapping
        ):
            status = getattr(current, "code", None)
        if (
            isinstance(status, int)
            and not isinstance(status, bool)
            and 100 <= status <= 599
        ):
            body = getattr(current, "body", None)
            if body is None:
                body = getattr(current, "details", None)
            google_error = (
                current if isinstance(current, GoogleAPIError) else current.__cause__
            )
            if isinstance(google_error, GoogleAPIError):
                response = google_error.response
                if response is not None and response.status_code >= 400:
                    # The Google SDK promotes non-JSON response text into a
                    # synthetic message. Use the original wire JSON so raw
                    # bodies cannot become trusted provider diagnostics.
                    decode = getattr(response, "json", None)
                    body = None
                    if callable(decode):
                        try:
                            body = decode()
                        except ValueError:
                            pass
            return provider_response_error_details(status, body)
        current = current.__cause__
    return {}
