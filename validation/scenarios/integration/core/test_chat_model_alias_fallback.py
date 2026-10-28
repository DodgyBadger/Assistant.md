"""Chat aliases fall back on missing names, not provider failures."""

import json
from copy import deepcopy

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_tasks import chat_turn
from validation.scenarios.support.model_configuration import (
    install_legacy_models,
)

pytestmark = pytest.mark.scenario


async def test_chat_alias_fallback_and_unavailable_provider(app: Application) -> None:
    vault = app.vault("AliasChatVault")
    await app.start()
    from core.chat.model_selection import resolve_chat_model_alias
    from core.llm.model_factory import build_model_instance
    from core.settings.model_registry import UnknownModelAlias

    edited = app.request(
        "/api/system/models/test",
        method="PUT",
        data={
            "provider": "test",
            "model_string": "test",
            "alternate_aliases": ["historical-test"],
        },
    )
    assert edited.status_code == 200, edited.text
    default = app.request(
        "/api/system/settings/general/default_model",
        method="PUT",
        data={"value": "historical-test"},
    )
    assert default.status_code == 200, default.text
    assert resolve_chat_model_alias("historical-test") == "test"
    assert resolve_chat_model_alias("removed-alias") == "test"
    assert resolve_chat_model_alias("sonnet") == "sonnet"
    with pytest.raises(UnknownModelAlias):
        build_model_instance("removed-alias")
    first = await chat_turn(
        app,
        {
            "vault_name": vault.name,
            "prompt": "Reply briefly.",
            "session_id": "alias-fallback-session",
            "model": "removed-alias",
        },
    )
    assert first["terminal_event"]["event"] == "done"
    second = await chat_turn(
        app,
        {
            "vault_name": vault.name,
            "prompt": "Continue briefly.",
            "session_id": "alias-fallback-session",
            "model": "historical-test",
        },
    )
    assert second["terminal_event"]["event"] == "done"
    original = app.configuration()
    ambiguous = deepcopy(original)
    ambiguous["models"]["sonnet"]["alternate_aliases"] = ["ambiguous"]
    ambiguous["models"]["opus"]["alternate_aliases"] = ["ambiguous"]
    install_legacy_models(app, ambiguous)
    try:
        assert resolve_chat_model_alias("ambiguous") == "test"
    finally:
        app.install_configuration(original)
    unavailable = await chat_turn(
        app,
        {
            "vault_name": vault.name,
            "prompt": "Do not dispatch without credentials.",
            "session_id": "alias-provider-failure",
            "model": "sonnet",
        },
    )
    assert unavailable["terminal_event"]["event"] == "error"
    activity = app.request("/api/system/activity-log", params={"limit": 1000})
    assert activity.status_code == 200
    entries = activity.json()["entries"]
    assert any(
        row.get("data", {}).get("event") == "chat_model_fallback" for row in entries
    )
    assert not any(
        row.get("data", {}).get("event") == "chat_model_fallback"
        and row["data"].get("session_id") == "alias-provider-failure"
        for row in entries
    )
    invalid = app.configuration()
    invalid["settings"]["default_model"]["value"] = "missing-default"
    app.install_configuration(invalid)
    status = app.request("/api/status").json()["configuration_status"]
    assert any(
        issue["name"] == "setting:default_model" and issue["severity"] == "error"
        for issue in status["issues"]
    ), json.dumps(status)
    with pytest.raises(UnknownModelAlias):
        resolve_chat_model_alias("removed-alias")
