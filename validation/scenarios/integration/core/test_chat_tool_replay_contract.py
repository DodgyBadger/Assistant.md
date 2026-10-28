"""Persisted tool replay and privacy through real session APIs."""

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.replay_inputs import (
    seed_bracket_history,
    seed_committed_tool_history,
    seed_deferred_collision,
    seed_duplicate_events,
    seed_missing_id_history,
    seed_reused_call,
    seed_reused_result,
)

pytestmark = pytest.mark.scenario


async def test_bracket_prompts_and_tool_content_privacy(app: Application) -> None:
    for secret in (
        "Private tool result",
        "Private result",
        "Private native result",
        "Private retry",
    ):
        app.evidence.register_secret(secret)
    vault = app.vault("ChatToolReplayContractVault")
    await app.start()
    from core.chat.chat_store import ChatStore
    from core.runtime.state import get_runtime_context

    store = ChatStore(system_root=str(get_runtime_context().config.system_root))
    bracket_session_id = "bracketed_user_prompts"
    quote_prompt = "[[Notes/Draft.md]]\n\n> Selected text\n\nExplain this passage."
    link_prompt = "[Reference](https://example.com)\n\nExplain this link."
    seed_bracket_history(store, vault, bracket_session_id, quote_prompt, link_prompt)
    bracket_messages = store.get_stored_messages(bracket_session_id, vault.name)
    assert [
        message.content_text
        for message in bracket_messages
        if message.role == "user" and (not message.tool_return_ids)
    ] == [quote_prompt, link_prompt]
    boundaries, has_older = store.get_canonical_display_rows_before(
        bracket_session_id,
        vault.name,
        through_sequence_index=5,
        before_sequence_index=None,
        limit=10,
    )
    assert boundaries == [
        (0, 0, "message"),
        (1, 3, "message"),
        (4, 4, "message"),
        (5, 5, "message"),
    ]
    assert not has_older
    bracket_detail = app.request(
        f"/api/chat/sessions/{bracket_session_id}?vault_name={vault.name}"
    )
    assert bracket_detail.status_code == 200
    bracket_payloads = bracket_detail.json()["messages"]
    assert [
        message["content"]
        for message in bracket_payloads
        if message["role"] == "user" and (not message["is_tool_message"])
    ] == [quote_prompt, link_prompt]
    assert [
        message["content"] for message in bracket_payloads if message["is_tool_message"]
    ] == ["", ""]
    timeline = app.request(
        f"/api/chat/sessions/{bracket_session_id}/timeline?vault_name={vault.name}"
    )
    assert timeline.status_code == 200
    if timeline.status_code == 200:
        assert [
            message["content"]
            for message in timeline.json()["messages"]
            if message["role"] == "user"
        ] == [quote_prompt, link_prompt]


async def test_tool_parts_without_ids_remain_hidden(app: Application) -> None:
    for secret in (
        "Private tool result",
        "Private result",
        "Private native result",
        "Private retry",
    ):
        app.evidence.register_secret(secret)
    vault = app.vault("ChatToolReplayContractVault")
    await app.start()
    from core.chat.chat_store import ChatStore
    from core.runtime.state import get_runtime_context

    store = ChatStore(system_root=str(get_runtime_context().config.system_root))
    missing_id_session = "tool_parts_without_ids"
    seed_missing_id_history(store, vault, missing_id_session)
    missing_id_detail = app.request(
        f"/api/chat/sessions/{missing_id_session}?vault_name={vault.name}"
    )
    assert missing_id_detail.status_code == 200
    assert [
        (message["is_tool_message"], message["content"])
        for message in missing_id_detail.json()["messages"]
    ] == [
        (False, "Probe tool display."),
        (True, ""),
        (True, ""),
        (True, ""),
        (True, ""),
        (False, "Visible answer."),
    ]
    missing_id_boundaries, _ = store.get_canonical_display_rows_before(
        missing_id_session,
        vault.name,
        through_sequence_index=5,
        before_sequence_index=None,
        limit=10,
    )
    assert missing_id_boundaries == [(0, 0, "message"), (1, 5, "message")]


