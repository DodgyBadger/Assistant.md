"""Persisted readiness inputs and legacy compaction settings migration."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

import yaml
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.settings import run_settings_process


def readiness_messages():
    return [
        ModelRequest(parts=[UserPromptPart(content="Question")]),
        ModelResponse(parts=[TextPart(content="Answer")]),
    ]


def set_readiness_setting(app: Application, key: str, value: str) -> None:
    response = app.request(
        f"/api/system/settings/general/{key}", method="PUT", data={"value": value}
    )
    assert response.status_code == 200, response.text


def set_invalid_readiness_setting(app: Application, key: str, value: str) -> None:
    rejected = app.request(
        f"/api/system/settings/general/{key}", method="PUT", data={"value": value}
    )
    assert rejected.status_code == 400, rejected.text
    configuration = app.configuration()
    configuration["settings"][key]["value"] = int(value) if value.isdecimal() else value
    app.install_configuration(configuration)


def migrate_retired_compaction_settings(app: Application) -> dict[str, Any]:
    root = app.root / "retired-compaction-settings"
    root.mkdir()
    legacy = {
        "settings": {
            "context_reduction_strategy": {"value": "stepped_session_map"},
            "session_map_author_model": {"value": "legacy-author"},
            "session_map_author_thinking": {"value": "medium"},
            "session_map_low_watermark_tokens": {"value": 1234},
            "session_map_min_retained_groups": {"value": 4},
            "compaction_token_threshold": {"value": 5678},
        }
    }
    (root / "settings.yaml").write_text(yaml.safe_dump(legacy))
    run_settings_process(
        app,
        root,
        "from core.settings.store import load_settings\nfrom pathlib import Path\nimport json\nsettings = load_settings()\n(Path("
        + repr(str(root))
        + ") / 'effective.json').write_text(json.dumps(settings.model_dump(mode='json')))\n",
    )
    return json.loads((root / "effective.json").read_text())


def session_readiness(app: Application, *, session_id: str, vault_name: str):
    from core.memory.session_map.readiness import (
        evaluate_session_map_compaction_readiness,
    )
    from core.runtime.state import get_runtime_context

    readiness = evaluate_session_map_compaction_readiness(
        store=get_runtime_context().chat_store,
        session_id=session_id,
        vault_name=vault_name,
    )
    app.evidence.append(
        "session-map-readiness.jsonl", {"session_id": session_id, **asdict(readiness)}
    )
    return readiness
