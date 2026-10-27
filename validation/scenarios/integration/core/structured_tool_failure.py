"""Integration scenario for structured tool failure envelopes."""

import sys
from pathlib import Path

import httpx
from pydantic_ai.messages import ToolReturn

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from validation.support.base_scenario import BaseScenario


class StructuredToolFailureScenario(BaseScenario):
    """Validate retryable/permanent/configuration failure metadata for tools."""

    async def test_scenario(self):
        from core.tools.failures import classify_exception
        from core.tools.web_common import web_tool_failure

        http_error = _http_status_error(503, retry_after="7")
        classification = classify_exception(http_error, phase="web_search")
        self.soft_assert_equal(
            classification.failure_kind,
            "provider_unavailable",
            "HTTP 5xx should be classified as provider_unavailable",
        )
        self.soft_assert(
            classification.retryable,
            "HTTP 5xx should be retryable",
        )
        self.soft_assert_equal(
            classification.retry_after,
            "7",
            "Retry-After should be preserved when present",
        )

        result = web_tool_failure(
            tool_name="web_search",
            strategy="tavily",
            exc=_http_status_error(503, retry_after="5"),
            phase="web_search",
        )
        self._assert_failure_metadata(
            result,
            expected={
                "tool_name": "web_search",
                "capability": "web_search",
                "strategy": "tavily",
                "status": "failed",
                "failure_kind": "provider_unavailable",
                "retryable": True,
                "phase": "web_search",
                "http_status": 503,
                "retry_after": "5",
            },
        )

        sensitive_query = "private search phrase"
        redacted = web_tool_failure(
            tool_name="web_search",
            strategy="duckduckgo",
            exc=RuntimeError(f"provider rejected {sensitive_query}"),
            phase="web_search",
            redactions=[sensitive_query],
        )
        self.soft_assert(
            sensitive_query not in str(redacted.return_value),
            "Structured web failures should redact request text",
        )

        self.teardown_scenario()
        self.assert_no_failures()

    def _assert_failure_metadata(
        self,
        result,
        *,
        expected: dict[str, object],
    ) -> None:
        self.soft_assert(
            isinstance(result, ToolReturn),
            "Tool failure should return a ToolReturn envelope",
        )
        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        for key, value in expected.items():
            self.soft_assert_equal(
                metadata.get(key),
                value,
                f"Failure metadata should include {key}",
            )
        self.soft_assert(
            isinstance(result.return_value, str) and result.return_value.strip(),
            "Tool failure should keep concise model-readable text",
        )
        self.soft_assert(
            "suggested_action" in metadata,
            "Tool failure metadata should include suggested_action",
        )


def _http_status_error(
    status_code: int, *, retry_after: str | None = None
) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.tavily.com/search")
    headers = {"Retry-After": retry_after} if retry_after else {}
    response = httpx.Response(status_code, headers=headers, request=request)
    return httpx.HTTPStatusError(
        "synthetic status failure", request=request, response=response
    )
