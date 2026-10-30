"""Real catalog import, shipped profiles and durable alternate promotion."""

import pytest

from validation.scenarios.support.legacy_model_catalog import import_legacy_catalog

pytestmark = pytest.mark.scenario


async def test_older_provider_families_adopt_the_current_shipped_catalog(app):
    await app.start()
    current = app.configuration()["models"]
    legacy = {
        "models": {
            alias: {
                "provider": provider,
                "model_string": binding,
                "user_editable": True,
                "capabilities": ["text"],
            }
            for alias, provider, binding in (
                ("sonnet", "anthropic", "claude-3-7-sonnet-latest"),
                ("opus", "anthropic", "claude-3-opus-20240229"),
                ("haiku", "anthropic", "claude-3-5-haiku-20241022"),
                ("gpt", "openai", "gpt-4-turbo-2024-04-09"),
                ("gpt-mini", "openai", "gpt-4o-mini-2024-07-18"),
                ("gemini", "google", "gemini-2.5-pro-preview-06-05"),
            )
        }
    }
    result = import_legacy_catalog(app, "older-families", legacy)
    for old, canonical in (
        ("sonnet", "sonnet"),
        ("opus", "opus"),
        ("haiku", "haiku"),
        ("gpt", "sol"),
        ("gpt-mini", "terra"),
        ("gemini", "gemini"),
    ):
        actual = result["effective"]["models"][canonical]
        assert actual["managed_id"] == current[canonical]["managed_id"]
        assert actual["model_string"] == current[canonical]["model_string"]
        assert actual["user_editable"] is False
        assert result["persisted_models"][canonical] == actual
        if old != canonical:
            assert old in actual["alternate_aliases"]
    assert result["backups"] == [result["original"]]


async def test_shipped_anthropic_bindings_support_thinking_and_schema_output(app):
    await app.start()
    from pydantic_ai.profiles.anthropic import anthropic_model_profile

    models = app.configuration()["models"]
    profiles = {}
    for name in ("sonnet", "opus", "haiku"):
        profile = anthropic_model_profile(models[name]["model_string"])
        assert profile is not None
        assert profile["anthropic_supports_adaptive_thinking"], name
        assert profile["supports_json_schema_output"], name
        profiles[name] = dict(profile)
    app.evidence.save_json("shipped-sdk-profiles.json", profiles)


async def test_alternate_promotion_and_repeated_rename_survive_reload(app):
    await app.start()
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.llm.model_utils import resolve_model
    from core.runtime.reload_service import reload_configuration
    from core.settings.model_registry import UnknownModelAlias

    payload = {
        "provider": "openai",
        "model_string": "validation-binding",
        "capabilities": ["text"],
        "alternate_aliases": ["beta", "historic"],
    }
    created = app.request("/api/system/models/alpha", "PUT", payload)
    assert created.status_code == 200, created.text
    promoted = app.request(
        "/api/system/models/beta", "PUT", {**payload, "original_name": "alpha"}
    )
    assert promoted.status_code == 200, promoted.text
    assert set(promoted.json()["alternate_aliases"]) == {"historic", "alpha"}
    renamed = app.request(
        "/api/system/models/gamma",
        "PUT",
        {
            **payload,
            "original_name": "beta",
            "alternate_aliases": ["historic", "alpha"],
        },
    )
    assert renamed.status_code == 200, renamed.text
    assert set(renamed.json()["alternate_aliases"]) == {"historic", "alpha", "beta"}
    removed = app.request(
        "/api/system/models/gamma",
        "PUT",
        {**payload, "alternate_aliases": ["historic", "beta"]},
    )
    assert removed.status_code == 200, removed.text
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        reload_configuration()
    assert resolve_model("beta") == resolve_model("historic") == resolve_model("gamma")
    with pytest.raises(UnknownModelAlias):
        resolve_model("alpha")
    before = app.configuration()
    collision = app.request("/api/system/models/other", "PUT", payload)
    assert collision.status_code == 400, collision.text
    assert app.configuration() == before


async def test_concurrent_cold_loads_publish_one_complete_upgrade_and_backup(app):
    await app.start()
    from validation.scenarios.support.catalog_cold_load import concurrent_catalog_load

    results, backups, original = await concurrent_catalog_load(app)
    assert len(results) == 3 and results[0] == results[1] == results[2]
    sol = results[0]["models"]["sol"]
    assert sol["managed_id"] == app.configuration()["models"]["sol"]["managed_id"]
    assert sol["model_string"] == app.configuration()["models"]["sol"]["model_string"]
    assert set(sol["alternate_aliases"]) == {"gpt", "historic-cold"}
    assert "gpt" not in results[0]["models"]
    assert backups == [original]


async def test_shared_legacy_alternates_disable_both_models_without_choosing_a_winner(
    app,
):
    await app.start()
    from copy import deepcopy

    from core.llm.model_utils import resolve_model
    from core.settings.model_registry import InvalidModelAlias
    from validation.scenarios.support.model_configuration import install_legacy_models

    original = app.configuration()
    legacy = deepcopy(original)
    for name, alternate in (("first", "shared"), ("second", "SHARED")):
        legacy["models"][name] = {
            "provider": "openai",
            "model_string": name,
            "user_editable": True,
            "capabilities": ["text"],
            "alternate_aliases": [alternate],
        }
    try:
        install_legacy_models(app, legacy)
        listed = app.request("/api/system/models").json()
        broken = [model for model in listed if model["name"] in ("first", "second")]
        assert len(broken) == 2
        assert all(
            not model["available"] and not model["chat_selectable"] for model in broken
        )
        for alias in ("first", "second", "shared"):
            with pytest.raises(InvalidModelAlias):
                resolve_model(alias)
        assert resolve_model("sol") == resolve_model("gpt")
    finally:
        install_legacy_models(app, original)
