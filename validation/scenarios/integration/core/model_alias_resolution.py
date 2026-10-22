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
        finally:
            await self.stop_system()
