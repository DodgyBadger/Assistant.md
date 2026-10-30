"""Fresh installation defaults and explicit choices survive real persistence."""

import json

import pytest

from validation.scenarios.support.settings import run_settings_process

pytestmark = pytest.mark.scenario

# These values are the previously agreed product defaults, not model versions
# or incidental serialized formatting. Retain an explicit oracle across upgrades.
EXPECTED = {
    "compaction_high_watermark_tokens": 200000,
    "auto_cache_max_tokens": 30000,
    "vault_scan_interval_seconds": 300,
    "compaction_low_watermark_tokens": 40000,
    "task_snapshot_retention_days": 7,
    "default_chat_mode": "inline_edit",
    "chat_model_requests_limit": 0,
    "delegate_timeout_seconds": 0,
    "chunking_max_image_mb_per_image": 10,
    "compaction_strategy": "session_map",
    "web_search_strategy": "duckduckgo",
    "web_extract_strategy": "curl",
    "logfire": False,
    "openai_oauth_enabled": False,
    "editable_builtin_providers": [],
}


async def test_fresh_defaults_and_unconfigured_model_admission(app):
    await app.start()
    root = app.root / "fresh-shipped-contract"
    root.mkdir()
    run_settings_process(
        app,
        root,
        """
import json
from core.settings import store, validate_settings, get_compaction_author_model
settings = store.load_settings()
result = {"values": {key: entry.value for key, entry in settings.settings.items()}, "sources": {key: entry.source for key, entry in settings.settings.items()}, "compaction_author": get_compaction_author_model()}
settings.settings["compaction_high_watermark_tokens"].value = 150000
settings.settings["compaction_high_watermark_tokens"].source = "custom"
store.save_settings(settings)
store.refresh_settings_cache()
result["reloaded"] = store.load_settings().settings["compaction_high_watermark_tokens"].model_dump(mode="json")
from pathlib import Path
Path("""
        + repr(str(root / "observed.json"))
        + """).write_text(json.dumps(result))
""",
    )
    observed = json.loads((root / "observed.json").read_text())
    assert {key: observed["values"][key] for key in EXPECTED} == EXPECTED
    assert all(observed["sources"][key] == "default" for key in EXPECTED)
    assert observed["values"]["default_model"] == "none"
    assert observed["sources"]["default_model"] == "default"
    assert observed["compaction_author"] is None
    reset = app.request("/api/system/settings/defaults", "POST")
    assert reset.status_code == 200, reset.text
    health = app.request("/api/status")
    assert health.status_code == 200, health.text
    errors = [
        e
        for e in health.json()["configuration_status"]["issues"]
        if e["name"] == "setting:default_model"
    ]
    assert len(errors) == 1 and errors[0]["severity"] == "error"
    assert "compaction" in errors[0]["message"].lower()
    assert (observed["reloaded"]["value"], observed["reloaded"]["source"]) == (
        150000,
        "custom",
    )
    app.evidence.save_json("shipped-default-observations.json", observed)
