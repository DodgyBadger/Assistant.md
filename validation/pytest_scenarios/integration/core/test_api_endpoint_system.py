"""System configuration and principal-owned API resources through FastAPI."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from validation.pytest_scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_system_health_settings_models_providers_and_secrets(
    app: Application,
) -> None:
    for secret in ("validation-secret-123", "validation-token"):
        app.evidence.register_secret(secret)
    app.vault("IntegrationApiVault")
    assert app.request("/api/health").status_code == 503
    await app.start()
    assert app.request("/api/health").status_code == 200
    status = app.request("/api/status")
    assert status.status_code == 200 and status.json()["total_vaults"] == 1
    version = tomllib.loads(
        (Path(__file__).resolve().parents[4] / "pyproject.toml").read_text()
    )["project"]["version"]
    assert status.json()["system"]["app_version"] == version
    assert app.request("/openapi.json").json()["info"]["version"] == version
    assert app.request("/api/system/activity-log").status_code == 200
    general = app.request("/api/system/settings/general")
    assert general.status_code == 200 and general.json()
    first = general.json()[0]
    assert (
        app.request(
            f"/api/system/settings/general/{first['key']}",
            "PUT",
            {"value": first["value"]},
        ).status_code
        == 200
    )
    models = app.request("/api/system/models")
    assert models.status_code == 200 and models.json()
    base = models.json()[0]
    assert (
        app.request(
            "/api/system/models/validation-test-model",
            "PUT",
            {
                "provider": base["provider"],
                "model_string": base["model_string"],
                "description": "Validation alias",
            },
        ).status_code
        == 200
    )
    assert (
        app.request("/api/system/models/validation-test-model", "DELETE").status_code
        == 200
    )
    providers = app.request("/api/system/providers")
    assert providers.status_code == 200
    openai = next(item for item in providers.json() if item["name"] == "openai")
    assert (
        openai["user_editable"],
        openai["configured_auth_mode"],
        openai["effective_auth_mode"],
        openai["oauth_enabled"],
        openai["oauth_status"],
    ) == (False, "api_key", "api_key", False, "disabled")
    assert (
        app.request(
            "/api/system/settings/general/editable_builtin_providers",
            "PUT",
            {"value": '["openai"]'},
        ).status_code
        == 200
    )
    assert (
        app.request(
            "/api/system/providers/validation-provider",
            "PUT",
            {
                "api_key": "VALIDATION_PROVIDER_KEY",
                "base_url": "VALIDATION_PROVIDER_BASE_URL",
            },
        ).status_code
        == 200
    )
    assert (
        app.request("/api/system/providers/validation-provider", "DELETE").status_code
        == 200
    )
    assert isinstance(app.request("/api/system/secrets").json(), list)
    assert (
        app.request(
            "/api/system/secrets",
            "PUT",
            {"name": "VALIDATION_TEMP_SECRET", "value": "validation-secret-123"},
        ).status_code
        == 200
    )
    assert any(
        item["name"] == "VALIDATION_TEMP_SECRET" and item["has_value"]
        for item in app.request("/api/system/secrets").json()
    )
    assert (
        app.request(
            "/api/system/secrets",
            "PUT",
            {
                "name": "OPENAI_OAUTH_TOKEN_STATE",
                "value": json.dumps(
                    {
                        "access_token": "validation-token",
                        "account_id": "validation-account",
                    }
                ),
            },
        ).status_code
        == 200
    )
    secrets = app.request("/api/system/secrets").json()
    assert all(
        item["name"] not in {"OPENAI_OAUTH_TOKEN_STATE", "OPENAI_OAUTH_PENDING_STATE"}
        for item in secrets
    )
    assert "validation-token" not in json.dumps(
        secrets
    ) and "validation-secret-123" not in json.dumps(secrets)
    assert (
        app.request(
            "/api/system/secrets",
            "PUT",
            {"name": "VALIDATION_TEMP_SECRET", "value": ""},
        ).status_code
        == 200
    )
    assert not any(
        item["name"] == "VALIDATION_TEMP_SECRET" and item["has_value"]
        for item in app.request("/api/system/secrets").json()
    )


async def test_mcp_configuration_api_preserves_owner_and_write_only_credentials(
    app: Application,
) -> None:
    app.evidence.register_secret("validation-mcp-token")
    app.vault("IntegrationApiVault")
    await app.start()
    base = "/api/system/mcp/connections"
    rejected = app.request(
        base,
        "POST",
        {
            "display_name": "Injected owner",
            "url": "https://example.com/mcp",
            "owner_principal_id": "foreign-user",
        },
    )
    assert rejected.status_code == 422
    created = await app.request_async(
        base,
        "POST",
        {
            "display_name": "Validation MCP",
            "url": "https://example.com/mcp",
            "transport": "streamable_http",
            "auth_mode": "bearer",
            "enabled": True,
            "allow_private_http": True,
            "allowed_tools": ["search"],
            "credential": "validation-mcp-token",
        },
    )
    assert created.status_code == 200
    data = created.json()
    connection_id = data["connection_id"]
    assert (data["slug"], data["credential_present"], data["allow_private_http"]) == (
        "validation-mcp",
        True,
        True,
    )
    assert "credential" not in data and "validation-mcp-token" not in created.text
    listed = app.request(base)
    assert listed.status_code == 200 and [
        item["connection_id"] for item in listed.json()
    ] == [connection_id]
    assert "validation-mcp-token" not in listed.text
    updated = await app.request_async(
        f"{base}/{connection_id}",
        "PUT",
        {
            "display_name": "Renamed Validation MCP",
            "url": "https://example.com/mcp",
            "transport": "sse",
            "auth_mode": "bearer",
            "header_name": None,
            "enabled": False,
            "allow_private_http": False,
            "allowed_tools": None,
        },
    )
    assert updated.status_code == 200 and (
        updated.json()["slug"],
        updated.json()["allow_private_http"],
    ) == ("validation-mcp", False)
    cleared = await app.request_async(f"{base}/{connection_id}/credential", "DELETE")
    assert cleared.status_code == 200 and cleared.json()["credential_present"] is False
    assert (
        await app.request_async(f"{base}/{connection_id}", "DELETE")
    ).status_code == 200
    assert app.request(f"{base}/{connection_id}/test", "POST").status_code == 404
