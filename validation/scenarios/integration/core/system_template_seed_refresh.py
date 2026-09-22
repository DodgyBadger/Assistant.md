"""
Integration scenario for manual refresh of packaged system authoring seeds.

Validates that startup preserves existing system templates and the explicit
System / Misc refresh action upgrades generated copies without manual file
deletion from system/Authoring.
"""

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from validation.core.base_scenario import BaseScenario


class SystemTemplateSeedRefreshScenario(BaseScenario):
    """Validate packaged system templates refresh through the explicit API."""

    async def test_scenario(self):
        controller = self._get_system_controller()
        system_root = controller._system_root
        target = system_root / "Authoring" / "default.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("STALE GENERATED DEFAULT", encoding="utf-8")
        workflow_target = system_root / "Authoring" / "nightly-session-summarization.md"
        workflow_target.write_text(
            "\n".join(
                [
                    "---",
                    "run_type: workflow",
                    'schedule: "cron: 0 2 * * *"',
                    "enabled: true",
                    "description: stale enabled workflow",
                    "---",
                    "",
                    "STALE WORKFLOW BODY",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        seed = Path("core/authoring/seed_templates/context/default.md")
        expected = seed.read_text(encoding="utf-8")
        workflow_seed = Path(
            "core/authoring/seed_templates/workflows/nightly-session-summarization.md"
        )
        expected_workflow = workflow_seed.read_text(encoding="utf-8").replace(
            "enabled: false", "enabled: true", 1
        )

        await self.start_system()

        settings_response = self.call_api("/api/system/settings")
        self.soft_assert_equal(
            settings_response.status_code,
            200,
            "Settings fetch should succeed before repair",
        )
        settings_raw = yaml.safe_load(settings_response.json()["content"])
        settings_raw["providers"]["openrouter"].pop("provider", None)
        settings_raw["settings"].pop("openrouter_ignored_providers", None)
        settings_raw["settings"].pop("max_concurrent_delegates", None)
        settings_raw["settings"]["default_model"].pop("category", None)
        settings_raw["settings"]["default_model"]["value"] = "haiku"
        settings_raw["settings"]["delegate_tool_calls_limit"] = {
            "value": 32,
            "description": "stale removed setting",
            "category": "Delegation",
            "restart_required": False,
        }
        update_settings_response = self.call_api(
            "/api/system/settings",
            method="PUT",
            data={"content": yaml.safe_dump(settings_raw, sort_keys=False)},
        )
        self.soft_assert_equal(
            update_settings_response.status_code,
            200,
            "Settings update should allow existing OpenRouter provider without routing block",
        )
        from core.settings import (
            get_max_concurrent_delegates,
            get_model_stream_idle_timeout_seconds,
        )

        self.soft_assert_equal(
            get_max_concurrent_delegates(),
            3,
            "Upgraded settings should use the delegate concurrency template fallback before repair",
        )
        self.soft_assert_equal(
            get_model_stream_idle_timeout_seconds(),
            120.0,
            "Upgraded settings should use the semantic model-stream timeout default",
        )
        status_response = self.call_api("/api/status")
        self.soft_assert_equal(
            status_response.status_code,
            200,
            "Status should load after metadata removal",
        )
        status_issues = (
            status_response.json().get("configuration_status", {}).get("issues", [])
        )
        self.soft_assert(
            any(
                issue.get("name") == "settings:missing_metadata"
                for issue in status_issues
            ),
            "Status should warn when existing settings are missing template metadata",
        )

        repair_response = self.call_api("/api/system/settings/repair", method="POST")
        self.soft_assert_equal(
            repair_response.status_code,
            200,
            "Settings repair should complete through the system API",
        )
        repaired_settings = yaml.safe_load(repair_response.json()["content"])
        self.soft_assert_equal(
            repaired_settings["providers"]["openrouter"].get("provider"),
            {"require_parameters": True},
            "Settings repair should restore OpenRouter provider routing defaults",
        )
        self.soft_assert_equal(
            repaired_settings["settings"]["openrouter_ignored_providers"].get("value"),
            ["azure"],
            "Settings repair should restore OpenRouter ignored-provider defaults",
        )
        self.soft_assert_equal(
            repaired_settings["settings"]["default_model"].get("category"),
            "Models",
            "Settings repair should restore missing setting metadata",
        )
        self.soft_assert_equal(
            repaired_settings["settings"]["default_model"].get("value"),
            "haiku",
            "Settings repair should preserve existing setting values while restoring metadata",
        )
        self.soft_assert_equal(
            "delegate_tool_calls_limit" in repaired_settings["settings"],
            False,
            "Settings repair should prune the removed delegate tool-call ceiling",
        )
        self.soft_assert_equal(
            repaired_settings["settings"]["max_concurrent_delegates"]["value"],
            3,
            "Settings repair should add the delegate concurrency control",
        )
        self.soft_assert_equal(
            repaired_settings["settings"]["model_stream_idle_timeout_seconds"]["value"],
            120.0,
            "Settings repair should add the semantic model-stream idle timeout",
        )
        for invalid_persisted_value in (-1, 33, "invalid"):
            persisted = yaml.safe_load(
                self.call_api("/api/system/settings").json()["content"]
            )
            persisted["settings"]["max_concurrent_delegates"][
                "value"
            ] = invalid_persisted_value
            persisted_update = self.call_api(
                "/api/system/settings",
                method="PUT",
                data={"content": yaml.safe_dump(persisted, sort_keys=False)},
            )
            self.soft_assert_equal(
                persisted_update.status_code,
                200,
                "Raw settings should retain backward-compatible mapping validation",
            )
            self.soft_assert_equal(
                get_max_concurrent_delegates(),
                3,
                "Invalid persisted delegate concurrency should fail closed to the template default",
            )
        persisted["settings"]["max_concurrent_delegates"]["value"] = 3
        self.call_api(
            "/api/system/settings",
            method="PUT",
            data={"content": yaml.safe_dump(persisted, sort_keys=False)},
        )
        unlimited_update = self.call_api(
            "/api/system/settings/general/max_concurrent_delegates",
            method="PUT",
            data={"value": "0"},
        )
        self.soft_assert_equal(
            unlimited_update.status_code,
            200,
            "Delegate concurrency should accept zero as unlimited",
        )
        self.soft_assert_equal(
            get_max_concurrent_delegates(),
            0,
            "Delegate concurrency getter should preserve unlimited mode",
        )
        invalid_concurrency = self.call_api(
            "/api/system/settings/general/max_concurrent_delegates",
            method="PUT",
            data={"value": "-1"},
        )
        self.soft_assert_equal(
            invalid_concurrency.status_code,
            400,
            "Delegate concurrency should reject negative values",
        )
        disabled_idle_timeout = self.call_api(
            "/api/system/settings/general/model_stream_idle_timeout_seconds",
            method="PUT",
            data={"value": "0"},
        )
        self.soft_assert_equal(
            disabled_idle_timeout.status_code,
            200,
            "Model-stream idle timeout should accept zero as disabled",
        )
        self.soft_assert_equal(
            get_model_stream_idle_timeout_seconds(),
            0.0,
            "Model-stream idle timeout getter should preserve disabled mode",
        )
        invalid_idle_timeout = self.call_api(
            "/api/system/settings/general/model_stream_idle_timeout_seconds",
            method="PUT",
            data={"value": "3601"},
        )
        self.soft_assert_equal(
            invalid_idle_timeout.status_code,
            400,
            "Model-stream idle timeout should reject values above one hour",
        )

        self.soft_assert_equal(
            target.read_text(encoding="utf-8"),
            "STALE GENERATED DEFAULT",
            "Startup should preserve existing system authoring files",
        )
        self.soft_assert(
            "STALE WORKFLOW BODY" in workflow_target.read_text(encoding="utf-8"),
            "Startup should preserve existing system workflow templates",
        )

        response = self.call_api("/api/system/authoring/seed-refresh", method="POST")
        self.soft_assert_equal(
            response.status_code,
            200,
            "Manual refresh should complete through the system API",
        )
        payload = response.json()
        self.soft_assert(
            target.as_posix() in payload.get("updated", []),
            "Manual refresh should report the stale default template as updated",
        )
        self.soft_assert(
            workflow_target.as_posix() in payload.get("updated", []),
            "Manual refresh should report the stale system workflow template as updated",
        )
        self.soft_assert_equal(
            target.read_text(encoding="utf-8"),
            expected,
            "Manual refresh should update packaged system authoring seed files",
        )
        self.soft_assert_equal(
            workflow_target.read_text(encoding="utf-8"),
            expected_workflow,
            "Manual refresh should update system workflow content while preserving enabled state",
        )

        await self.stop_system()
        self.teardown_scenario()
        self.assert_no_failures()
