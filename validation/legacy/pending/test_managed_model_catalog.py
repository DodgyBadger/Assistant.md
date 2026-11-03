# Deferred reference only; see validation/registry/pytest-gaps.json.
# Original complete source is available in Git before the 2026-10-31 cleanup.
"""Managed model upgrades preserve user ownership and durable references."""

from copy import deepcopy

import pytest

from core.settings.model_catalog import upgrade_model_catalog


@pytest.fixture(autouse=True)
def isolated_roots(tmp_path, monkeypatch):
    from core.runtime import paths

    monkeypatch.setattr(paths, "_bootstrap_system_root", tmp_path)
    monkeypatch.setattr(paths, "_bootstrap_data_root", tmp_path / "data")


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
