"""Integration scenario for API-safe error envelopes and retry readiness."""

import sys
from pathlib import Path

import httpx
import openai

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from validation.core.base_scenario import BaseScenario


class ApiErrorResilienceScenario(BaseScenario):
    """Validate actionable, safe API errors and Pydantic AI retry integration points."""

    async def test_scenario(self):
        import httpx
        from pydantic_ai.providers.openai import OpenAIProvider
        from pydantic_ai.retries import (
            AsyncTenacityTransport,
            TenacityTransport,
            wait_retry_after,
        )

        from core.llm.model_factory import (
            _build_retrying_model_http_client,
            _is_retryable_model_http_exception,
            _mark_provider_owns_http_client,
        )
        from core.llm.openai_client import build_openai_sdk_client
        from core.llm.provider_policy import custom_provider_base_url_available
        from core.llm.stream_retry import ModelStreamIdleTimeout
        from core.tools.failures import classify_exception

        self.create_vault("ApiErrorVault")
        await self.start_system()

        missing_task = self.call_api("/api/tasks/not-a-real-task")
        self.soft_assert_equal(
            missing_task.status_code,
            404,
            "Missing task endpoint should return a typed API error",
        )
        missing_payload = missing_task.json()
        self._assert_error_envelope(
            missing_payload,
            expected_error="ExecutionTaskNotFound",
            expected_retryable=False,
        )
        self.soft_assert_equal(
            missing_payload["details"].get("task_id"),
            "not-a-real-task",
            "APIException details should preserve relevant recovery ids",
        )

        invalid_vault = self.call_api(
            "/api/workflows/file",
            params={"global_id": "bad-id"},
        )
        self.soft_assert_equal(
            invalid_vault.status_code,
            500,
            "Unexpected API exceptions should return a safe server error",
        )
        invalid_payload = invalid_vault.json()
        self._assert_error_envelope(
            invalid_payload,
            expected_error="InternalServerError",
            expected_detail_error_type="ValueError",
            expected_retryable=False,
        )
        self.soft_assert(
            "traceback" not in (invalid_payload.get("details") or {}),
            "Non-debug API error details should not expose tracebacks",
        )

        rate_limited = _http_status_error(429, retry_after="11")
        rate_classification = classify_exception(rate_limited, phase="model_request")
        self.soft_assert(
            rate_classification.retryable,
            "Classification should mark rate limits retryable for retry transport policy",
        )
        self.soft_assert_equal(
            rate_classification.retry_after,
            "11",
            "Classification should preserve Retry-After for agent-visible recovery metadata",
        )

        bad_request = _http_status_error(400)
        bad_classification = classify_exception(bad_request, phase="model_request")
        self.soft_assert(
            not bad_classification.retryable,
            "Classification should mark permanent bad requests non-retryable",
        )

        generic_openai_error = openai.APIError(
            "An error occurred while processing your request.",
            request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
            body=None,
        )
        generic_openai_classification = classify_exception(
            generic_openai_error, phase="agent_stream"
        )
        self.soft_assert_equal(
            generic_openai_classification.failure_kind,
            "transient_provider",
            "Generic OpenAI API failures should have a transient provider classification",
        )
        self.soft_assert(
            generic_openai_classification.retryable,
            "Generic OpenAI API failures should support manual prompt retry",
        )

        permanent_openai_error = _openai_status_error(400)
        permanent_openai_classification = classify_exception(
            permanent_openai_error, phase="agent_stream"
        )
        self.soft_assert(
            not permanent_openai_classification.retryable,
            "Permanent OpenAI request errors should not support unchanged prompt retry",
        )

        overloaded = classify_exception(
            RuntimeError(
                "Our servers are currently overloaded. Please try again later."
            ),
            phase="agent_stream",
        )
        self.soft_assert_equal(
            overloaded.failure_kind,
            "provider_overloaded",
            "Streamed provider overload errors should have a distinct failure classification",
        )
        self.soft_assert(
            overloaded.retryable,
            "Streamed provider overload errors should support manual retry",
        )

        idle_timeout = classify_exception(
            ModelStreamIdleTimeout(12.5),
            phase="agent_stream",
        )
        self.soft_assert_equal(
            idle_timeout.failure_kind,
            "model_stream_idle_timeout",
            "Semantic stream stalls should have a distinct failure classification",
        )
        self.soft_assert(
            idle_timeout.retryable,
            "A semantic stream stall should support policy-controlled replay",
        )

        self.soft_assert(
            not custom_provider_base_url_available(
                {"base_url": "MISSING_BASE_URL"},
                get_secret_value=lambda _name: None,
            ),
            "An unresolved base URL secret pointer must not be treated as a URL",
        )
        self.soft_assert(
            custom_provider_base_url_available(
                {"base_url": "CUSTOM_BASE_URL"},
                get_secret_value=lambda _name: "https://provider.example/v1",
            ),
            "A populated base URL secret should make a custom provider available",
        )

        self.soft_assert(
            callable(wait_retry_after),
            "Pydantic AI wait_retry_after should be available for retry timing",
        )
        self.soft_assert(
            AsyncTenacityTransport is not None and TenacityTransport is not None,
            "Pydantic AI retry transports should be available for retry execution",
        )

        retrying_client = _build_retrying_model_http_client()
        try:
            self.soft_assert(
                isinstance(retrying_client._transport, AsyncTenacityTransport),
                "Model HTTP client should use Pydantic AI AsyncTenacityTransport",
            )
        finally:
            await retrying_client.aclose()

        sdk_http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={}))
        )
        sdk_client = build_openai_sdk_client(
            api_key="validation-key",
            base_url="https://provider.example/v1",
            http_client=sdk_http_client,
        )
        try:
            self.soft_assert_equal(
                sdk_client.max_retries,
                0,
                "AssistantMD retry clients should disable nested OpenAI SDK retries",
            )
        finally:
            await sdk_client.close()

        owned_client = _build_retrying_model_http_client()
        owned_provider = _mark_provider_owns_http_client(
            OpenAIProvider(base_url="http://localhost:9", http_client=owned_client),
            owned_client,
        )
        try:
            self.soft_assert(
                getattr(owned_provider, "_own_http_client", None) is owned_client,
                "Retrying model HTTP clients should be marked provider-owned for lifecycle management",
            )
        finally:
            await owned_client.aclose()

        self.soft_assert(
            _is_retryable_model_http_exception(_http_status_error(503)),
            "Model retry predicate should retry provider 5xx responses",
        )
        self.soft_assert(
            _is_retryable_model_http_exception(_http_status_error(429)),
            "Model retry predicate should retry provider rate limits",
        )
        self.soft_assert(
            _is_retryable_model_http_exception(
                httpx.ConnectError(
                    "synthetic connection failure",
                    request=httpx.Request("POST", "https://api.provider.test/v1/chat"),
                )
            ),
            "Model retry predicate should retry network request errors",
        )
        self.soft_assert(
            not _is_retryable_model_http_exception(_http_status_error(400)),
            "Model retry predicate should not retry permanent bad requests",
        )
        self.soft_assert(
            not _is_retryable_model_http_exception(
                httpx.UnsupportedProtocol(
                    "Request URL is missing an 'http://' or 'https://' protocol."
                )
            ),
            "Model retry predicate should not retry invalid provider URLs",
        )

        self.teardown_scenario()
        self.assert_no_failures()

    def _assert_error_envelope(
        self,
        payload: dict,
        *,
        expected_error: str,
        expected_retryable: bool,
        expected_detail_error_type: str | None = None,
    ) -> None:
        self.soft_assert_equal(
            payload.get("success"),
            False,
            "Error responses should keep success=false",
        )
        self.soft_assert_equal(
            payload.get("error"),
            expected_error,
            "Error response should preserve stable error type",
        )
        details = payload.get("details") or {}
        self.soft_assert_equal(
            details.get("status"),
            "failed",
            "Error details should include failure status",
        )
        self.soft_assert_equal(
            details.get("error_type"),
            expected_detail_error_type or expected_error,
            "Error details should include stable error_type",
        )
        self.soft_assert_equal(
            details.get("phase"),
            "api_request",
            "Error details should include phase",
        )
        self.soft_assert_equal(
            details.get("retryable"),
            expected_retryable,
            "Error details should include retryability",
        )
        self.soft_assert(
            isinstance(details.get("suggested_action"), str)
            and details["suggested_action"].strip(),
            "Error details should include suggested_action",
        )


def _http_status_error(
    status_code: int, *, retry_after: str | None = None
) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.provider.test/v1/chat")
    headers = {"Retry-After": retry_after} if retry_after else {}
    response = httpx.Response(status_code, headers=headers, request=request)
    return httpx.HTTPStatusError(
        "synthetic provider status failure", request=request, response=response
    )


def _openai_status_error(status_code: int) -> openai.APIStatusError:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(status_code, request=request)
    return openai.APIStatusError(
        "synthetic OpenAI status failure",
        response=response,
        body=None,
    )
