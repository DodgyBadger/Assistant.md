"""Settings editor metadata, typed persistence, and invalid-reference recovery."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from validation.support.base_scenario import BaseScenario


class SettingsEditorsScenario(BaseScenario):
    async def test_scenario(self) -> None:
        vault = self.create_vault("SettingsEditorsVault")
        custom_context = vault / "AssistantMD" / "Authoring" / "custom-context.md"
        custom_context.parent.mkdir(parents=True, exist_ok=True)
        custom_context.write_text(
            "---\nrun_type: context\n---\nCustom context\n", encoding="utf-8"
        )
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

            def update(key, value, status=200):
                response = self.call_api(
                    f"/api/system/settings/general/{key}",
                    method="PUT",
                    data={"value": value},
                )
                assert response.status_code == status, response.text
                return response

            baseline = settings()
            assert baseline["debug"]["editor"]["kind"] == "boolean"
            assert baseline["max_concurrent_delegates"]["editor"]["maximum"] == 32
            assert baseline["default_model"]["editor"]["kind"] == "select"
            assert "sonnet" in [
                choice["value"]
                for choice in baseline["default_model"]["editor"]["choices"]
            ]
            assert baseline["disabled_tools"]["editor"]["kind"] == "multiselect"
            assert (
                baseline["ingestion_pdf_default_strategies"]["editor"]["kind"]
                == "ordered_list"
            )
            assert (
                baseline["vault_state_excluded_patterns"]["editor"]["kind"]
                == "string_list"
            )
            assert all(not entry["issues"] for entry in baseline.values()), baseline

            update("default_context_script", "")
            assert not settings()["default_context_script"]["issues"]
            update("default_context_script", "custom-context.md")
            assert not settings()["default_context_script"]["issues"]
            custom_context.unlink()
            assert settings()["default_context_script"]["issues"]
            update("default_context_script", "default.md")
            restart = update("debug", "false").json()
            assert restart["restart_required"] is True
            assert restart["editor"]["kind"] == "boolean"

            # The low watermark is used only by session-map compaction.
            update("compaction_high_watermark_tokens", "1")
            update("compaction_strategy", "session_map", 400)
            update("compaction_high_watermark_tokens", "80000")
            update("compaction_strategy", "session_map")

            for key, raw in [
                ("chat_tool_calls_limit", "1.5"),
                ("chat_tool_calls_limit", "-1"),
                ("model_stream_retry_base_delay_seconds", "NaN"),
                ("model_stream_retry_max_delay_seconds", "Infinity"),
                ("model_stream_retries", "6"),
                ("default_chat_mode", "typo"),
                ("default_model", "removed-model"),
                ("default_model", "jev"),
                ("compaction_author_model", "jev"),
                ("disabled_tools", '["does-not-exist"]'),
                ("disabled_tools", '["file_read","file_read"]'),
                ("vault_state_excluded_patterns", "[42]"),
                ("ingestion_pdf_default_strategies", "[]"),
                ("ingestion_pdf_default_strategies", '["typo"]'),
                ("compaction_low_watermark_tokens", "90000"),
            ]:
                before = self.configuration_request().json()["configuration"]
                update(key, raw, 400)
                assert self.configuration_request().json()["configuration"] == before

            update("persist_model_reasoning_parts", "true")
            update("model_stream_retry_base_delay_seconds", "0.5")
            update("disabled_tools", '["file_read"]')
            update("disabled_tools", "[]")
            update("ingestion_pdf_default_strategies", '["pdf_text","pdf_ocr"]')
            update("compaction_author_model", "none")
            persisted = self.configuration_request().json()["configuration"]
            assert (
                persisted["settings"]["persist_model_reasoning_parts"]["value"] is True
            )
            assert (
                persisted["settings"]["model_stream_retry_base_delay_seconds"]["value"]
                == 0.5
            )
            assert persisted["settings"]["ingestion_pdf_default_strategies"][
                "value"
            ] == ["pdf_text", "pdf_ocr"]

            # Advanced YAML editing intentionally permits invalid values. The structured
            # editor must report them and permit repair without losing unrelated state.
            persisted["settings"]["default_model"]["value"] = "retired-alias"
            persisted["settings"]["max_concurrent_delegates"]["value"] = "broken"
            persisted["settings"]["default_chat_mode"]["value"] = "typo"
            persisted["settings"]["vault_state_excluded_patterns"]["value"] = [42]
            response = self.configuration_request(
                method="PUT", data={"configuration": persisted}
            )
            assert response.status_code == 200, response.text
            invalid = settings()
            for key in (
                "default_model",
                "max_concurrent_delegates",
                "default_chat_mode",
                "vault_state_excluded_patterns",
            ):
                assert invalid[key]["issues"], key
            assert not invalid["compaction_author_model"]["issues"]
            update("chat_tool_calls_limit", "2")
            assert settings()["default_model"]["value"] == "retired-alias"
            update("max_concurrent_delegates", "4")
            assert not settings()["max_concurrent_delegates"]["issues"]
            update("default_model", "sonnet")
            update("default_chat_mode", "normal")
            update("vault_state_excluded_patterns", json.dumps(["**/custom/**"]))
            assert all(not entry["issues"] for entry in settings().values())

            # Both participants report relationship violations; unrelated settings
            # remain editable and incremental repairs cannot worsen the violation.
            persisted = self.configuration_request().json()["configuration"]
            persisted["settings"]["compaction_low_watermark_tokens"]["value"] = 100000
            response = self.configuration_request(
                method="PUT", data={"configuration": persisted}
            )
            assert response.status_code == 200, response.text
            invalid = settings()
            for key in (
                "compaction_low_watermark_tokens",
                "compaction_high_watermark_tokens",
            ):
                assert any(
                    issue["code"] == "relationship" for issue in invalid[key]["issues"]
                )
            update("chat_tool_calls_limit", "3")
            update("compaction_low_watermark_tokens", "90000")
            update("compaction_low_watermark_tokens", "110000", 400)
            update("compaction_high_watermark_tokens", "120000")
            update("compaction_low_watermark_tokens", "20000")
            update("compaction_high_watermark_tokens", "80000")

            # Choices and validity follow the live registry, including an alias
            # removed after a client obtained the choices and later restored.
            persisted = self.configuration_request().json()["configuration"]
            persisted["models"]["sonnet"]["model_string"] = "upgraded-model-id"
            response = self.configuration_request(
                method="PUT", data={"configuration": persisted}
            )
            assert response.status_code == 200, response.text
            assert not settings()["default_model"]["issues"]
            saved_model = persisted["models"].pop("sonnet")
            response = self.configuration_request(
                method="PUT", data={"configuration": persisted}
            )
            assert response.status_code == 200, response.text
            assert settings()["default_model"]["issues"]
            update("default_model", "sonnet", 400)
            persisted["models"]["sonnet"] = saved_model
            response = self.configuration_request(
                method="PUT", data={"configuration": persisted}
            )
            assert response.status_code == 200, response.text
            assert not settings()["default_model"]["issues"]
            persisted["models"] = {}
            response = self.configuration_request(
                method="PUT", data={"configuration": persisted}
            )
            assert response.status_code == 200, response.text
            empty_models = settings()
            assert empty_models["default_model"]["issues"]
            assert [
                choice["value"]
                for choice in empty_models["default_model"]["editor"]["choices"]
            ] == ["none"]
            assert not empty_models["compaction_author_model"]["issues"]
            # Packaged tools remain registered before an older persisted file is repaired.
            persisted["tools"].pop("file_read")
            response = self.configuration_request(
                method="PUT", data={"configuration": persisted}
            )
            assert response.status_code == 200, response.text
            tool_choices = settings()["disabled_tools"]["editor"]["choices"]
            assert "file_read" in [choice["value"] for choice in tool_choices]
            update("disabled_tools", '["file_read"]')
            assert not settings()["disabled_tools"]["issues"]
        finally:
            await self.stop_system()
            self.teardown_scenario()
