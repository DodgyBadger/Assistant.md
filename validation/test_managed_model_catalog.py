"""Managed model upgrades preserve user ownership and durable references."""

from copy import deepcopy

from core.settings.model_catalog import upgrade_model_catalog


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


def test_verified_legacy_baseline_adopts_only_untouched_binding() -> None:
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
            "managed_id": "anthropic-sonnet",
            "user_editable": False,
        }
    }
    adopted = upgrade_model_catalog(legacy, template)
    assert adopted.models["sonnet"]["managed_id"] == "anthropic-sonnet"
    assert adopted.models["sonnet"]["user_editable"] is False
    legacy["sonnet"]["model_string"] = "my-own-model"
    custom = upgrade_model_catalog(legacy, template)
    assert custom.models["sonnet"]["model_string"] == "my-own-model"
    assert custom.models["sonnet"]["managed_id"] is None


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
