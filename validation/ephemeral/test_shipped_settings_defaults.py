"""The agreed shipped defaults and preservation of explicit installation choices."""

import json
import sqlite3

from core.settings import store


def test_shipped_numeric_defaults_and_local_overrides(tmp_path, monkeypatch):
    from core.runtime import paths

    monkeypatch.setattr(paths, "_bootstrap_system_root", tmp_path)
    monkeypatch.setattr(paths, "_bootstrap_data_root", tmp_path / "data")
    store.refresh_settings_cache()
    try:
        defaults = store.get_default_settings()
        assert defaults["compaction_high_watermark_tokens"].value == 200000
        assert defaults["auto_cache_max_tokens"].value == 30000
        assert defaults["vault_scan_interval_seconds"].value == 300
        assert defaults["compaction_low_watermark_tokens"].value == 40000
        assert defaults["task_snapshot_retention_days"].value == 7
        assert defaults["default_chat_mode"].value == "inline_edit"
        assert defaults["chat_model_requests_limit"].value == 0
        assert defaults["delegate_timeout_seconds"].value == 0
        assert defaults["chunking_max_image_mb_per_image"].value == 10
        assert defaults["compaction_strategy"].value == "session_map"
        assert defaults["web_search_strategy"].value == "duckduckgo"
        assert defaults["web_extract_strategy"].value == "curl"
        assert defaults["logfire"].value is False
        assert defaults["openai_oauth_enabled"].value is False
        assert defaults["editable_builtin_providers"].value == []
        initial = store.load_settings()
        assert initial.settings["compaction_high_watermark_tokens"].source == "default"
        initial.settings["compaction_high_watermark_tokens"].source = "custom"
        initial.settings["compaction_high_watermark_tokens"].value = 150000
        store.save_settings(initial)
        assert (
            store.load_settings().settings["compaction_high_watermark_tokens"].value
            == 150000
        )
        with sqlite3.connect(tmp_path / "settings.db") as conn:
            assert (
                json.loads(
                    conn.execute(
                        "SELECT payload FROM configuration_settings WHERE name='compaction_high_watermark_tokens'"
                    ).fetchone()[0]
                )["value"]
                == 150000
            )
    finally:
        store.refresh_settings_cache()


def test_unconfigured_default_is_valid_storage_but_requires_setup(
    tmp_path, monkeypatch
):
    import core.secrets
    from core.runtime import paths
    from core.secrets.bootstrap import SecretsBootstrapState, SecretsBootstrapStatus
    from core.settings import get_compaction_author_model, validate_settings
    from core.settings.editor_schema import build_editors, value_issues

    monkeypatch.setattr(
        core.secrets,
        "get_secrets_bootstrap_status",
        lambda: SecretsBootstrapStatus(
            SecretsBootstrapState.LOCKED, "Validation fixture"
        ),
    )

    monkeypatch.setattr(paths, "_bootstrap_system_root", tmp_path)
    monkeypatch.setattr(paths, "_bootstrap_data_root", tmp_path / "data")
    store.refresh_settings_cache()
    try:
        snapshot = store.load_settings()
        assert snapshot.settings["default_model"].value == "none"
        assert snapshot.settings["default_model"].source == "default"
        assert get_compaction_author_model() is None
        editors = build_editors(snapshot, store.get_default_settings())
        assert value_issues("default_model", "none", editors["default_model"]) == []
        status = validate_settings()
        errors = [
            issue for issue in status.errors if issue.name == "setting:default_model"
        ]
        assert len(errors) == 1
        assert "Select a default model" in errors[0].message
        assert "compaction" in errors[0].message
    finally:
        store.refresh_settings_cache()
