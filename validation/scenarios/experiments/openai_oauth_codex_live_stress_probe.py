"""Live experiment for OAuth-backed OpenAI Codex chat stress testing.

This scenario intentionally uses the configured live model and current provider
auth state. Keep it in experiments; it spends real model quota and depends on
the user's local OpenAI OAuth/API configuration.
"""

import json
import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from validation.core.base_scenario import BaseScenario  # noqa: E402


class OpenAIOAuthCodexLiveStressProbeScenario(BaseScenario):
    """Run same-session, tool-heavy chat turns against the configured model."""

    async def test_scenario(self):
        vault = self.create_vault("OpenAIOAuthCodexLiveStressProbeVault")
        _seed_probe_files(self, vault)
        model = _configure_live_openai_settings(self)

        await self.start_system()

        metadata_response = self.call_api("/api/metadata")
        assert (
            metadata_response.status_code == 200
        ), "Metadata endpoint should be available"
        metadata = metadata_response.json()
        model_metadata = _find_model_metadata(metadata, model)
        assert (
            model_metadata and model_metadata.get("available") is True
        ), "Live OAuth stress probe model alias should be present and available"

        try:
            provider_response = self.call_api("/api/system/providers")
            assert (
                provider_response.status_code == 200
            ), "Provider status endpoint should be available"
            openai_status = _find_openai_provider(provider_response.json())
            assert openai_status is not None, "OpenAI provider status should be present"

            session_id = "openai_oauth_codex_live_stress_probe_session"
            first = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": FIRST_TURN_PROMPT,
                    "session_id": session_id,
                    "tools": ["file_read", "file_write"],
                    "model": model,
                    "thinking": "off",
                },
                timeout_seconds=240.0,
            )
            assert (
                first["start_response"].status_code == 200
            ), "First stress chat task should start"
            assert (
                first["terminal_event"]
                and first["terminal_event"].get("event") == "done"
            ), "First stress chat task should complete"

            second = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": SECOND_TURN_PROMPT,
                    "session_id": session_id,
                    "tools": ["file_read", "file_write"],
                    "model": model,
                    "thinking": "off",
                },
                timeout_seconds=240.0,
            )
            assert (
                second["start_response"].status_code == 200
            ), "Second stress chat task should start"
            assert (
                second["terminal_event"]
                and second["terminal_event"].get("event") == "done"
            ), "Second stress chat task should complete"

            detail_response = self.call_api(
                f"/api/chat/sessions/{session_id}?vault_name={vault.name}",
            )
            assert (
                detail_response.status_code == 200
            ), "Session detail should load after stress turns"
            detail = detail_response.json()
            tool_calls = detail.get("tool_calls", [])
            file_read_calls = [
                tool_call
                for tool_call in tool_calls
                if tool_call.get("tool_name") == "file_read"
            ]

            result_file = vault / "notes" / "oauth_stress_result.md"
            self.soft_assert(
                len(file_read_calls) >= 20,
                "Live stress probe should produce at least 20 file_read calls",
            )
            self.soft_assert(
                result_file.exists(),
                "Second stress turn should write notes/oauth_stress_result.md",
            )

            summary = {
                "model": model,
                "openai_provider": _summarize_openai_status(openai_status),
                "session_id": session_id,
                "first_task_id": first["task_id"],
                "second_task_id": second["task_id"],
                "first_event_count": len(first["events"]),
                "second_event_count": len(second["events"]),
                "tool_call_count": len(tool_calls),
                "file_read_call_count": len(file_read_calls),
                "result_file_exists": result_file.exists(),
                "result_file_preview": (
                    result_file.read_text(encoding="utf-8")[:2000]
                    if result_file.exists()
                    else None
                ),
            }
            (self.artifacts_dir / "stress_summary.json").write_text(
                json.dumps(summary, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            (self.artifacts_dir / "first_turn_events.json").write_text(
                json.dumps(first["events"], indent=2, sort_keys=True),
                encoding="utf-8",
            )
            (self.artifacts_dir / "second_turn_events.json").write_text(
                json.dumps(second["events"], indent=2, sort_keys=True),
                encoding="utf-8",
            )
            (self.artifacts_dir / "session_detail.json").write_text(
                json.dumps(detail, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        finally:
            await self.stop_system()

        self.teardown_scenario()
        self.assert_no_failures()


def _seed_probe_files(scenario: BaseScenario, vault: Path) -> None:
    index_lines = ["# OAuth Codex live stress probe index", ""]
    for number in range(1, 25):
        name = f"probe_{number:02d}.md"
        path = f"notes/{name}"
        marker = f"MARKER-{number:02d}"
        scenario.create_file(
            vault,
            path,
            (
                f"# Probe {number:02d}\n\n"
                f"- marker: {marker}\n"
                f"- checksum_hint: {number * 7919}\n"
                f"- instruction: preserve this marker in the final count.\n"
            ),
        )
        index_lines.append(f"- notes/{name}: {marker}")
    scenario.create_file(vault, "notes/index.md", "\n".join(index_lines) + "\n")


def _configure_live_openai_settings(scenario: BaseScenario) -> str:
    live_settings_path = Path(
        os.environ.get("OPENAI_OAUTH_STRESS_SETTINGS", "/app/system/settings.yaml")
    )
    assert live_settings_path.exists(), (
        "Live OAuth stress probe requires /app/system/settings.yaml or "
        "OPENAI_OAUTH_STRESS_SETTINGS"
    )

    live_settings = yaml.safe_load(live_settings_path.read_text(encoding="utf-8")) or {}
    live_general = live_settings.get("settings", {})
    live_models = live_settings.get("models", {})
    live_providers = live_settings.get("providers", {})

    model = str(
        os.environ.get("OPENAI_OAUTH_STRESS_MODEL")
        or (live_general.get("default_model") or {}).get("value")
        or ""
    ).strip()
    assert model and model != "test", (
        "Live OAuth stress probe requires a non-test OpenAI model alias; set "
        "OPENAI_OAUTH_STRESS_MODEL or system.settings.default_model"
    )

    model_config = live_models.get(model)
    assert (
        isinstance(model_config, dict) and model_config.get("provider") == "openai"
    ), "Live OAuth stress probe model alias must resolve to the openai provider"
    openai_provider = live_providers.get("openai")
    assert isinstance(
        openai_provider, dict
    ), "Live OAuth stress probe requires an openai provider entry in live settings"

    controller = scenario._get_system_controller()  # noqa: SLF001
    isolated_settings_path = controller._system_root / "settings.yaml"  # noqa: SLF001
    isolated_settings = (
        yaml.safe_load(isolated_settings_path.read_text(encoding="utf-8")) or {}
    )
    isolated_settings.setdefault("settings", {})
    isolated_settings.setdefault("models", {})
    isolated_settings.setdefault("providers", {})

    isolated_settings["settings"]["default_model"] = {
        **(isolated_settings["settings"].get("default_model") or {}),
        "value": model,
    }
    if "openai_oauth_enabled" in live_general:
        isolated_settings["settings"]["openai_oauth_enabled"] = live_general[
            "openai_oauth_enabled"
        ]
    isolated_settings["models"][model] = model_config
    isolated_settings["providers"]["openai"] = openai_provider
    isolated_settings_path.write_text(
        yaml.safe_dump(isolated_settings, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )
    return model


def _find_openai_provider(payload: object) -> dict[str, object] | None:
    providers = payload
    if not isinstance(providers, list):
        return None
    for provider in providers:
        if isinstance(provider, dict) and provider.get("name") == "openai":
            return provider
    return None


def _find_model_metadata(
    payload: dict[str, object], model: str
) -> dict[str, object] | None:
    models = payload.get("models", [])
    if not isinstance(models, list):
        return None
    for candidate in models:
        if isinstance(candidate, dict) and candidate.get("name") == model:
            return candidate
    return None


def _summarize_openai_status(status: dict[str, object]) -> dict[str, object]:
    keys = (
        "name",
        "configured",
        "auth_mode",
        "effective_auth_mode",
        "status_message",
        "oauth_enabled",
        "oauth_connected",
    )
    return {key: status.get(key) for key in keys if key in status}


FIRST_TURN_PROMPT = """Run a Codex OAuth stress probe.

Use only file_read. Make separate read calls for notes/probe_01.md through
notes/probe_18.md, then make one read call for notes/missing_probe.md so we can
observe the tool failure path, then read notes/index.md. Recover from the
missing file and finish with a compact summary that reports how many probe
markers you saw."""


SECOND_TURN_PROMPT = """Continue the same stress probe.

Use file_read for the separate read calls for notes/probe_19.md through
notes/probe_24.md. Then use file_write to write notes/oauth_stress_result.md with:
- the phrase OAUTH_CODEX_STRESS_COMPLETE
- the total marker count across both turns
- a short note that the missing file from turn one did not stop the session."""
