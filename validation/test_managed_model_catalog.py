"""Managed model upgrades preserve user ownership and durable references."""

from copy import deepcopy

from core.settings.model_catalog import upgrade_model_catalog


def test_standard_alias_adopts_matching_family_even_when_explicitly_unmanaged() -> None:
    template = {
        "sonnet": {
            **entry("builtin-sonnet", binding="claude-sonnet-5-5"),
            "provider": "anthropic",
            "capabilities": ["text", "vision"],
        }
    }
    for has_identity in (True, False):
        old = {
            **entry(None, aliases=["my-sonnet"], binding="claude-sonnet-4-6"),
            "provider": "anthropic",
        }
        if not has_identity:
            old.pop("managed_id")
        custom = {**deepcopy(old), "managed_id": None, "alternate_aliases": []}
        active = {"sonnet": old, "sonnet-custom": custom}
        result = upgrade_model_catalog(active, template)
        assert not result.issues
        assert result.updated == ["sonnet"]
        assert result.models["sonnet"]["managed_id"] == "builtin-sonnet"
        assert result.models["sonnet"]["model_string"] == "claude-sonnet-5-5"
        assert result.models["sonnet"]["alternate_aliases"] == ["my-sonnet"]
        assert result.models["sonnet-custom"] == custom
        assert not upgrade_model_catalog(result.models, template).updated


def test_standard_alias_family_conflict_preserves_settings_and_blocks_upgrade() -> None:
    template = {
        "sonnet": {
            **entry("builtin-sonnet", binding="claude-sonnet-5-5"),
            "provider": "anthropic",
        }
    }
    for provider, binding in (
        ("openai", "claude-sonnet-4-6"),
        ("anthropic", "claude-opus-4-6"),
    ):
        active = {"sonnet": {**entry(None, binding=binding), "provider": provider}}
        result = upgrade_model_catalog(active, template)
        assert result.models == active
        assert result.issues
        assert not result.updated
        assert not result.renames


def test_unmanaged_historical_openai_aliases_adopt_before_renaming() -> None:
    import yaml

    from core.settings.model_registry import build_model_registry
    from core.settings.store import SETTINGS_TEMPLATE, ModelConfig

    catalog = yaml.safe_load(SETTINGS_TEMPLATE.read_text())["models"]
    for old, primary, binding in (
        ("gpt", "sol", "gpt-5.6-sol"),
        ("gpt-mini", "terra", "gpt-5.6-terra"),
        ("gpt-nano", "luna", "gpt-5.6-luna"),
    ):
        active = {old: entry(None, binding=binding, aliases=["historical"])}
        result = upgrade_model_catalog(active, catalog)
        assert not result.issues
        assert result.renames == [(old, primary)]
        assert result.models[primary]["managed_id"] == catalog[primary]["managed_id"]
        assert result.models[primary]["alternate_aliases"] == ["historical", old]
        registry = build_model_registry(
            {
                key: ModelConfig.model_validate(value)
                for key, value in result.models.items()
            }
        )
        assert registry.resolve(old) == primary


def test_older_provider_naming_formats_still_identify_their_model_family() -> None:
    import yaml

    from core.settings.store import SETTINGS_TEMPLATE

    catalog = yaml.safe_load(SETTINGS_TEMPLATE.read_text())["models"]
    for alias, provider, binding in (
        ("sonnet", "anthropic", "claude-3-7-sonnet-latest"),
        ("opus", "anthropic", "claude-3-opus-20240229"),
        ("haiku", "anthropic", "claude-3-5-haiku-20241022"),
        ("gpt", "openai", "gpt-4-turbo-2024-04-09"),
        ("gpt-mini", "openai", "gpt-4o-mini-2024-07-18"),
        ("gemini", "google", "gemini-2.5-pro-preview-06-05"),
    ):
        active = {alias: {**entry(None, binding=binding), "provider": provider}}
        result = upgrade_model_catalog(active, catalog)
        assert not result.issues, (alias, result.issues)
        assert result.updated
        assert all(model["managed_id"] for model in result.models.values())


def test_shipped_anthropic_models_support_adaptive_thinking() -> None:
    import yaml
    from pydantic_ai.profiles.anthropic import anthropic_model_profile

    from core.settings.store import SETTINGS_TEMPLATE

    models = yaml.safe_load(SETTINGS_TEMPLATE.read_text())["models"]
    for name in ("sonnet", "opus", "haiku"):
        profile = anthropic_model_profile(models[name]["model_string"])
        assert profile is not None
        assert profile["anthropic_supports_adaptive_thinking"], name
        assert profile["supports_json_schema_output"], name


