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
            # Display labels may repeat, but picker options must remain distinguishable.
            self.call_api(
                "/api/system/models/sonnet",
                method="PUT",
                data={
                    **managed_payload,
                    "alternate_aliases": [],
                    "display_name": "Shared label",
                },
            )
            self.call_api(
                "/api/system/models/sonnet-custom",
                method="PUT",
                data={**custom_payload, "display_name": "Shared label"},
            )
            general = self.call_api("/api/system/settings/general").json()
            choices = next(item for item in general if item["key"] == "default_model")[
                "editor"
            ]["choices"]
            assert (
                len(
                    {
                        choice["label"]
                        for choice in choices
                        if choice["value"] in {"sonnet", "sonnet-custom"}
                    }
                )
                == 2
            )
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
            # Catalog changes run against an isolated template and produce actual
            # System Activity records without dispatching any provider request.
            import json
            from unittest.mock import patch

            from core.settings import store

            template = yaml.safe_load(store.SETTINGS_TEMPLATE.read_text())
            template["models"]["managed-next"] = template["models"].pop("sonnet")
            template["models"]["managed-next"]["model_string"] = "catalog-v2"
            fixture = path.parent / "catalog-fixture.yaml"
            fixture.write_text(yaml.safe_dump(template))
            try:
                with patch.object(store, "SETTINGS_TEMPLATE", fixture):
                    with use_execution_authority(LOCAL_USER_AUTHORITY):
                        reload_configuration()
                    assert resolve_model("sonnet") == ("anthropic", "catalog-v2")
                    saved = path.read_text()
                    assert yaml.safe_load(saved)["models"]["managed-next"][
                        "alternate_aliases"
                    ] == ["sonnet"]
                    template["models"]["sonnet-custom"] = template["models"].pop(
                        "managed-next"
                    )
                    fixture.write_text(yaml.safe_dump(template))
                    with use_execution_authority(LOCAL_USER_AUTHORITY):
                        result = reload_configuration()
                    assert path.read_text() == saved
                    assert any(
                        issue.name == "models:catalog_update"
                        for issue in result.status.issues
                    )
                activity = [
                    json.loads(line)
                    for line in (path.parent / "activity.log").read_text().splitlines()
                ]
                updates = [
                    event["data"]
                    for event in activity
                    if event.get("data", {}).get("event") == "managed_models_updated"
                ]
                assert any(
                    ["sonnet", "managed-next"] in event["renames"] for event in updates
                )
                blocked_events = [
                    event["data"]
                    for event in activity
                    if event.get("data", {}).get("event")
                    == "managed_models_update_blocked"
                ]
                assert len(blocked_events) == 2, blocked_events
                assert len({event["issue"] for event in blocked_events}) == 2

            finally:
                path.write_text(before)
                with use_execution_authority(LOCAL_USER_AUTHORITY):
                    reload_configuration()
            # The raw editor must repair malformed current model data without loading it first.
            original = path.read_text()
            malformed = yaml.safe_load(original)
            malformed["models"]["sonnet-custom"]["alternate_aliases"] = "not-a-list"
            path.write_text(yaml.safe_dump(malformed))
            store.refresh_settings_cache()
            try:
                repaired = self.call_api(
                    "/api/system/settings", method="PUT", data={"content": original}
                )
                assert repaired.status_code == 200, repaired.text
                assert self.call_api("/api/system/models").status_code == 200
            finally:
                path.write_text(original)
                with use_execution_authority(LOCAL_USER_AUTHORITY):
                    reload_configuration()
        finally:
            await self.stop_system()
