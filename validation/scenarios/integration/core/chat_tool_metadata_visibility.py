"""
Integration scenario validating chat tool metadata exposes the expected tool surface.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from validation.support.base_scenario import BaseScenario


class ChatToolMetadataVisibilityScenario(BaseScenario):
    """Validate chat metadata exposes expected current tools."""

    async def test_scenario(self):
        self.create_vault("ChatToolMetadataVisibilityVault")

        await self.start_system()
        try:
            response = self.call_api("/api/metadata")
            assert response.status_code == 200, "Metadata endpoint should succeed"
            payload = response.json()
            tools = payload.get("tools", [])
            tool_names = {tool.get("name") for tool in tools}
            tool_descriptions = {
                tool.get("name"): str(tool.get("description") or "") for tool in tools
            }

            self.soft_assert(
                "code_execution" in tool_names,
                "Chat metadata should expose code_execution",
            )
            self.soft_assert(
                "session_ops" in tool_names,
                "Chat metadata should expose session_ops",
            )
            self.soft_assert(
                "web_search" in tool_names,
                "Chat metadata should expose stable web_search",
            )
            self.soft_assert(
                "web_extract" in tool_names,
                "Chat metadata should expose stable web_extract",
            )
            self.soft_assert(
                "web_crawl" in tool_names,
                "Chat metadata should expose stable web_crawl",
            )
            self.soft_assert(
                "dynamic" in tool_descriptions.get("browser", "").lower(),
                "Browser metadata should identify its dynamic-page role",
            )
        finally:
            await self.stop_system()
            self.teardown_scenario()
