"""Durable aliases across model configuration, lookup, and settings persistence."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from validation.core.base_scenario import BaseScenario


class ModelAliasResolutionScenario(BaseScenario):
    async def test_scenario(self) -> None:
        await self.start_system()
        try:
            from core.llm.model_utils import (
                get_model_capabilities,
                resolve_model,
            )

            payload = {
                "provider": "openai",
                "model_string": "validation-model",
                "capabilities": ["text", "vision"],
                "alternate_aliases": ["historical-model"],
                "display_name": "Validation Model",
            }
            created = self.call_api(
                "/api/system/models/current-model", method="PUT", data=payload
            )
            assert created.status_code == 200, created.text
            assert created.json()["alternate_aliases"] == ["historical-model"]
            assert resolve_model(" Historical-Model ") == resolve_model("current-model")
            assert get_model_capabilities("historical-model") == {"text", "vision"}
            listed = self.call_api("/api/system/models").json()
            assert not any(model["name"] == "historical-model" for model in listed)
            before = self.call_api("/api/system/settings").json()["content"]
            collision = self.call_api(
                "/api/system/models/another-model", method="PUT", data=payload
            )
            assert collision.status_code == 400, collision.text
            assert self.call_api("/api/system/settings").json()["content"] == before
            sonnet = next(model for model in listed if model["name"] == "sonnet")
            assert sonnet["user_editable"] is False
            managed_payload = {
                "provider": sonnet["provider"],
                "model_string": sonnet["model_string"],
                "capabilities": sonnet["capabilities"],
                "alternate_aliases": ["my-sonnet"],
            }
            edited = self.call_api(
                "/api/system/models/sonnet", method="PUT", data=managed_payload
            )
            assert edited.status_code == 200, edited.text
            assert edited.json()["alternate_aliases"] == ["my-sonnet"]
            blocked = self.call_api(
                "/api/system/models/sonnet",
                method="PUT",
                data={**managed_payload, "model_string": "custom-binding"},
            )
            assert blocked.status_code == 400, blocked.text
            renamed = self.call_api(
                "/api/system/models/renamed-model",
                method="PUT",
                data={**payload, "original_name": "current-model"},
            )
            assert renamed.status_code == 200, renamed.text
            assert set(renamed.json()["alternate_aliases"]) == {
                "historical-model",
                "current-model",
            }
            assert resolve_model("current-model") == resolve_model("renamed-model")
            assert not any(
                model["name"] == "current-model"
                for model in self.call_api("/api/system/models").json()
            )
            default = self.call_api(
                "/api/system/settings/general/default_model",
                method="PUT",
                data={"value": "my-sonnet"},
            )
            assert default.status_code == 200, default.text
            assert default.json()["issues"] == []
            assert default.json()["edit_value"] == "sonnet"
            assert "my-sonnet" not in [
                choice["value"] for choice in default.json()["editor"]["choices"]
            ]
            removed = self.call_api(
                "/api/system/models/sonnet",
                method="PUT",
                data={**managed_payload, "alternate_aliases": []},
            )
            assert removed.status_code == 200, removed.text
            settings = self.call_api("/api/system/settings/general").json()
            assert next(item for item in settings if item["key"] == "default_model")[
                "issues"
            ]
            # Duplication uses ordinary custom creation, which cannot copy managed identity.
            custom_payload = {
                "provider": sonnet["provider"],
                "model_string": "user-model-id",
                "capabilities": sonnet["capabilities"],
                "alternate_aliases": [],
            }
            copied = self.call_api(
                "/api/system/models/sonnet-custom", method="PUT", data=custom_payload
            )
            assert copied.status_code == 200, copied.text
            assert copied.json()["managed_id"] is None
            assert copied.json()["user_editable"] is True
            repaired = self.call_api("/api/system/settings/repair", method="POST")
            assert repaired.status_code == 200, repaired.text
            custom = next(
                model
                for model in self.call_api("/api/system/models").json()
                if model["name"] == "sonnet-custom"
            )
            assert custom["model_string"] == "user-model-id"
            # Advanced edits share the collision boundary and cannot partially write.
            import yaml

            before = self.call_api("/api/system/settings").json()["content"]
            raw = yaml.safe_load(before)
            raw["models"]["sonnet-custom"]["alternate_aliases"] = ["sonnet"]
            collision = self.call_api(
                "/api/system/settings",
                method="PUT",
                data={"content": yaml.safe_dump(raw)},
            )
            assert collision.status_code == 400, collision.text
            assert self.call_api("/api/system/settings").json()["content"] == before
            # On-disk invalid names remain diagnosable and never choose a winner.
            from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
            from core.runtime.reload_service import reload_configuration
            from core.settings.model_registry import InvalidModelAlias

            path = self._get_system_controller()._system_root / "settings.yaml"
            path.write_text(yaml.safe_dump(raw))
            try:
                with use_execution_authority(LOCAL_USER_AUTHORITY):
                    reload_configuration()
                models = self.call_api("/api/system/models")
                assert models.status_code == 200, models.text
                assert all(
                    not model["available"]
                    for model in models.json()
                    if model["name"] in {"sonnet", "sonnet-custom"}
                )
                try:
                    resolve_model("sonnet")
                except InvalidModelAlias:
                    pass
                else:
                    raise AssertionError("Ambiguous aliases must not resolve")
            finally:
                path.write_text(before)
                with use_execution_authority(LOCAL_USER_AUTHORITY):
                    reload_configuration()
        finally:
            await self.stop_system()