def test_jev_retirement_preserves_custom_models_and_is_idempotent() -> None:
    managed = {
        **entry("builtin-jev", aliases=["old-decision"]),
        "provider": "typesafe",
        "model_string": "jev-latest",
        "capabilities": ["decision"],
    }
    legacy = {key: value for key, value in managed.items() if key != "managed_id"}
    custom = {**managed, "managed_id": None, "user_editable": True}
    for retired in (managed, legacy):
        active = {"jev": retired, "my-decision": custom}
        result = upgrade_model_catalog(active, {})
        assert not result.issues
        assert result.models == {"my-decision": custom}
        assert result.removed == ["jev"]
        assert "jev" in active
        assert not upgrade_model_catalog(result.models, {}).removed
    assert upgrade_model_catalog({"jev": custom}, {}).models == {"jev": custom}
    customized_legacy = {**legacy, "model_string": "my-jev"}
    assert "jev" in upgrade_model_catalog({"jev": customized_legacy}, {}).models


def entry(
    identity: str | None = "stable",
    *,
    aliases: list[str] | None = None,
    binding: str = "v1",
) -> dict:
    return {
        "managed_id": identity,
        "provider": "openai",
        "model_string": binding,
        "capabilities": ["text"],
        "user_editable": identity is None,
        "alternate_aliases": aliases or [],
    }


def test_repeated_renames_and_user_removal_are_idempotent() -> None:
    active = {"old": entry(aliases=["user-name"])}
    template = {"new": entry(binding="v2")}
    result = upgrade_model_catalog(active, template)
    assert not result.issues
    assert result.models["new"]["alternate_aliases"] == ["user-name", "old"]
    assert result.models["new"]["model_string"] == "v2"
    assert result.renames == [("old", "new")]
    assert upgrade_model_catalog(result.models, template).models == result.models
    result.models["new"]["alternate_aliases"].remove("old")
    assert upgrade_model_catalog(result.models, template).models == result.models
    again = upgrade_model_catalog(result.models, {"third": entry(binding="v3")})
    assert again.models["third"]["alternate_aliases"] == ["user-name", "new"]


def test_same_model_alternate_promotion_and_collision_rollback() -> None:
    active = {"old": entry(aliases=["new", "historic"])}
    promoted = upgrade_model_catalog(active, {"new": entry()})
    assert promoted.models["new"]["alternate_aliases"] == ["historic", "old"]
    active["custom"] = entry(None, aliases=["blocked"])
    original = deepcopy(active)
    collision = upgrade_model_catalog(active, {"blocked": entry(binding="v2")})
    assert collision.issues
    assert collision.models == original == active


def test_custom_entries_are_not_adopted_or_retargeted() -> None:
    active = {"new": entry(None, binding="custom")}
    result = upgrade_model_catalog(active, {"new": entry(binding="v2")})
    assert result.models == active
    assert not result.renames


def test_standard_family_adoption_rejects_unknown_bindings() -> None:
    legacy = {
        "sonnet": {
            "provider": "anthropic",
            "model_string": "claude-sonnet-5",
            "capabilities": ["text", "vision"],
            "user_editable": True,
        }
    }
    template = {
        "sonnet": {
            **legacy["sonnet"],
            "managed_id": "builtin-sonnet",
            "user_editable": False,
        }
    }
    adopted = upgrade_model_catalog(legacy, template)
    assert adopted.models["sonnet"]["managed_id"] == "builtin-sonnet"
    assert adopted.models["sonnet"]["user_editable"] is False
    legacy["sonnet"]["model_string"] = "my-own-model"
    custom = upgrade_model_catalog(legacy, template)
    assert custom.models["sonnet"]["model_string"] == "my-own-model"
    assert custom.models["sonnet"].get("managed_id") is None


