"""Effective defaults, reset ownership boundaries, and user-visible outcomes."""

from __future__ import annotations

import json
import sqlite3
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.settings.store import get_default_settings
from validation.core.base_scenario import BaseScenario


class SettingsLifecycleScenario(BaseScenario):
    async def test_scenario(self) -> None:
        self.create_vault("SettingsLifecycleVault")
        await self.start_system()
        try:

            def settings():
                response = self.call_api("/api/system/settings/general")
                assert response.status_code == 200, response.text
                return {entry["key"]: entry for entry in response.json()}

            before = settings()["default_api_timeout"]
            saved = self.call_api(
                "/api/system/settings/general/default_api_timeout",
                method="PUT",
                data={"value": before["value"]},
            )
            assert saved.status_code == 200, saved.text
            assert saved.json()["source"] == "custom"
            reset = self.call_api(
                "/api/system/settings/general/default_api_timeout/override",
                method="DELETE",
            )
            assert reset.status_code == 200, reset.text
            assert reset.json()["source"] == "default"
            assert reset.json()["value"] == reset.json()["default_value"]
            assert (
                "default_api_timeout" not in self.configuration_snapshot()["settings"]
            )
            # Resetting one side of a numeric relationship must not make it invalid.
            high = self.call_api(
                "/api/system/settings/general/compaction_high_watermark_tokens",
                method="PUT",
                data={"value": "1"},
            )
            assert high.status_code == 200, high.text
            strategy = self.call_api(
                "/api/system/settings/general/compaction_strategy",
                method="PUT",
                data={"value": "session_map"},
            )
            assert strategy.status_code == 400
            self.call_api(
                "/api/system/settings/general/compaction_high_watermark_tokens/override",
                method="DELETE",
            )
            custom = self.call_api(
                "/api/system/models/my-lifecycle-model",
                method="PUT",
                data={
                    "provider": "test",
                    "model_string": "test",
                    "alternate_aliases": ["historical-lifecycle-model"],
                },
            )
            assert custom.status_code == 200, custom.text
            secret = self.call_api(
                "/api/system/secrets",
                method="PUT",
                data={"name": "LIFECYCLE_SECRET", "value": "validation-fixture-only"},
            )
            assert secret.status_code == 200, secret.text
            raw = self.configuration_snapshot()
            raw["models"].pop("haiku")
            raw["models"]["read-only-custom-model"] = {
                **raw["models"]["my-lifecycle-model"],
                "alternate_aliases": [],
                "user_editable": False,
            }
            raw["providers"]["openai"]["base_url"] = "https://custom.example.test"
            raw["providers"]["my-lifecycle-provider"] = {
                "base_url": "https://custom.example.test",
                "user_editable": True,
            }
            raw["tools"]["my-lifecycle-tool"] = {
                **raw["tools"]["file_read"],
                "user_editable": True,
            }
            raw["settings"]["debug"] = {"value": True, "source": "custom"}
            self.install_configuration_fixture(raw)
            restored = self.call_api("/api/system/settings/defaults", method="POST")
            assert restored.status_code == 200, restored.text
            assert restored.json()["restart_required"] is True
            backup = Path(restored.json()["backup_path"])
            assert backup.exists()
            current = self.configuration_snapshot()
            assert current["settings"] == {}
            assert (
                current["models"]["read-only-custom-model"]
                == raw["models"]["read-only-custom-model"]
            )
            assert (
                current["models"]["my-lifecycle-model"]
                == raw["models"]["my-lifecycle-model"]
            )
            assert all(
                current["providers"]["my-lifecycle-provider"][key] == value
                for key, value in raw["providers"]["my-lifecycle-provider"].items()
            )
            assert all(
                current["tools"]["my-lifecycle-tool"][key] == value
                for key, value in raw["tools"]["my-lifecycle-tool"].items()
            )
            assert current["models"]["haiku"]["managed_id"] == "builtin-haiku"
            assert (
                current["providers"]["openai"]["base_url"]
                != "https://custom.example.test"
            )
            assert all(entry["source"] == "default" for entry in settings().values())
            assert set(settings()) == set(get_default_settings())
            assert any(
                entry["name"] == "LIFECYCLE_SECRET" and entry["has_value"]
                for entry in self.call_api("/api/system/secrets").json()
            )
            with sqlite3.connect(backup) as conn:
                assert conn.execute(
                    "SELECT payload FROM configuration_settings WHERE name='debug'"
                ).fetchone()
            conflicting = deepcopy(current)
            conflicting["models"]["haiku"] = {
                "provider": "test",
                "model_string": "test",
                "user_editable": True,
            }
            self.install_configuration_fixture(conflicting)
            denied = self.call_api("/api/system/settings/defaults", method="POST")
            assert denied.status_code == 400, denied.text
            assert self.configuration_snapshot() == conflicting
            self.install_configuration_fixture(current)
            events = [
                json.loads(line)
                for line in (
                    self._get_system_controller()._system_root / "activity.log"
                )
                .read_text()
                .splitlines()
            ]
            assert any(
                event.get("data", {}).get("event") == "general_setting_default_restored"
                for event in events
            )
            lifecycle = [
                event["data"]
                for event in events
                if event.get("data", {})
                .get("event", "")
                .startswith("managed_configuration_defaults_")
            ]
            starts = [event for event in lifecycle if event["status"] == "started"]
            terminals = [
                event
                for event in lifecycle
                if event["status"] in {"completed", "failed"}
            ]
            assert len(starts) == 2 and len(terminals) == 2
            assert {event["operation_id"] for event in starts} == {
                event["operation_id"] for event in terminals
            }
            assert sorted(event["status"] for event in terminals) == [
                "completed",
                "failed",
            ]
            assert all(
                "validation-fixture-only" not in json.dumps(event)
                for event in lifecycle
            )
        finally:
            await self.stop_system()
            self.teardown_scenario()
