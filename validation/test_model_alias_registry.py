"""Model identity boundaries independent of provider credentials or runtime state."""

import pytest

from core.settings.model_registry import (
    InvalidModelAlias,
    UnknownModelAlias,
    build_model_registry,
)
from core.settings.store import ModelConfig


def test_custom_rename_cannot_claim_validation_fixture(monkeypatch) -> None:
    from core.settings import SettingsError, config_editor
    from core.settings.store import ProviderConfig, SettingsFile

    settings = SettingsFile(
        models={"mine": model()}, providers={"openai": ProviderConfig()}
    )
    monkeypatch.setattr(config_editor, "load_settings", lambda: settings)
    writes = []
    monkeypatch.setattr(config_editor, "_persist_changes", writes.append)
    with pytest.raises(SettingsError):
        config_editor.upsert_model_mapping(
            " TEST ", "openai", "example", original_name="mine"
        )
    assert writes == []
    assert list(settings.models) == ["mine"]


def test_removed_display_name_is_ignored_in_saved_model_entries() -> None:
    config = ModelConfig.model_validate(
        {
            "provider": "openai",
            "model_string": "example",
            "alternate_aliases": ["old"],
            "display_name": "Obsolete label",
        }
    )
    assert "display_name" not in config.model_dump()
    assert config.alternate_aliases == ["old"]


def model(*aliases: str) -> ModelConfig:
    return ModelConfig(
        provider="openai", model_string="example", alternate_aliases=list(aliases)
    )


def test_aliases_normalize_and_resolve_to_one_canonical_identity() -> None:
    registry = build_model_registry({" Canonical ": model(" OLD ")})
    registry.validate()
    assert registry.resolve("canonical") == " Canonical "
    assert registry.resolve("old") == " Canonical "
    with pytest.raises(UnknownModelAlias):
        registry.resolve("missing")


@pytest.mark.parametrize(
    "aliases", [[""], ["none"], ["test"], ["SAME"], ["old", " OLD "]]
)
def test_reserved_and_duplicate_names_are_invalid(aliases: list[str]) -> None:
    registry = build_model_registry({"same": model(*aliases)})
    with pytest.raises(ValueError):
        registry.validate()
    with pytest.raises(InvalidModelAlias):
        registry.resolve("same")


def test_collisions_never_choose_a_winner_but_keep_unaffected_models_usable() -> None:
    registry = build_model_registry(
        {"first": model("shared"), "second": model("SHARED"), "third": model()}
    )
    assert set(registry.issues) == {"first", "second"}
    for alias in ["shared", "first", "second"]:
        with pytest.raises(InvalidModelAlias):
            registry.resolve(alias)
    assert registry.resolve("third") == "third"


def test_validation_model_can_be_a_canonical_fixture() -> None:
    registry = build_model_registry({"test": model()})
    registry.validate()
    assert registry.resolve("TEST") == "test"
