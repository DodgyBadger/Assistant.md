"""Validate reusable decision-model configuration and readiness boundaries."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority  # noqa: E402
from core.settings.store import ModelConfig  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class DecisionModelConfigurationScenario(BaseScenario):
    """Prove decision-only aliases are configurable but excluded from chat."""

    async def test_scenario(self) -> None:
        decision_only = ModelConfig(
            provider="typesafe",
            model_string="jev-latest",
            capabilities=[" Decision ", "decision"],
        )
        self.soft_assert_equal(
            decision_only.capabilities,
            ["decision"],
            "Decision-only capability normalization must not add text",
        )
        self.soft_assert_equal(
            ModelConfig(
                provider="example",
                model_string="vision-model",
                capabilities=["vision"],
            ).capabilities,
            ["text", "vision"],
            "Existing generative capability normalization should remain compatible",
        )

        await self.start_system()
        general_settings = self.call_api("/api/system/settings/general")
        session_memory_mode = next(
            setting
            for setting in general_settings.json()
            if setting["key"] == "live_session_memory_mode"
        )
        self.soft_assert_equal(
            session_memory_mode["value"],
            "off",
            "The session-memory mode default must remain a string enum",
        )
        active_settings_response = self.call_api("/api/system/settings")
        active_settings = yaml.safe_load(active_settings_response.json()["content"])
        active_settings["settings"]["live_session_memory_mode"]["value"] = False
        legacy_boolean = self.call_api(
            "/api/system/settings",
            method="PUT",
            data={"content": yaml.safe_dump(active_settings, sort_keys=False)},
        )
        self.soft_assert_equal(
            legacy_boolean.status_code,
            200,
            "A legacy boolean session-memory mode should remain loadable",
        )
        observe_mode = self.call_api(
            "/api/system/settings/general/live_session_memory_mode",
            method="PUT",
            data={"value": "observe"},
        )
        self.soft_assert_equal(
            (observe_mode.status_code, observe_mode.json().get("value")),
            (200, "observe"),
            "The settings API should repair a legacy boolean mode while enabling observe",
        )
        off_mode = self.call_api(
            "/api/system/settings/general/live_session_memory_mode",
            method="PUT",
            data={"value": "off"},
        )
        self.soft_assert_equal(
            (off_mode.status_code, off_mode.json().get("value")),
            (200, "off"),
            "The settings API should persist the disabled enum value as a string",
        )
        from core.llm.model_factory import build_model_instance
        from core.llm.model_utils import (
            get_model_capabilities,
            model_supports_capability,
            refresh_model_cache,
            validate_api_keys,
        )

        refresh_model_cache()

        models_response = self.call_api("/api/system/models")
        self.soft_assert_equal(
            models_response.status_code,
            200,
            "Model configuration should remain readable",
        )
        jev = next(model for model in models_response.json() if model["name"] == "jev")
        self.soft_assert_equal(
            (
                jev["provider"],
                jev["model_string"],
                jev["capabilities"],
                jev["user_editable"],
                jev["available"],
                jev["chat_selectable"],
            ),
            ("typesafe", "jev-latest", ["decision"], False, False, False),
            "Built-in Jev metadata should be decision-only and unavailable without its secret",
        )
        self.soft_assert_equal(
            get_model_capabilities("jev"),
            {"decision"},
            "Runtime capability lookup should preserve the decision-only alias",
        )
        self.soft_assert(
            model_supports_capability("jev", "decision"),
            "Jev should advertise the reusable decision capability",
        )
        self.soft_assert(
            not model_supports_capability("jev", "text"),
            "Jev must not be treated as a generative text model",
        )

        providers_response = self.call_api("/api/system/providers")
        typesafe = next(
            provider
            for provider in providers_response.json()
            if provider["name"] == "typesafe"
        )
        self.soft_assert_equal(
            (
                typesafe["api_key"],
                typesafe["base_url"],
                typesafe["api_key_has_value"],
                typesafe["user_editable"],
            ),
            ("TYPESAFE_API_KEY", None, False, False),
            "TypeSafe should use the existing secret-pointer readiness contract without a base URL",
        )

        known_secrets = self.call_api("/api/system/secrets")
        self.soft_assert(
            any(
                entry["name"] == "TYPESAFE_API_KEY" and not entry["has_value"]
                for entry in known_secrets.json()
            ),
            "The configured TypeSafe pointer should appear in the principal-owned secrets UI",
        )

        secret_value = "validation-typesafe-secret-value"
        secret_update = self.call_api(
            "/api/system/secrets",
            method="PUT",
            data={"name": "TYPESAFE_API_KEY", "value": secret_value},
        )
        self.soft_assert_equal(
            secret_update.status_code,
            200,
            "The existing secrets API should accept the TypeSafe credential",
        )
        ready_models = self.call_api("/api/system/models")
        ready_jev = next(
            model for model in ready_models.json() if model["name"] == "jev"
        )
        self.soft_assert(
            ready_jev["available"],
            "Jev readiness should follow its populated secret pointer",
        )
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            validate_api_keys("jev")
        ready_providers = self.call_api("/api/system/providers")
        ready_typesafe = next(
            provider
            for provider in ready_providers.json()
            if provider["name"] == "typesafe"
        )
        self.soft_assert(
            ready_typesafe["api_key_has_value"],
            "TypeSafe provider status should expose credential presence only",
        )
        serialized_status = (
            str(ready_models.json())
            + str(ready_providers.json())
            + str(self.call_api("/api/system/secrets").json())
        )
        self.soft_assert(
            secret_value not in serialized_status,
            "Configuration and readiness responses must not expose the credential value",
        )
        active_settings = self.call_api("/api/system/settings").json()["content"]
        activity_log = (
            self._get_system_controller()._system_root / "activity.log"
        ).read_text(encoding="utf-8")
        self.soft_assert(
            secret_value not in active_settings and secret_value not in activity_log,
            "The credential value must not enter settings or activity logs",
        )

        generative_rejected = False
        try:
            with use_execution_authority(LOCAL_USER_AUTHORITY):
                build_model_instance("jev")
        except ValueError as exc:
            generative_rejected = "does not declare the 'text' capability" in str(exc)
        self.soft_assert(
            generative_rejected,
            "Generative model construction must reject a decision-only alias before provider dispatch",
        )

        custom_alias = self.call_api(
            "/api/system/models/validation-decision",
            method="PUT",
            data={
                "provider": "typesafe",
                "model_string": "jev-latest",
                "capabilities": ["decision"],
                "description": "Validation-only decision alias",
            },
        )
        self.soft_assert_equal(
            custom_alias.json()["capabilities"],
            ["decision"],
            "Configuration API round-trips decision-only aliases without adding text",
        )
        self.call_api(
            "/api/system/models/validation-decision",
            method="DELETE",
        )

        settings_response = self.call_api("/api/system/settings")
        settings = yaml.safe_load(settings_response.json()["content"])
        settings["models"].pop("jev")
        settings["providers"].pop("typesafe")
        removed_builtins = self.call_api(
            "/api/system/settings",
            method="PUT",
            data={"content": yaml.safe_dump(settings, sort_keys=False)},
        )
        self.soft_assert_equal(
            removed_builtins.status_code,
            200,
            "An older settings file without decision configuration should remain readable",
        )
        repair = self.call_api("/api/system/settings/repair", method="POST")
        repaired = yaml.safe_load(repair.json()["content"])
        self.soft_assert_equal(
            repaired["models"]["jev"]["capabilities"],
            ["decision"],
            "Settings repair should add the built-in Jev alias",
        )
        self.soft_assert_equal(
            repaired["providers"]["typesafe"]["api_key"],
            "TYPESAFE_API_KEY",
            "Settings repair should add the TypeSafe secret pointer",
        )

        clear_secret = self.call_api(
            "/api/system/secrets",
            method="PUT",
            data={"name": "TYPESAFE_API_KEY", "value": ""},
        )
        self.soft_assert_equal(
            clear_secret.status_code,
            200,
            "The TypeSafe credential should clear through the existing secrets API",
        )
        cleared_jev = next(
            model
            for model in self.call_api("/api/system/models").json()
            if model["name"] == "jev"
        )
        self.soft_assert(
            not cleared_jev["available"],
            "Clearing the TypeSafe secret should invalidate Jev readiness after reload",
        )
