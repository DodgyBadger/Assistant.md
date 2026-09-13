"""Validate app-wide disabled tool registry behavior."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from validation.core.base_scenario import BaseScenario, with_local_user_authority


class DisabledToolsRegistryScenario(BaseScenario):
    """Validate disabled_tools filters model and direct tool binding surfaces."""

    @with_local_user_authority
    async def test_scenario(self) -> None:
        vault = self.create_vault("DisabledToolsRegistryVault")
        await self.start_system()
        try:
            update_response = self.call_api(
                "/api/system/settings/general/disabled_tools",
                method="PUT",
                data={"value": json.dumps(["session_ops"])},
            )
            assert (
                update_response.status_code == 200
            ), "disabled_tools setting should update"

            metadata_response = self.call_api("/api/metadata")
            assert (
                metadata_response.status_code == 200
            ), "Metadata endpoint should succeed"
            metadata = metadata_response.json()
            assert metadata.get("settings", {}).get("disabled_tools") == [
                "session_ops"
            ], "Metadata should expose resolved disabled tools"
            assert "enabled_tools" not in metadata.get(
                "settings", {}
            ), "Metadata should not expose removed enabled_tools setting"

            from core.authoring.shared.tool_binding import resolve_tool_binding
            from core.settings.store import get_enabled_tool_names

            enabled_names = get_enabled_tool_names()
            assert (
                "session_ops" not in enabled_names
            ), "Disabled tools should be absent from the derived enabled registry"
            assert (
                "file_read" in enabled_names
            ), "Unlisted registered tools should be enabled automatically"
            assert (
                "workflow_run" in enabled_names
            ), "Tools omitted by an old allowlist should be available under denylist policy"

            binding = resolve_tool_binding(["file_read"], vault_path=str(vault))
            assert binding.tool_names() == [
                "file_read"
            ], "Unlisted tools should bind normally"

            all_binding = resolve_tool_binding("all", vault_path=str(vault))
            assert (
                "session_ops" not in all_binding.tool_names()
            ), "Binding all should exclude app-wide disabled tools"
            assert (
                "file_read" in all_binding.tool_names()
            ), "Binding all should include unlisted registered tools"
            assert (
                "workflow_run" in all_binding.tool_names()
            ), "Binding all should include every available registered tool"

            try:
                resolve_tool_binding(["session_ops"], vault_path=str(vault))
            except ValueError as exc:
                assert "unavailable or disabled" in str(exc)
            else:
                raise AssertionError("Explicitly disabled tools should fail clearly")
        finally:
            await self.stop_system()
            self.teardown_scenario()
