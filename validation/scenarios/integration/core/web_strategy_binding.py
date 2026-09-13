"""Validate strategy-aware availability through shared tool binding."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.authoring.shared.tool_binding import resolve_tool_binding
from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
from core.settings import validate_settings
from core.web.config import get_web_tool_strategy_requirements
from validation.core.base_scenario import BaseScenario


class WebStrategyBindingScenario(BaseScenario):
    """Prove provider requirements apply across settings-backed tool binding."""

    async def test_scenario(self) -> None:
        vault = self.create_vault("WebStrategyBindingVault")
        await self.start_system()
        try:
            with use_execution_authority(LOCAL_USER_AUTHORITY):
                baseline = resolve_tool_binding(
                    ["web_search", "web_extract"], vault_path=str(vault)
                )
            self.soft_assert_equal(
                baseline.tool_names(),
                ["web_search", "web_extract"],
                "Secret-free default web strategies should bind",
            )

            with use_execution_authority(LOCAL_USER_AUTHORITY):
                crawl = resolve_tool_binding(["web_crawl"], vault_path=str(vault))
            self.soft_assert_equal(
                crawl.tool_names(),
                [],
                "Tavily crawl should be unavailable without its selected strategy secret",
            )
            self.soft_assert(
                "TAVILY_API_KEY" in crawl.tool_instructions,
                "Skipped strategy should identify its missing secret",
            )

            update = self.call_api(
                "/api/system/settings/general/web_extract_strategy",
                method="PUT",
                data={"value": "tavily"},
            )
            self.soft_assert_equal(
                update.status_code,
                200,
                "Web extraction strategy should be user configurable",
            )
            with use_execution_authority(LOCAL_USER_AUTHORITY):
                tavily_extract = resolve_tool_binding(
                    ["web_extract"], vault_path=str(vault)
                )
            self.soft_assert_equal(
                tavily_extract.tool_names(),
                [],
                "A Tavily-selected extraction capability should not fall back to curl",
            )
            self.soft_assert(
                "TAVILY_API_KEY" in tavily_extract.tool_instructions,
                "Tavily extraction unavailability should explain the missing secret",
            )

            with use_execution_authority(LOCAL_USER_AUTHORITY):
                status = validate_settings()
            self.soft_assert_equal(
                status.tool_availability.get("web_extract"),
                False,
                "Configuration health should mark the selected strategy unavailable",
            )
            matching_issue = next(
                (issue for issue in status.issues if issue.name == "tool:web_extract"),
                None,
            )
            self.soft_assert(
                matching_issue is not None
                and "strategy 'tavily'" in matching_issue.message,
                "Configuration health should identify the selected strategy",
            )

            def invalid_search_strategy(tool_name: str) -> tuple[str, tuple[str, ...]]:
                if tool_name == "web_search":
                    raise ValueError("unknown configured strategy")
                return get_web_tool_strategy_requirements(tool_name)

            with patch(
                "core.authoring.shared.tool_binding.get_web_tool_strategy_requirements",
                side_effect=invalid_search_strategy,
            ):
                with use_execution_authority(LOCAL_USER_AUTHORITY):
                    invalid = resolve_tool_binding("all", vault_path=str(vault))
            self.soft_assert(
                "web_search" not in invalid.tool_names()
                and "file_read" in invalid.tool_names(),
                "Invalid strategy configuration should not prevent the remaining binding pass",
            )
            self.soft_assert(
                "invalid configuration" in invalid.tool_instructions.lower(),
                "Skipped invalid strategies should remain visible in binding instructions",
            )
        finally:
            await self.stop_system()
            self.teardown_scenario()
        self.assert_no_failures()
