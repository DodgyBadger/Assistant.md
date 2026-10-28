"""API errors expose structured recovery fields without traceback details."""

from __future__ import annotations

import pytest

from validation.pytest_scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_api_error_envelopes_identify_missing_and_unexpected_failures(
    app: Application,
) -> None:
    app.vault("ApiErrorVault")
    await app.start()
    for path, params, expected_status, error, error_type in (
        (
            "/api/tasks/not-a-real-task",
            None,
            404,
            "ExecutionTaskNotFound",
            "ExecutionTaskNotFound",
        ),
        (
            "/api/workflows/file",
            {"global_id": "bad-id"},
            500,
            "InternalServerError",
            "ValueError",
        ),
    ):
        response = app.request(path, params=params)
        assert response.status_code == expected_status
        payload = response.json()
        assert payload["success"] is False and payload["error"] == error
        details = payload["details"]
        assert (
            details["status"],
            details["error_type"],
            details["phase"],
            details["retryable"],
        ) == ("failed", error_type, "api_request", False)
        assert (
            isinstance(details["suggested_action"], str)
            and details["suggested_action"].strip()
        )
        assert "traceback" not in details
        if expected_status == 404:
            assert details["task_id"] == "not-a-real-task"


async def test_provider_http_failures_and_retry_transport_contracts(
    app: Application,
) -> None:
    await app.start()
    import httpx
    import httpx2
    from pydantic_ai.providers.openai import OpenAIProvider
    from pydantic_ai.retries import (
        AsyncHTTPX2TenacityTransport,
        AsyncTenacityTransport,
        TenacityTransport,
        wait_retry_after,
    )

    from core.llm.model_factory import (
        _build_retrying_model_http_client,
        _build_retrying_model_httpx2_client,
        _is_retryable_model_http_exception,
        _is_retryable_model_httpx2_exception,
        _mark_provider_owns_http_client,
    )
    from core.llm.openai_client import build_openai_sdk_client
    from core.tools.failures import classify_exception
    from validation.pytest_scenarios.support.api_failure_http import failure_endpoint

    with failure_endpoint() as base:
        for status, expected in ((429, True), (503, True), (400, False)):
            async with httpx.AsyncClient(trust_env=False) as client:
                response = await client.get(f"{base}/{status}")
            with pytest.raises(httpx.HTTPStatusError) as error:
                response.raise_for_status()
            assert _is_retryable_model_http_exception(error.value) is expected
            classification = classify_exception(error.value, phase="model_request")
            if status == 429:
                assert classification.retryable and classification.retry_after == "11"
            elif status == 400:
                assert not classification.retryable
    assert (
        callable(wait_retry_after)
        and AsyncTenacityTransport is not None
        and TenacityTransport is not None
    )
    http_client = _build_retrying_model_http_client()
    try:
        assert isinstance(http_client._transport, AsyncTenacityTransport)
    finally:
        await http_client.aclose()
    httpx2_client = _build_retrying_model_httpx2_client()
    try:
        assert isinstance(httpx2_client._transport, AsyncHTTPX2TenacityTransport)
    finally:
        await httpx2_client.aclose()
    sdk = build_openai_sdk_client(
        api_key="validation-key",
        base_url="http://127.0.0.1:9/v1",
        http_client=httpx.AsyncClient(trust_env=False),
    )
    try:
        assert sdk.max_retries == 0
    finally:
        await sdk.close()
    owned = _build_retrying_model_http_client()
    provider = _mark_provider_owns_http_client(
        OpenAIProvider(base_url="http://127.0.0.1:9/v1", http_client=owned), owned
    )
    try:
        assert getattr(provider, "_own_http_client", None) is owned
    finally:
        await owned.aclose()
    with pytest.raises(httpx.UnsupportedProtocol) as invalid:
        async with httpx.AsyncClient(trust_env=False) as client:
            await client.get("not-a-url")
    assert not _is_retryable_model_http_exception(invalid.value)
    with pytest.raises(httpx2.UnsupportedProtocol) as invalid2:
        async with httpx2.AsyncClient(trust_env=False) as client:
            await client.get("not-a-url")
    assert not _is_retryable_model_httpx2_exception(invalid2.value)
