"""Effective defaults, reset ownership boundaries, and user-visible outcomes."""

from __future__ import annotations

import json
import sqlite3
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.settings import config_editor, repository
from core.settings.store import get_default_settings, load_settings
from validation.core.base_scenario import BaseScenario


class SettingsLifecycleScenario(BaseScenario):
    async def test_scenario(self) -> None:
        self.create_vault("SettingsLifecycleVault")
        await self.start_system()
        # This scenario exercises V1 behavior independently of the shipped strategy.
        strategy_response = self.call_api(
            "/api/system/settings/general/compaction_strategy",
            method="PUT",
            data={"value": "recovery_card"},
        )
        assert strategy_response.status_code == 200, strategy_response.text
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
            other = self.call_api(
                "/api/system/settings/general/debug",
                method="PUT",
                data={"value": "true"},
            )
            assert other.status_code == 200, other.text
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
            assert settings()["debug"]["source"] == "custom"
            assert settings()["debug"]["value"] == "true"
            self.call_api(
                "/api/system/settings/general/debug/override", method="DELETE"
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
            assert (
                self.call_api(
                    "/api/system/settings/general/compaction_strategy",
                    method="PUT",
                    data={"value": "session_map"},
                ).status_code
                == 200
            )
            default_high = int(
                settings()["compaction_high_watermark_tokens"]["default_value"]
            )
            for key, value in (
                ("compaction_high_watermark_tokens", default_high + 20000),
                ("compaction_low_watermark_tokens", default_high + 10000),
            ):
                response = self.call_api(
                    f"/api/system/settings/general/{key}",
                    method="PUT",
                    data={"value": str(value)},
                )
                assert response.status_code == 200, response.text
            before_invalid_reset = self.configuration_snapshot()
            rejected = self.call_api(
                "/api/system/settings/general/compaction_high_watermark_tokens/override",
                method="DELETE",
            )
            assert rejected.status_code == 400, rejected.text
            assert self.configuration_snapshot() == before_invalid_reset
            assert (
                int(settings()["compaction_high_watermark_tokens"]["value"])
                == default_high + 20000
            )
            assert (
                int(settings()["compaction_low_watermark_tokens"]["value"])
                == default_high + 10000
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
            authoring = (
                self._get_system_controller()._system_root / "Authoring" / "default.md"
            )
            authoring_content = (
                authoring.read_text() + "\n<!-- local customization -->\n"
            )
            authoring.write_text(authoring_content)
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
            assert authoring.read_text() == authoring_content
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
                first_revision = conn.execute(
                    "SELECT revision FROM configuration_state"
                ).fetchone()[0]
            second_reset = self.call_api("/api/system/settings/defaults", method="POST")
            assert second_reset.status_code == 200, second_reset.text
            second_backup = Path(second_reset.json()["backup_path"])
            assert second_backup != backup and second_backup.exists()
            with sqlite3.connect(second_backup) as conn:
                assert (
                    conn.execute("SELECT revision FROM configuration_state").fetchone()[
                        0
                    ]
                    == first_revision + 1
                )
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
            assert len(starts) == 3 and len(terminals) == 3
            assert {event["operation_id"] for event in starts} == {
                event["operation_id"] for event in terminals
            }
            assert sorted(event["status"] for event in terminals) == [
                "completed",
                "completed",
                "failed",
            ]
            assert all(
                "validation-fixture-only" not in json.dumps(event)
                for event in lifecycle
            )

            # This proves domain concurrency admission, not the API service's
            # internal backup-to-reset scheduling or error translation.
            root = self._get_system_controller()._system_root
            earlier = self.call_api(
                "/api/system/settings/general/default_api_timeout",
                method="PUT",
                data={"value": "11"},
            )
            assert earlier.status_code == 200, earlier.text
            backup_path = root / "lifecycle-concurrency-backup.db"
            backed_up_revision = repository.backup(root, backup_path)
            assert backed_up_revision is not None
            with sqlite3.connect(backup_path) as conn:
                assert repository.revision(conn) == backed_up_revision
                assert (
                    repository.read_records(conn)["settings"]["default_api_timeout"][
                        "value"
                    ]
                    == 11
                )

            later = self.call_api(
                "/api/system/settings/general/default_api_timeout",
                method="PUT",
                data={"value": "12"},
            )
            assert later.status_code == 200, later.text
            edited = self.configuration_snapshot()
            edited_revision = load_settings()._revision
            assert edited_revision > backed_up_revision
            try:
                config_editor.reset_managed_configuration(
                    expected_revision=backed_up_revision
                )
            except repository.StaleSettingsError:
                pass
            else:
                raise AssertionError("Reset must reject an edit newer than its backup")
            assert self.configuration_snapshot() == edited
            assert settings()["default_api_timeout"]["value"] == "12"
            assert load_settings()._revision == edited_revision
            with sqlite3.connect(backup_path) as conn:
                assert repository.revision(conn) == backed_up_revision
                assert (
                    repository.read_records(conn)["settings"]["default_api_timeout"][
                        "value"
                    ]
                    == 11
                )

            matching_backup = root / "lifecycle-matching-backup.db"
            matching_revision = repository.backup(root, matching_backup)
            assert matching_revision == edited_revision
            accepted = config_editor.reset_managed_configuration(
                expected_revision=matching_revision
            )
            assert accepted.settings["default_api_timeout"].source == "default"
            assert load_settings()._revision == matching_revision + 1
        finally:
            await self.stop_system()
            self.teardown_scenario()
