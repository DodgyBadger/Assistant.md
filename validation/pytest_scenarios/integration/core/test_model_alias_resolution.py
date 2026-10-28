"""Durable model aliases across public configuration and settings boundaries."""

from copy import deepcopy

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.model_configuration import (
    install_legacy_models,
)
from validation.pytest_scenarios.support.model_failures import require_activity

pytestmark = pytest.mark.scenario


async def test_model_aliases_survive_rename_and_settings_edits(
    app: Application,
) -> None:
    await app.start()
    from core.llm.model_utils import (
        get_model_capabilities,
        resolve_model,
    )

    payload = {
        "provider": "openai",
        "model_string": "validation-model",
        "capabilities": ["text", "vision"],
        "alternate_aliases": ["historical-model"],
    }
    created = app.request(
        "/api/system/models/current-model", method="PUT", data=payload
    )
    assert created.status_code == 200, created.text
    assert created.json()["alternate_aliases"] == ["historical-model"]
    assert "display_name" not in created.json()
    assert resolve_model(" Historical-Model ") == resolve_model("current-model")
    assert get_model_capabilities("historical-model") == {"text", "vision"}
    listed = app.request("/api/system/models").json()
    assert not any(model["name"] == "historical-model" for model in listed)
    for primary, alternate in (
        ("sol", "gpt"),
        ("terra", "gpt-mini"),
        ("luna", "gpt-nano"),
    ):
        assert resolve_model(alternate) == resolve_model(primary)
        assert any(model["name"] == primary for model in listed)
        assert not any(model["name"] == alternate for model in listed)
    before = app.configuration()
    collision = app.request(
        "/api/system/models/another-model", method="PUT", data=payload
    )
    assert collision.status_code == 400, collision.text
    assert app.configuration() == before
    # Rejected aliases/renames must leave persisted configuration intact.
    for aliases in (
        [""],
        ["none"],
        ["test"],
        ["CURRENT-MODEL"],
        ["old", " OLD "],
    ):
        invalid = app.request(
            "/api/system/models/current-model",
            method="PUT",
            data={**payload, "alternate_aliases": aliases},
        )
        assert invalid.status_code == 400, invalid.text
        assert app.configuration() == before
    reserved = app.request(
        "/api/system/models/ TEST ",
        method="PUT",
        data={**payload, "original_name": "current-model"},
    )
    assert reserved.status_code == 400, reserved.text
    assert app.configuration() == before
    assert resolve_model(" TEST ") == resolve_model("test")
    sonnet = next(model for model in listed if model["name"] == "sonnet")
    assert sonnet["user_editable"] is False
    managed_payload = {
        "provider": sonnet["provider"],
        "model_string": sonnet["model_string"],
        "capabilities": sonnet["capabilities"],
        "alternate_aliases": ["my-sonnet"],
    }
    edited = app.request(
        "/api/system/models/sonnet", method="PUT", data=managed_payload
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["alternate_aliases"] == ["my-sonnet"]
    blocked = app.request(
        "/api/system/models/sonnet",
        method="PUT",
        data={**managed_payload, "model_string": "custom-binding"},
    )
    assert blocked.status_code == 400, blocked.text
    renamed = app.request(
        "/api/system/models/renamed-model",
        method="PUT",
        data={**payload, "original_name": "current-model"},
    )
    assert renamed.status_code == 200, renamed.text
    assert set(renamed.json()["alternate_aliases"]) == {
        "historical-model",
        "current-model",
    }
    assert resolve_model("current-model") == resolve_model("renamed-model")
    assert not any(
        model["name"] == "current-model"
        for model in app.request("/api/system/models").json()
    )
    default = app.request(
        "/api/system/settings/general/default_model",
        method="PUT",
        data={"value": "my-sonnet"},
    )
    assert default.status_code == 200, default.text
    assert default.json()["issues"] == []
    assert default.json()["edit_value"] == "sonnet"
    assert "my-sonnet" not in [
        choice["value"] for choice in default.json()["editor"]["choices"]
    ]
    removed = app.request(
        "/api/system/models/sonnet",
        method="PUT",
        data={**managed_payload, "alternate_aliases": []},
    )
    assert removed.status_code == 200, removed.text
    settings = app.request("/api/system/settings/general").json()
    assert next(item for item in settings if item["key"] == "default_model")["issues"]
    # Duplication uses ordinary custom creation, which cannot copy managed identity.
    custom_payload = {
        "provider": sonnet["provider"],
        "model_string": "user-model-id",
        "capabilities": sonnet["capabilities"],
        "alternate_aliases": [],
    }
    copied = app.request(
        "/api/system/models/sonnet-custom", method="PUT", data=custom_payload
    )
    assert copied.status_code == 200, copied.text
    assert copied.json()["managed_id"] is None
    assert copied.json()["user_editable"] is True
    general = app.request("/api/system/settings/general").json()
    choices = next(item for item in general if item["key"] == "default_model")[
        "editor"
    ]["choices"]
    assert {
        choice["value"]: choice["label"]
        for choice in choices
        if choice["value"] in {"sonnet", "sonnet-custom"}
    } == {"sonnet": "sonnet", "sonnet-custom": "sonnet-custom"}
    repaired = app.request("/api/system/settings/repair", method="POST")
    assert repaired.status_code == 200, repaired.text
    custom = next(
        model
        for model in app.request("/api/system/models").json()
        if model["name"] == "sonnet-custom"
    )
    assert custom["model_string"] == "user-model-id"

    before = app.configuration()
    raw = deepcopy(before)
    raw["models"]["sonnet-custom"]["alternate_aliases"] = ["sonnet"]
    # Persisted invalid names remain diagnosable and never choose a winner.
    from core.settings.model_registry import InvalidModelAlias

    install_legacy_models(app, raw)
    try:
        models = app.request("/api/system/models")
        assert models.status_code == 200, models.text
        assert all(
            not model["available"]
            for model in models.json()
            if model["name"] in {"sonnet", "sonnet-custom"}
        )
        assert all(
            not model["chat_selectable"]
            for model in models.json()
            if model["name"] in {"sonnet", "sonnet-custom"}
        )
        metadata = app.request("/api/metadata")
        assert metadata.status_code == 200, metadata.text
        assert all(
            not model["chat_selectable"]
            for model in metadata.json()["models"]
            if model["name"] in {"sonnet", "sonnet-custom"}
        )
        # A broken identity must not disable unrelated models.
        assert resolve_model("renamed-model") == ("openai", "validation-model")
        try:
            resolve_model("sonnet")
        except InvalidModelAlias:
            pass
        else:
            raise AssertionError("Ambiguous aliases must not resolve")
    finally:
        install_legacy_models(app, before)
    # Load legacy persisted entries through the real configuration reload.
    # Obsolete presentation fields are excluded from API output; identities survive.
    legacy_aliases = app.configuration()
    legacy_aliases["models"]["renamed-model"]["display_name"] = "Legacy label"
    install_legacy_models(app, legacy_aliases)
    persisted = next(
        model
        for model in app.request("/api/system/models").json()
        if model["name"] == "renamed-model"
    )
    assert "display_name" not in persisted
    assert set(persisted["alternate_aliases"]) == {
        "historical-model",
        "current-model",
    }
    assert resolve_model(" HISTORICAL-MODEL ") == resolve_model("renamed-model")

    require_activity(
        app,
        "model_mapping_saved",
        fields={
            "alias": "renamed-model",
            "canonical_alias": "renamed-model",
            "original_alias": "current-model",
            "status": "completed",
        },
    )

    # Standard family aliases become managed even after older installations
    # persisted them as custom; conflicting bindings remain recoverable.
    original = app.configuration()
    try:
        legacy = deepcopy(original)
        legacy["models"]["sonnet"].update(
            managed_id=None,
            model_string="claude-sonnet-4-6",
            capabilities=["text"],
            user_editable=True,
        )
        install_legacy_models(app, legacy)
        adopted = app.configuration()["models"]["sonnet"]
        assert adopted["managed_id"] == "builtin-sonnet"
        assert adopted["model_string"] == "claude-sonnet-5-5"
        assert adopted["user_editable"] is False

        legacy["models"]["sonnet"].update(provider="openai", model_string="gpt-6-astra")
        conflicting = deepcopy(legacy)
        install_legacy_models(app, conflicting)
        status = app.request("/api/status").json()["configuration_status"]
        assert app.configuration() == conflicting
        assert any(
            issue["name"] == "models:catalog_update"
            and issue["severity"] == "warning"
            and "sonnet" in issue["message"]
            for issue in status["issues"]
        )
        restored = app.request(
            "/api/system/models/sonnet",
            method="PUT",
            data={
                "provider": "anthropic",
                "model_string": "claude-sonnet-4-6",
                "capabilities": ["text"],
            },
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["managed_id"] == "builtin-sonnet"
        assert restored.json()["model_string"] == "claude-sonnet-5-5"
        assert restored.json()["user_editable"] is False
    finally:
        install_legacy_models(app, original)
    # Raw-YAML configuration mutation is no longer exposed by the API.
    assert app.request("/api/system/settings").status_code == 404
    assert app.request("/api/system/settings", method="PUT", data={}).status_code == 404
