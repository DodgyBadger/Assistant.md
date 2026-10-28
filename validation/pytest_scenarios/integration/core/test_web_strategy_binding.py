"""Web tool binding follows configured strategy and owned credentials."""

from __future__ import annotations

import pytest

from validation.pytest_scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_web_binding_tracks_strategy_credentials_and_invalid_configuration(
    app: Application,
) -> None:
    app.evidence.register_secret("tavily-validation-secret")
    vault = app.vault("WebStrategyBindingVault")
    await app.start()
    from core.authoring.shared.tool_binding import resolve_tool_binding
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.settings import validate_settings

    with use_execution_authority(LOCAL_USER_AUTHORITY):
        baseline = resolve_tool_binding(
            ["web_search", "web_extract"], vault_path=str(vault)
        )
        assert baseline.tool_names() == ["web_search", "web_extract"]
        crawl = resolve_tool_binding(["web_crawl"], vault_path=str(vault))
        assert crawl.tool_names() == [] and "TAVILY_API_KEY" in crawl.tool_instructions
    update = app.request(
        "/api/system/settings/general/web_extract_strategy", "PUT", {"value": "tavily"}
    )
    assert update.status_code == 200
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        tavily = resolve_tool_binding(["web_extract"], vault_path=str(vault))
        assert (
            tavily.tool_names() == [] and "TAVILY_API_KEY" in tavily.tool_instructions
        )
        status = validate_settings()
        assert status.tool_availability.get("web_extract") is False
        issue = next(
            issue for issue in status.issues if issue.name == "tool:web_extract"
        )
        assert "strategy 'tavily'" in issue.message
    secret = app.request(
        "/api/system/secrets",
        "PUT",
        {"name": "TAVILY_API_KEY", "value": "tavily-validation-secret"},
    )
    assert secret.status_code == 200
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        assert resolve_tool_binding(
            ["web_extract", "web_crawl"], vault_path=str(vault)
        ).tool_names() == ["web_extract", "web_crawl"]
    snapshot = app.configuration()
    snapshot["settings"]["web_search_strategy"]["value"] = "unknown-validation-strategy"
    app.install_configuration(snapshot)
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        invalid = resolve_tool_binding("all", vault_path=str(vault))
        assert (
            "web_search" not in invalid.tool_names()
            and "file_read" in invalid.tool_names()
        )
        assert "invalid configuration" in invalid.tool_instructions.lower()
