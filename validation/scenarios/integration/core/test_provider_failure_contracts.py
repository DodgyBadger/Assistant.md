"""Real provider failures retain retry metadata and safe diagnostics."""

from __future__ import annotations

import json

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_tasks import chat_turn
from validation.scenarios.support.provider_error_inputs import provider, tavily_service

pytestmark = pytest.mark.scenario


@pytest.mark.parametrize("status", [500, 503])
@pytest.mark.parametrize("retry_after", ["11", "Wed, 21 Oct 2037 07:28:00 GMT"])
async def test_real_tavily_503_exposes_retry_after_and_hides_query(
    app: Application, retry_after: str, status: int
) -> None:
    query, key = "private-search-query-marker", "tavily-validation-key"
    app.evidence.register_secret(query, key)
    await app.start()
    from pydantic_ai.messages import ToolReturn

    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.tools.web_search import WebSearch

    assert (
        app.request(
            "/api/system/secrets", "PUT", {"name": "TAVILY_API_KEY", "value": key}
        ).status_code
        == 200
    )
    assert (
        app.request(
            "/api/system/settings/general/web_search_strategy",
            "PUT",
            {"value": "tavily"},
        ).status_code
        == 200
    )
    with provider() as server:
        server.retry_after = retry_after
        server.status = status
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            result = await WebSearch.get_tool(
                service=tavily_service(server, query)
            ).function(query=query, max_results=4)
        assert isinstance(result, ToolReturn)
        assert (
            result.metadata["failure_kind"] == "provider_unavailable"
            and result.metadata["retryable"] is True
        )
        assert (
            result.metadata["http_status"] == status
            and result.metadata["retry_after"] == retry_after
        )
        assert (
            result.metadata["strategy"] == "tavily"
            and result.metadata["phase"] == "web_search"
        )
        assert query not in str(result.return_value) and query not in str(
            result.metadata
        )
        assert len(server.requests) == 1
        assert (
            server.requests[0]["body"]["query"] == query
            and server.requests[0]["body"]["max_results"] == 4
        )
        assert server.requests[0]["body"]["api_key"] == key
        activity = app.request(
            "/api/system/activity-log?limit=100&tag=web-capabilities"
        )
        assert (
            activity.status_code == 200
            and query not in activity.text
            and key not in activity.text
        )
        event = next(
            entry["data"]
            for entry in activity.json()["entries"]
            if entry.get("data", {}).get("event") == "web_capability_failed"
        )
        assert (
            event["status"],
            event["failure_kind"],
            event["retryable"],
            event["http_status"],
        ) == ("failed", "provider_unavailable", True, status)


async def test_configured_model_provider_503_and_bearer_header(
    app: Application,
) -> None:
    app.evidence.register_secret("overloaded-provider-key")
    vault = app.vault("ProviderOverloadVault")
    with provider() as server:
        await app.start()
        url = f"http://127.0.0.1:{server.server_port}/v1"
        app.evidence.register_secret(url)
        for name, value in (
            ("OVERLOAD_PROVIDER_URL", url),
            ("OVERLOAD_PROVIDER_KEY", "overloaded-provider-key"),
        ):
            assert (
                app.request(
                    "/api/system/secrets", "PUT", {"name": name, "value": value}
                ).status_code
                == 200
            )
        assert (
            app.request(
                "/api/system/providers/overload-provider",
                "PUT",
                {
                    "base_url": "OVERLOAD_PROVIDER_URL",
                    "api_key": "OVERLOAD_PROVIDER_KEY",
                },
            ).status_code
            == 200
        )
        assert (
            app.request(
                "/api/system/models/overload-model",
                "PUT",
                {
                    "provider": "overload-provider",
                    "model_string": "overload-model",
                    "capabilities": ["text"],
                },
            ).status_code
            == 200
        )
        result = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": "provider-overloaded",
                "prompt": "Exercise provider failure.",
                "model": "overload-model",
            },
        )
        assert result["terminal_event"]["event"] == "error"
        details = result["terminal_event"]["details"]
        assert (
            details["failure_kind"],
            details["retryable"],
            details["http_status"],
        ) == ("provider_unavailable", True, 503)
        assert server.requests and all(
            {name.lower(): value for name, value in request["headers"].items()}[
                "authorization"
            ]
            == "Bearer overloaded-provider-key"
            for request in server.requests
        )
        assert "overloaded-provider-key" not in json.dumps(result["terminal_event"])
