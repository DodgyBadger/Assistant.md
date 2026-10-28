"""App-wide tool denylist applies to metadata and authoring bindings."""

import json

import pytest

from validation.scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_disabled_tools_registry(app: Application) -> None:
    vault = app.vault("DisabledToolsRegistryVault")
    await app.start()
    from core.authoring.shared.tool_binding import resolve_tool_binding
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.settings.store import get_enabled_tool_names

    updated = app.request(
        "/api/system/settings/general/disabled_tools",
        method="PUT",
        data={"value": json.dumps(["session_ops"])},
    )
    assert updated.status_code == 200, updated.text
    metadata = app.request("/api/metadata")
    assert metadata.status_code == 200
    settings = metadata.json()["settings"]
    assert settings["disabled_tools"] == ["session_ops"]
    assert "enabled_tools" not in settings
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        enabled = get_enabled_tool_names()
        assert "session_ops" not in enabled
        assert {"file_read", "workflow_run"} <= set(enabled)
        assert resolve_tool_binding(
            ["file_read"], vault_path=str(vault)
        ).tool_names() == ["file_read"]
        all_tools = resolve_tool_binding("all", vault_path=str(vault)).tool_names()
        assert "session_ops" not in all_tools
        assert {"file_read", "workflow_run"} <= set(all_tools)
        with pytest.raises(ValueError, match="unavailable or disabled"):
            resolve_tool_binding(["session_ops"], vault_path=str(vault))