def test_load_persists_rename_and_backup_and_preserves_removed_alias(
    tmp_path, monkeypatch
) -> None:
    import yaml

    from core.settings import store

    active_path = tmp_path / "settings.yaml"
    template_path = tmp_path / "template.yaml"
    active = {"models": {"old": entry(aliases=["optional"])}}
    active_path.write_text(yaml.safe_dump(active))
    template_path.write_text(yaml.safe_dump({"models": {"new": entry(binding="v2")}}))
    monkeypatch.setattr(store, "get_active_settings_path", lambda: active_path)
    monkeypatch.setattr(store, "SETTINGS_TEMPLATE", template_path)
    store.refresh_settings_cache()
    try:
        loaded = store.load_settings()
        assert loaded.models["new"].alternate_aliases == ["optional", "old"]
        assert (
            yaml.safe_load(active_path.read_text())["models"]["new"]["model_string"]
            == "v2"
        )
        backups = list((tmp_path / "migration_backups").glob("settings.yaml.bak*"))
        assert len(backups) == 1
        assert yaml.safe_load(backups[0].read_text()) == active
        loaded.models["new"].alternate_aliases.remove("old")
        store.save_settings(loaded)
        store.refresh_settings_cache()
        assert store.load_settings().models["new"].alternate_aliases == ["optional"]
        assert (
            len(list((tmp_path / "migration_backups").glob("settings.yaml.bak*"))) == 1
        )
    finally:
        store.refresh_settings_cache()


def test_load_collision_preserves_file_and_exposes_issue(tmp_path, monkeypatch) -> None:
    import yaml

    from core.settings import store

    active_path = tmp_path / "settings.yaml"
    template_path = tmp_path / "template.yaml"
    active = {"models": {"old": entry(), "new": entry(None)}}
    original = yaml.safe_dump(active)
    active_path.write_text(original)
    template_path.write_text(yaml.safe_dump({"models": {"new": entry(binding="v2")}}))
    monkeypatch.setattr(store, "get_active_settings_path", lambda: active_path)
    monkeypatch.setattr(store, "SETTINGS_TEMPLATE", template_path)
    store.refresh_settings_cache()
    try:
        loaded = store.load_settings()
        assert loaded._catalog_issues
        assert active_path.read_text() == original
        assert loaded.models["old"].model_string == "v1"
        assert not (tmp_path / "migration_backups").exists()
    finally:
        store.refresh_settings_cache()


def test_load_persists_retirement_without_other_catalog_updates(
    tmp_path, monkeypatch
) -> None:
    import yaml

    from core.settings import store

    active_path = tmp_path / "settings.yaml"
    template_path = tmp_path / "template.yaml"
    active = {"models": {"jev": entry("builtin-jev")}}
    active_path.write_text(yaml.safe_dump(active))
    template_path.write_text(yaml.safe_dump({"models": {}}))
    monkeypatch.setattr(store, "get_active_settings_path", lambda: active_path)
    monkeypatch.setattr(store, "SETTINGS_TEMPLATE", template_path)
    store.refresh_settings_cache()
    try:
        assert store.load_settings().models == {}
        assert yaml.safe_load(active_path.read_text())["models"] == {}
        backups = list((tmp_path / "migration_backups").glob("settings.yaml.bak*"))
        assert len(backups) == 1
        assert yaml.safe_load(backups[0].read_text()) == active
        store.refresh_settings_cache()
        assert store.load_settings().models == {}
        assert len(list((tmp_path / "migration_backups").iterdir())) == 1
    finally:
        store.refresh_settings_cache()


def test_concurrent_cold_loads_publish_one_complete_upgrade(
    tmp_path, monkeypatch
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from time import sleep

    import yaml

    from core.settings import store

    active_path = tmp_path / "settings.yaml"
    template_path = tmp_path / "template.yaml"
    active_path.write_text(yaml.safe_dump({"models": {"old": entry()}}))
    template_path.write_text(yaml.safe_dump({"models": {"new": entry(binding="v2")}}))
    monkeypatch.setattr(store, "get_active_settings_path", lambda: active_path)
    monkeypatch.setattr(store, "SETTINGS_TEMPLATE", template_path)
    copyfile = store.shutil.copyfile

    def delayed_backup(source, destination):
        sleep(0.03)
        return copyfile(source, destination)

    monkeypatch.setattr(store.shutil, "copyfile", delayed_backup)
    store.refresh_settings_cache()
    try:
        with ThreadPoolExecutor(max_workers=6) as executor:
            loaded = list(executor.map(lambda _: store.load_settings(), range(6)))
        assert all(item.models["new"].alternate_aliases == ["old"] for item in loaded)
        assert (
            yaml.safe_load(active_path.read_text())["models"]["new"]["model_string"]
            == "v2"
        )
        assert (
            len(list((tmp_path / "migration_backups").glob("settings.yaml.bak*"))) == 1
        )
    finally:
        store.refresh_settings_cache()
