"""Provider URL readiness resolves real principal-owned secret configuration."""

from __future__ import annotations

import httpx
import pytest

from validation.pytest_scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_custom_provider_url_secret_and_invalid_openai_endpoint(
    app: Application,
) -> None:
    app.evidence.register_secret("validation-openai-key", "https://provider.example/v1")
    await app.start()
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.llm.openai_runtime import build_openai_provider_with_resolution
    from core.llm.provider_policy import custom_provider_base_url_available
    from core.settings.secrets_store import get_secret_value

    assert (
        app.request(
            "/api/system/secrets",
            "PUT",
            {"name": "CUSTOM_BASE_URL", "value": "https://provider.example/v1"},
        ).status_code
        == 200
    )
    assert (
        app.request(
            "/api/system/secrets",
            "PUT",
            {"name": "OPENAI_API_KEY", "value": "validation-openai-key"},
        ).status_code
        == 200
    )
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        assert not custom_provider_base_url_available(
            {"base_url": "MISSING_BASE_URL"}, get_secret_value=get_secret_value
        )
        assert custom_provider_base_url_available(
            {"base_url": "CUSTOM_BASE_URL"}, get_secret_value=get_secret_value
        )
        for value in (
            "https://",
            "https:///path",
            "https:// invalid.example",
            "ftp://provider.example",
        ):
            assert not custom_provider_base_url_available(
                {"base_url": value}, get_secret_value=get_secret_value
            )
        async with httpx.AsyncClient(trust_env=False) as client:
            with pytest.raises(ValueError, match=r"complete HTTP\(S\) URL"):
                build_openai_provider_with_resolution(
                    provider_config={
                        "api_key": "OPENAI_API_KEY",
                        "base_url": "MISSING_OPENAI_BASE_URL",
                        "auth_mode": "api_key",
                    },
                    http_client=client,
                )
