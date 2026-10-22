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
        finally:
            await self.stop_system()
