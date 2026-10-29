"""The real OpenRouter model factory sends configured attribution headers."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.openrouter_attribution_inputs import (
    attribution_environment,
    attribution_provider,
)

pytestmark = pytest.mark.scenario


async def test_factory_openrouter_model_sends_optional_attribution_to_provider(
    app: Application,
) -> None:
    key = "openrouter-fixture-key"
    app.evidence.register_secret(key)
    await app.start()
    from pydantic_ai.models.openrouter import OpenRouterModel

    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.llm.agents import create_agent, generate_response
    from core.llm.model_factory import build_model_instance

    assert (
        app.request(
            "/api/system/secrets", "PUT", {"name": "OPENROUTER_API_KEY", "value": key}
        ).status_code
        == 200
    )
    assert (
        app.request(
            "/api/system/models/openrouter-fixture",
            "PUT",
            {
                "provider": "openrouter",
                "model_string": "openai/gpt-4o-mini",
                "capabilities": ["text"],
            },
        ).status_code
        == 200
    )
    with attribution_provider() as server:
        for enabled in (True, False):
            with (
                attribution_environment(enabled) as values,
                use_execution_authority(LOCAL_USER_AUTHORITY),
            ):
                model = build_model_instance("openrouter-fixture")
                assert isinstance(model, OpenRouterModel)
                model.client.base_url = f"http://127.0.0.1:{server.server_port}/v1"
                try:
                    agent = await create_agent(model=model)
                    result = await generate_response(agent, "Test attribution headers.")
                    assert result
                finally:
                    await model.client.close()
            request = server.requests[-1]
            assert request["path"] == "/v1/chat/completions"
            assert request["body"]["model"] == "openai/gpt-4o-mini"
            headers = {
                name.lower(): value for name, value in request["headers"].items()
            }
            assert headers["authorization"] == f"Bearer {key}"
            if enabled:
                assert headers["http-referer"] == values["OPENROUTER_APP_URL"]
                assert headers["x-title"] == values["OPENROUTER_APP_TITLE"]
            else:
                assert "http-referer" not in headers and "x-title" not in headers
        assert len(server.requests) == 2
