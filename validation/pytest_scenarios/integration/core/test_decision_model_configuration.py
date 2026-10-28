"""Decision-only models, readiness, secrecy, and retired configuration repair."""

import json
import sqlite3

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.decision_model_inputs import RETIRED_SETTINGS

pytestmark = pytest.mark.scenario


async def test_decision_model_configuration_and_repair(app: Application) -> None:
    secret = "validation-typesafe-secret-value"
    app.evidence.register_secret(secret)
    await app.start()
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.llm.model_factory import build_model_instance
    from core.llm.model_utils import (
        get_model_capabilities,
        model_supports_capability,
        validate_api_keys,
    )
    from core.settings.repository import read_records
    from core.settings.store import ModelConfig

    assert ModelConfig(
        provider="typesafe",
        model_string="jev-latest",
        capabilities=[" Decision ", "decision"],
    ).capabilities == ["decision"]
    assert ModelConfig(
        provider="example", model_string="vision-model", capabilities=["vision"]
    ).capabilities == ["text", "vision"]
    general = app.request("/api/system/settings/general")
    assert general.status_code == 200
    assert all(
        not row["key"].startswith("live_session_memory_") for row in general.json()
    )
    models = app.request("/api/system/models")
    assert models.status_code == 200
    assert all(
        "embedding" not in row["capabilities"]
        and "dimensions" not in row
        and row["name"] != "jev"
        for row in models.json()
    )
    assert all(
        row["name"] != "typesafe" for row in app.request("/api/system/providers").json()
    )
    rejected = app.request(
        "/api/system/models/unused-embedding",
        method="PUT",
        data={
            "provider": "openai",
            "model_string": "text-embedding-3-small",
            "capabilities": ["embedding"],
        },
    )
    assert rejected.status_code == 400
    legacy = app.configuration()
    legacy["providers"]["typesafe"] = {
        "api_key": "TYPESAFE_API_KEY",
        "base_url": None,
        "user_editable": False,
    }
    app.install_configuration(legacy)
    assert "typesafe" not in app.configuration()["providers"]
    activity_path = app.root / "system/activity.log"
    assert any(
        row.get("data", {}).get("event") == "retired_builtin_providers_updated"
        and row["data"]["providers"] == ["typesafe"]
        for row in map(json.loads, activity_path.read_text().splitlines())
    )
    provider = app.request(
        "/api/system/providers/typesafe",
        method="PUT",
        data={"api_key": "TYPESAFE_API_KEY"},
    )
    assert provider.status_code == 200, provider.text
    created = app.request(
        "/api/system/models/jev",
        method="PUT",
        data={
            "provider": "typesafe",
            "model_string": "jev-latest",
            "capabilities": ["decision"],
        },
    )
    assert created.status_code == 200, created.text
    jev = created.json()
    assert (
        jev["provider"],
        jev["model_string"],
        jev["capabilities"],
        jev["user_editable"],
        jev["available"],
        jev["chat_selectable"],
    ) == ("typesafe", "jev-latest", ["decision"], True, False, False)
    assert get_model_capabilities("jev") == {"decision"}
    assert model_supports_capability("jev", "decision")
    assert not model_supports_capability("jev", "text")
    typesafe = next(
        row
        for row in app.request("/api/system/providers").json()
        if row["name"] == "typesafe"
    )
    assert (
        typesafe["api_key"],
        typesafe["base_url"],
        typesafe["api_key_has_value"],
        typesafe["user_editable"],
    ) == ("TYPESAFE_API_KEY", None, False, True)
    assert any(
        row["name"] == "TYPESAFE_API_KEY" and not row["has_value"]
        for row in app.request("/api/system/secrets").json()
    )
    updated = app.request(
        "/api/system/secrets",
        method="PUT",
        data={"name": "TYPESAFE_API_KEY", "value": secret},
    )
    assert updated.status_code == 200
    ready_models = app.request("/api/system/models").json()
    ready_providers = app.request("/api/system/providers").json()
    ready_secrets = app.request("/api/system/secrets").json()
    assert next(row for row in ready_models if row["name"] == "jev")["available"]
    assert next(row for row in ready_providers if row["name"] == "typesafe")[
        "api_key_has_value"
    ]
    assert secret not in str(
        (ready_models, ready_providers, ready_secrets, app.configuration())
    )
    assert secret not in activity_path.read_text()
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        validate_api_keys("jev")
        with pytest.raises(ValueError, match="does not declare the 'text' capability"):
            build_model_instance("jev")
    custom = app.request(
        "/api/system/models/validation-decision",
        method="PUT",
        data={
            "provider": "typesafe",
            "model_string": "jev-latest",
            "capabilities": ["decision"],
            "description": "Validation-only decision alias",
        },
    )
    assert custom.status_code == 200 and custom.json()["capabilities"] == ["decision"]
    legacy = app.configuration()
    legacy["models"]["legacy-vector"] = {
        "provider": "openai",
        "model_string": "text-embedding-3-small",
        "capabilities": [" Embedding "],
        "dimensions": 1536,
        "user_editable": True,
    }
    legacy["models"].pop("jev")
    for key, value in RETIRED_SETTINGS.items():
        legacy["settings"][key] = {
            "value": value,
            "description": "Retired configuration input",
            "category": "Chat",
            "restart_required": False,
        }
    app.install_configuration(legacy)
    assert all(
        row["key"] not in RETIRED_SETTINGS
        for row in app.request("/api/system/settings/general").json()
    )
    assert "legacy-vector" not in {
        row["name"] for row in app.request("/api/system/models").json()
    }
    backup_dir = app.root / "system/migration_backups"
    previous = set(backup_dir.rglob("settings.db.bak*"))
    repair = app.request("/api/system/settings/repair", method="POST")
    assert repair.status_code == 200, repair.text
    repaired = app.configuration()
    backups = set(backup_dir.rglob("settings.db.bak*")) - previous
    assert len(backups) == 1
    with sqlite3.connect(str(next(iter(backups)))) as conn:
        assert "legacy-vector" in read_records(conn)["models"]
    assert "legacy-vector" not in repaired["models"] and "jev" not in repaired["models"]
    assert repaired["models"]["validation-decision"]["capabilities"] == ["decision"]
    assert repaired["providers"]["typesafe"]["api_key"] == "TYPESAFE_API_KEY"
    assert RETIRED_SETTINGS.keys().isdisjoint(repaired["settings"])
    cleared = app.request(
        "/api/system/secrets",
        method="PUT",
        data={"name": "TYPESAFE_API_KEY", "value": ""},
    )
    assert cleared.status_code == 200
    assert not next(
        row
        for row in app.request("/api/system/models").json()
        if row["name"] == "validation-decision"
    )["available"]
