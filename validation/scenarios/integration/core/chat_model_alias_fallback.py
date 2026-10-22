"""Chat aliases fall back only on missing names and never on provider failures."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from validation.core.base_scenario import BaseScenario


class ChatModelAliasFallbackScenario(BaseScenario):
    async def test_scenario(self) -> None:
        vault = self.create_vault("AliasChatVault")
        await self.start_system()
        try:
            from core.chat.model_selection import resolve_chat_model_alias
            from core.llm.model_factory import build_model_instance
            from core.settings.model_registry import UnknownModelAlias

            edited = self.call_api(
                "/api/system/models/test",
                method="PUT",
                data={
                    "provider": "test",
                    "model_string": "test",
                    "alternate_aliases": ["historical-test"],
                },
            )
            assert edited.status_code == 200, edited.text
            updated = self.call_api(
                "/api/system/settings/general/default_model",
                method="PUT",
                data={"value": "historical-test"},
            )
            assert updated.status_code == 200, updated.text
            assert resolve_chat_model_alias("historical-test") == "test"
            assert resolve_chat_model_alias("removed-alias") == "test"
            assert resolve_chat_model_alias("sonnet") == "sonnet"
            try:
                build_model_instance("removed-alias")
            except UnknownModelAlias:
                pass
            else:
                raise AssertionError("Explicit model construction must not fall back")
            response = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "Reply briefly.",
                    "session_id": "alias-fallback-session",
                    "model": "removed-alias",
                }
            )
            assert response["terminal_event"]["event"] == "done", response
            second = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "Continue briefly.",
                    "session_id": "alias-fallback-session",
                    "model": "historical-test",
                }
            )
            assert second["terminal_event"]["event"] == "done", second
            # Resuming inline review is another chat preparation path and uses the same rule.
            from pydantic_ai import DeferredToolResults

            from core.chat import executor as chat_executor
            from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority

            with use_execution_authority(LOCAL_USER_AUTHORITY):
                resumed = await chat_executor._prepare_deferred_review_resume_execution(
                    vault_name=vault.name,
                    vault_path=str(vault),
                    session_id="alias-fallback-session",
                    tools=[],
                    model="removed-alias",
                    message_history=[],
                    deferred_tool_results=DeferredToolResults(),
                )
                try:
                    assert resumed.model == "test"
                finally:
                    await resumed.close()
            # An invalid on-disk alias also cannot resolve; use only the configured default.
            from core.runtime.reload_service import reload_configuration

            path = self._get_system_controller()._system_root / "settings.yaml"
            original = path.read_text()
            ambiguous = yaml.safe_load(original)
            ambiguous["models"]["sonnet"]["alternate_aliases"] = ["ambiguous"]
            ambiguous["models"]["opus"]["alternate_aliases"] = ["ambiguous"]
            path.write_text(yaml.safe_dump(ambiguous))
            try:
                with use_execution_authority(LOCAL_USER_AUTHORITY):
                    reload_configuration()
                assert resolve_chat_model_alias("ambiguous") == "test"
            finally:
                path.write_text(original)
                with use_execution_authority(LOCAL_USER_AUTHORITY):
                    reload_configuration()
            # A configured alias with missing credentials must not use the valid default.
            unavailable = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "Do not dispatch without credentials.",
                    "session_id": "alias-provider-failure",
                    "model": "sonnet",
                }
            )
            assert unavailable["terminal_event"]["event"] == "error", unavailable
            activity = (
                self._get_system_controller()._system_root / "activity.log"
            ).read_text()
            assert any(
                json.loads(line).get("data", {}).get("event") == "chat_model_fallback"
                for line in activity.splitlines()
            )
            raw = yaml.safe_load(
                self.call_api("/api/system/settings").json()["content"]
            )
            raw["settings"]["default_model"]["value"] = "missing-default"
            saved = self.call_api(
                "/api/system/settings",
                method="PUT",
                data={"content": yaml.safe_dump(raw)},
            )
            assert saved.status_code == 200, saved.text
            status = self.call_api("/api/status").json()["configuration_status"]
            assert any(
                issue["name"] == "setting:default_model"
                and issue["severity"] == "error"
                for issue in status["issues"]
            ), status
            try:
                resolve_chat_model_alias("removed-alias")
            except UnknownModelAlias:
                pass
            else:
                raise AssertionError(
                    "An unresolved default must not choose another model"
                )
        finally:
            await self.stop_system()
