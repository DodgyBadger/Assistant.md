"""Real application behavior staged for the pytest migration."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_settings_editors(app: Application) -> None:
    import json

    vault = app.vault("SettingsEditorsVault")
    custom_context = vault / "AssistantMD" / "Authoring" / "custom-context.md"
    custom_context.parent.mkdir(parents=True, exist_ok=True)
    custom_context.write_text(
        "---\nrun_type: context\n---\nCustom context\n", encoding="utf-8"
    )
    await app.start()
    strategy_response = app.request(
        "/api/system/settings/general/compaction_strategy",
        method="PUT",
        data={"value": "recovery_card"},
    )
    assert strategy_response.status_code == 200, strategy_response.text

    def settings():
        response = app.request("/api/system/settings/general")
        assert response.status_code == 200, response.text
        return {entry["key"]: entry for entry in response.json()}

    def update(key, value, status=200):
        response = app.request(
            f"/api/system/settings/general/{key}", method="PUT", data={"value": value}
        )
        assert response.status_code == status, response.text
        return response

    baseline = settings()
    assert baseline["debug"]["editor"]["kind"] == "boolean"
    assert baseline["max_concurrent_delegates"]["editor"]["maximum"] == 32
    assert baseline["default_model"]["editor"]["kind"] == "select"
    assert "sonnet" in [
        choice["value"] for choice in baseline["default_model"]["editor"]["choices"]
    ]
    assert baseline["disabled_tools"]["editor"]["kind"] == "multiselect"
    assert (
        baseline["ingestion_pdf_default_strategies"]["editor"]["kind"] == "ordered_list"
    )
    assert baseline["vault_state_excluded_patterns"]["editor"]["kind"] == "string_list"
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
        before = app.configuration()
        update(key, raw, 400)
        assert app.configuration() == before
    update("persist_model_reasoning_parts", "true")
    update("model_stream_retry_base_delay_seconds", "0.5")
    update("disabled_tools", '["file_read"]')
    update("disabled_tools", "[]")
    update("ingestion_pdf_default_strategies", '["pdf_text","pdf_ocr"]')
    update("compaction_author_model", "none")
    persisted = app.configuration()
    assert persisted["settings"]["persist_model_reasoning_parts"]["value"] is True
    assert (
        persisted["settings"]["model_stream_retry_base_delay_seconds"]["value"] == 0.5
    )
    assert persisted["settings"]["ingestion_pdf_default_strategies"]["value"] == [
        "pdf_text",
        "pdf_ocr",
    ]
    persisted["settings"]["default_model"]["value"] = "retired-alias"
    persisted["settings"]["max_concurrent_delegates"]["value"] = "broken"
    persisted["settings"]["default_chat_mode"]["value"] = "typo"
    persisted["settings"]["vault_state_excluded_patterns"]["value"] = [42]
    app.install_configuration(persisted)
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
    persisted = app.configuration()
    persisted["settings"]["compaction_low_watermark_tokens"]["value"] = 100000
    app.install_configuration(persisted)
    invalid = settings()
    for key in ("compaction_low_watermark_tokens", "compaction_high_watermark_tokens"):
        assert any(issue["code"] == "relationship" for issue in invalid[key]["issues"])
    update("chat_tool_calls_limit", "3")
    update("compaction_low_watermark_tokens", "90000")
    update("compaction_low_watermark_tokens", "110000", 400)
    update("compaction_high_watermark_tokens", "120000")
    update("compaction_low_watermark_tokens", "20000")
    update("compaction_high_watermark_tokens", "80000")
    persisted = app.configuration()
    persisted["models"]["sonnet"]["model_string"] = "upgraded-model-id"
    app.install_configuration(persisted)
    assert not settings()["default_model"]["issues"]
    saved_model = persisted["models"].pop("sonnet")
    app.install_configuration(persisted)
    assert settings()["default_model"]["issues"]
    update("default_model", "sonnet", 400)
    persisted["models"]["sonnet"] = saved_model
    app.install_configuration(persisted)
    assert not settings()["default_model"]["issues"]
    persisted["models"] = {}
    app.install_configuration(persisted)
    empty_models = settings()
    assert empty_models["default_model"]["issues"]
    assert [
        choice["value"] for choice in empty_models["default_model"]["editor"]["choices"]
    ] == ["none"]
    assert not empty_models["compaction_author_model"]["issues"]
    persisted["tools"].pop("file_read")
    app.install_configuration(persisted)
    tool_choices = settings()["disabled_tools"]["editor"]["choices"]
    assert "file_read" in [choice["value"] for choice in tool_choices]
    update("disabled_tools", '["file_read"]')
    assert not settings()["disabled_tools"]["issues"]