async def test_committed_replay_and_compaction_collisions(app: Application) -> None:
    for secret in (
        "Private tool result",
        "Private result",
        "Private native result",
        "Private retry",
    ):
        app.evidence.register_secret(secret)
    vault = app.vault("ChatToolReplayContractVault")
    await app.start()
    from core.chat.chat_store import ChatStore
    from core.runtime.state import get_runtime_context

    store = ChatStore(system_root=str(get_runtime_context().config.system_root))
    from core.chat.compaction import build_compaction_summary_message

    session_id = "chat_tool_replay_contract_session"
    seed_committed_tool_history(store, vault, session_id)
    detail_response = app.request(
        f"/api/chat/sessions/{session_id}?vault_name={vault.name}"
    )
    assert detail_response.status_code == 200, "Session detail should load"
    detail = detail_response.json()
    message_payloads = detail["messages"]
    tool_call_message = next(
        message
        for message in message_payloads
        if set(message["tool_call_ids"]) == {"probe-a", "probe-b", "probe-c"}
    )
    tool_return_message = next(
        message
        for message in message_payloads
        if set(message["tool_return_ids"]) == {"probe-a", "probe-b", "probe-c"}
    )
    assert tool_call_message["is_tool_message"]
    assert tool_return_message["is_tool_message"]
    assert tool_call_message["tool_call_ids"] == ["probe-a", "probe-b", "probe-c"]
    assert tool_return_message["tool_return_ids"] == ["probe-a", "probe-b", "probe-c"]
    assert tool_call_message["content"] == ""
    assert tool_return_message["content"] == ""
    tool_calls = detail["tool_calls"]
    event_ids = [tool_call["tool_call_id"] for tool_call in tool_calls]
    assert "cancelled-call" not in event_ids
    assert event_ids == ["probe-a", "probe-b", "probe-c"]
    assert tool_calls == [
        {
            "tool_call_id": "probe-a",
            "tool_name": "probe_alpha",
            "status": "completed",
            "token_count": 3,
        },
        {
            "tool_call_id": "probe-b",
            "tool_name": "probe_beta",
            "status": "failed",
            "token_count": 2,
        },
        {
            "tool_call_id": "probe-c",
            "tool_name": "probe_gamma",
            "status": "interrupted",
            "token_count": 4,
        },
    ]
    assert "tool_events" not in detail
    alpha_detail_response = app.request(
        f"/api/chat/sessions/{session_id}/tools/probe-a?vault_name={vault.name}"
    )
    assert alpha_detail_response.status_code == 200
    alpha_detail = alpha_detail_response.json()
    assert alpha_detail["args"] == {"path": "A.md"}
    assert alpha_detail["result_text"] == "alpha result"
    assert alpha_detail["result_metadata"]["fetched_at"] == "2026-08-09T01:02:00Z"
    seed_deferred_collision(store, vault, session_id)
    effective_collision = app.request(
        f"/api/chat/sessions/{session_id}?vault_name={vault.name}"
    )
    assert [
        call["tool_call_id"] for call in effective_collision.json()["tool_calls"]
    ] == ["probe-b", "probe-c"]
    effective_collision_detail = app.request(
        f"/api/chat/sessions/{session_id}/tools/probe-a?vault_name={vault.name}"
    )
    assert effective_collision_detail.status_code == 404
    summary_message = build_compaction_summary_message(
        "The probe work was completed before compaction."
    )
    store.add_compaction_checkpoint(
        session_id=session_id,
        vault_name=vault.name,
        checkpoint_id="tool-replay-compaction",
        source="validation",
        message_count_before=5,
        last_message_sequence_index=4,
        summary_message=summary_message,
        replacement_history=[summary_message],
    )
    compacted_detail = app.request(
        f"/api/chat/sessions/{session_id}?vault_name={vault.name}"
    )
    assert compacted_detail.json()["tool_calls"] == []
    archived_detail = app.request(
        f"/api/chat/sessions/{session_id}/tools/probe-a?vault_name={vault.name}"
    )
    assert archived_detail.status_code == 404
    seed_reused_call(store, vault, session_id)
    deferred_collision_session = app.request(
        f"/api/chat/sessions/{session_id}?vault_name={vault.name}"
    )
    assert deferred_collision_session.json()["tool_calls"] == []
    deferred_collision_detail = app.request(
        f"/api/chat/sessions/{session_id}/tools/probe-a?vault_name={vault.name}"
    )
    assert deferred_collision_detail.status_code == 404
    seed_reused_result(store, vault, session_id)
    collision_session = app.request(
        f"/api/chat/sessions/{session_id}?vault_name={vault.name}"
    )
    assert collision_session.json()["tool_calls"] == []
    collision_detail = app.request(
        f"/api/chat/sessions/{session_id}/tools/probe-a?vault_name={vault.name}"
    )
    assert collision_detail.status_code == 404


async def test_duplicate_event_identity_is_not_replayed(app: Application) -> None:
    for secret in (
        "Private tool result",
        "Private result",
        "Private native result",
        "Private retry",
    ):
        app.evidence.register_secret(secret)
    vault = app.vault("ChatToolReplayContractVault")
    await app.start()
    from core.chat.chat_store import ChatStore
    from core.runtime.state import get_runtime_context

    store = ChatStore(system_root=str(get_runtime_context().config.system_root))
    duplicate_event_session = "chat_tool_duplicate_event_session"
    seed_duplicate_events(store, vault, duplicate_event_session)
    duplicate_event_summary = app.request(
        f"/api/chat/sessions/{duplicate_event_session}?vault_name={vault.name}"
    )
    assert duplicate_event_summary.json()["tool_calls"] == []
    duplicate_event_detail = app.request(
        f"/api/chat/sessions/{duplicate_event_session}/tools/duplicate-event-call?vault_name={vault.name}"
    )
    assert duplicate_event_detail.status_code == 404
