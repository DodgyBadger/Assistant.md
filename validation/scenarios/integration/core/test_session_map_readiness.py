"""Fail-closed compaction readiness from real settings and canonical history."""

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.session_map_readiness import (
    migrate_retired_compaction_settings,
    readiness_messages,
    session_readiness,
    set_invalid_readiness_setting,
    set_readiness_setting,
)

pytestmark = pytest.mark.scenario


async def test_session_map_readiness_fails_closed_for_invalid_settings(
    app: Application,
) -> None:

    from core.chat.compaction import build_compaction_summary_message
    from core.identity import LOCAL_USER_PRINCIPAL_ID
    from core.runtime.state import get_runtime_context

    vault = app.vault("SessionMapReadinessVault")
    await app.start()
    decision_fixture = app.request(
        "/api/system/models/validation-decision",
        method="PUT",
        data={
            "provider": "openai",
            "model_string": "validation-decision",
            "capabilities": ["decision"],
        },
    )
    assert decision_fixture.status_code == 200, decision_fixture.text
    store = get_runtime_context().chat_store
    canonical_session = "session-map-ready-canonical"
    store.ensure_session(
        canonical_session, vault.name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
    )
    messages = readiness_messages()
    store.add_messages(canonical_session, vault.name, messages)
    settings_response = app.request("/api/system/settings/general")
    assert settings_response.status_code == 200
    settings = {item["key"]: item for item in settings_response.json()}
    assert settings["compaction_strategy"]["value"] == "session_map"
    assert settings["compaction_author_model"]["value"] == "none"
    assert settings["compaction_retained_turns"]["value"] == "3"
    set_readiness_setting(app, "default_model", "test")
    initial = session_readiness(
        app, session_id=canonical_session, vault_name=vault.name
    )
    assert (initial.enabled, initial.reason) == (True, "ready_canonical_history")
    legacy_update = app.request(
        "/api/system/settings/general/compaction_strategy",
        method="PUT",
        data={"value": "stepped_session_map"},
    )
    assert (legacy_update.status_code, legacy_update.json().get("value")) == (
        200,
        "session_map",
    )
    invalid_strategy = app.request(
        "/api/system/settings/general/compaction_strategy",
        method="PUT",
        data={"value": "unknown"},
    )
    assert invalid_strategy.status_code == 400
    upgraded_settings = migrate_retired_compaction_settings(app)
    assert {
        key: upgraded_settings["settings"][key]["value"]
        for key in (
            "compaction_strategy",
            "compaction_author_model",
            "compaction_author_thinking",
            "compaction_low_watermark_tokens",
            "compaction_retained_turns",
            "compaction_high_watermark_tokens",
        )
    } == {
        "compaction_strategy": "session_map",
        "compaction_author_model": "legacy-author",
        "compaction_author_thinking": "medium",
        "compaction_low_watermark_tokens": 1234,
        "compaction_retained_turns": 4,
        "compaction_high_watermark_tokens": 5678,
    }
    for key, value in (
        ("compaction_strategy", "session_map"),
        ("default_model", "test"),
        ("compaction_author_thinking", "low"),
        ("compaction_low_watermark_tokens", "50"),
        ("compaction_retained_turns", "3"),
        ("compaction_high_watermark_tokens", "100"),
        ("compaction_type", "auto"),
    ):
        response = app.request(
            f"/api/system/settings/general/{key}", method="PUT", data={"value": value}
        )
        assert response.status_code == 200
    ready = session_readiness(app, session_id=canonical_session, vault_name=vault.name)
    assert (
        ready.enabled,
        ready.reason,
        ready.author_model,
        ready.author_thinking,
        ready.high_watermark_tokens,
        ready.low_watermark_tokens,
        ready.minimum_retained_groups,
    ) == (True, "ready_canonical_history", "test", "low", 100, 50, 3)
    invalid_floor = app.request(
        "/api/system/settings/general/compaction_retained_turns",
        method="PUT",
        data={"value": "0"},
    )
    assert invalid_floor.status_code == 400
    set_invalid_readiness_setting(app, "compaction_author_model", "validation-decision")
    decision_only = session_readiness(
        app, session_id=canonical_session, vault_name=vault.name
    )
    assert (decision_only.enabled, decision_only.reason) == (
        False,
        "author_model_not_text_capable",
    )
    set_readiness_setting(app, "compaction_author_model", "test")
    set_invalid_readiness_setting(app, "compaction_author_thinking", "impossible")
    invalid_thinking = session_readiness(
        app, session_id=canonical_session, vault_name=vault.name
    )
    assert (invalid_thinking.enabled, invalid_thinking.reason) == (
        False,
        "author_thinking_invalid",
    )
    set_readiness_setting(app, "compaction_author_thinking", "low")
    set_invalid_readiness_setting(app, "compaction_low_watermark_tokens", "100")
    invalid_watermarks = session_readiness(
        app, session_id=canonical_session, vault_name=vault.name
    )
    assert (invalid_watermarks.enabled, invalid_watermarks.reason) == (
        False,
        "invalid_watermarks",
    )
    set_readiness_setting(app, "compaction_low_watermark_tokens", "50")
    set_readiness_setting(app, "compaction_type", "suggested")
    manual_only = session_readiness(
        app, session_id=canonical_session, vault_name=vault.name
    )
    assert (manual_only.enabled, manual_only.reason) == (
        True,
        "ready_canonical_history",
    )
    set_readiness_setting(app, "compaction_type", "auto")
    compacted_session = "session-map-ready-recovery-card"
    store.ensure_session(
        compacted_session, vault.name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
    )
    store.add_messages(compacted_session, vault.name, messages)
    summary = build_compaction_summary_message("Existing recovery state.")
    store.add_compaction_checkpoint(
        session_id=compacted_session,
        vault_name=vault.name,
        checkpoint_id="existing-recovery-card",
        source="validation",
        message_count_before=2,
        last_message_sequence_index=1,
        summary_message=summary,
        replacement_history=[summary],
    )
    recovery_card = session_readiness(
        app, session_id=compacted_session, vault_name=vault.name
    )
    assert (recovery_card.enabled, recovery_card.reason, recovery_card.strategy) == (
        False,
        "recovery_card_checkpoint_present",
        "recovery_card",
    )
