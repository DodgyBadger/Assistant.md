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
            }
            created = self.call_api(
                "/api/system/models/current-model", method="PUT", data=payload
            )
            assert created.status_code == 200, created.text
            assert created.json()["alternate_aliases"] == ["historical-model"]
            assert "display_name" not in created.json()
            assert resolve_model(" Historical-Model ") == resolve_model("current-model")
            assert get_model_capabilities("historical-model") == {"text", "vision"}
            listed = self.call_api("/api/system/models").json()
            assert not any(model["name"] == "historical-model" for model in listed)
            for primary, alternate in (
                ("sol", "gpt"),
                ("terra", "gpt-mini"),
                ("luna", "gpt-nano"),
            ):
                assert resolve_model(alternate) == resolve_model(primary)
                assert any(model["name"] == primary for model in listed)
                assert not any(model["name"] == alternate for model in listed)
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
            general = self.call_api("/api/system/settings/general").json()
            choices = next(item for item in general if item["key"] == "default_model")[
                "editor"
            ]["choices"]
            assert {
                choice["value"]: choice["label"]
                for choice in choices
                if choice["value"] in {"sonnet", "sonnet-custom"}
            } == {"sonnet": "sonnet", "sonnet-custom": "sonnet-custom"}
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
                assert all(
                    not model["chat_selectable"]
                    for model in models.json()
                    if model["name"] in {"sonnet", "sonnet-custom"}
                )
                metadata = self.call_api("/api/metadata")
                assert metadata.status_code == 200, metadata.text
                assert all(
                    not model["chat_selectable"]
                    for model in metadata.json()["models"]
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

            retired = yaml.safe_load(path.read_text())
            retired["models"]["jev"] = {
                "managed_id": "builtin-jev",
                "provider": "typesafe",
                "model_string": "jev-latest",
                "capabilities": ["decision"],
                "user_editable": False,
            }
            path.write_text(yaml.safe_dump(retired))
            with use_execution_authority(LOCAL_USER_AUTHORITY):
                reload_configuration()
            assert "jev" not in yaml.safe_load(path.read_text())["models"]
            assert any(
                "jev" in event.get("data", {}).get("removed", [])
                and event.get("data", {}).get("event") == "managed_models_updated"
                for event in (
                    json.loads(line)
                    for line in (path.parent / "activity.log").read_text().splitlines()
                )
            )

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
            # Standard family aliases become managed even after older installations
            # persisted them as custom; conflicting bindings remain recoverable.
            original = path.read_text()
            try:
                legacy = yaml.safe_load(original)
                legacy["models"]["sonnet"].update(
                    managed_id=None,
                    model_string="claude-sonnet-4-6",
                    capabilities=["text"],
                    user_editable=True,
                )
                path.write_text(yaml.safe_dump(legacy))
                with use_execution_authority(LOCAL_USER_AUTHORITY):
                    reload_configuration()
                adopted = yaml.safe_load(path.read_text())["models"]["sonnet"]
                assert adopted["managed_id"] == "builtin-sonnet"
                assert adopted["model_string"] == "claude-sonnet-5-5"
                assert adopted["user_editable"] is False

                legacy["models"]["sonnet"].update(
                    provider="openai", model_string="gpt-6-astra"
                )
                conflicting = yaml.safe_dump(legacy)
                path.write_text(conflicting)
                with use_execution_authority(LOCAL_USER_AUTHORITY):
                    result = reload_configuration()
                assert path.read_text() == conflicting
                assert any(
                    issue.name == "models:catalog_update"
                    and issue.severity == "warning"
                    and "sonnet" in issue.message
                    for issue in result.status.issues
                )
                restored = self.call_api(
                    "/api/system/models/sonnet",
                    method="PUT",
                    data={
                        "provider": "anthropic",
                        "model_string": "claude-sonnet-4-6",
                        "capabilities": ["text"],
                    },
                )
                assert restored.status_code == 200, restored.text
                assert restored.json()["managed_id"] == "builtin-sonnet"
                assert restored.json()["model_string"] == "claude-sonnet-5-5"
                assert restored.json()["user_editable"] is False
            finally:
                path.write_text(original)
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
