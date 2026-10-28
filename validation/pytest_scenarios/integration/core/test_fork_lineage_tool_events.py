from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.fork_lineage_inputs import (
    _assistant,
    _user,
)

pytestmark = pytest.mark.scenario


async def test_reused_tool_event_lineage(app: Application) -> None:
    vault = app.vault("ForkLineageVault")
    await app.start()
    from core.identity import LOCAL_USER_PRINCIPAL_ID
    from core.runtime.state import get_runtime_context

    store = get_runtime_context().chat_store
    vault_name = vault.name
    source_id = "fork-reused-tool-id-source"
    child_id = "fork-reused-tool-id-child"
    store.ensure_session(
        source_id, vault_name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
    )
    for label in ("past", "future"):
        calls = [ToolCallPart(tool_name="probe", args={}, tool_call_id="reused")]
        returns = [
            ToolReturnPart(
                tool_name="probe",
                tool_call_id="reused",
                content=f"{label} canonical result",
            )
        ]
        if label == "past":
            calls.append(
                ToolCallPart(tool_name="probe", args={}, tool_call_id="unique")
            )
            returns.append(
                ToolReturnPart(
                    tool_name="probe",
                    tool_call_id="unique",
                    content="Unique canonical result",
                )
            )
        store.add_messages(
            source_id,
            vault_name,
            [
                _user(label),
                ModelResponse(parts=calls),
                ModelRequest(parts=returns),
                _assistant(f"{label} answer"),
            ],
        )
        store.add_tool_event(
            session_id=source_id,
            vault_name=vault_name,
            tool_call_id="reused",
            tool_name="probe",
            event_type="call",
            args={"label": label},
        )
        store.add_tool_event(
            session_id=source_id,
            vault_name=vault_name,
            tool_call_id="reused",
            tool_name="probe",
            event_type="result",
            result_text=f"{label} structured result",
        )
    store.add_tool_event(
        session_id=source_id,
        vault_name=vault_name,
        tool_call_id="unique",
        tool_name="probe",
        event_type="call",
        args={"label": "unique"},
    )
    store.add_tool_event(
        session_id=source_id,
        vault_name=vault_name,
        tool_call_id="unique",
        tool_name="probe",
        event_type="result",
        result_text="Unique structured result",
    )
    source_prefix = store.get_stored_messages_range(
        source_id, vault_name, after_sequence_index=-1, through_sequence_index=3
    )
    store.fork_session(
        source_session_id=source_id,
        new_session_id=child_id,
        vault_name=vault_name,
        through_sequence_index=3,
        title="Past invocation only",
    )
    assert store.get_history(child_id, vault_name, mode="raw") == [
        message.message for message in source_prefix
    ], "Ambiguous structured details must not prevent copying canonical tool history"
    assert (
        store.get_tool_events_for_call(child_id, vault_name, "reused") == []
    ), "Reused tool IDs must not carry future or ambiguous invocation details into a fork"
    assert (
        len(store.get_tool_events(source_id, vault_name)) == 6
    ), "Omitting ambiguous child details must preserve all parent event records"
    assert (
        store.get_session_metadata(child_id, vault_name)["fork"][
            "copied_tool_event_count"
        ]
        == 2
    ), "Fork lineage should report only events that were safely inherited"
    assert store.get_tool_events(
        child_id, vault_name
    ) == store.get_tool_events_for_call(
        source_id, vault_name, "unique"
    ), "Unambiguous invocation details should remain intact in the child"
    store.add_tool_event(
        session_id=source_id,
        vault_name=vault_name,
        tool_call_id="unique",
        tool_name="probe",
        event_type="call",
        args={"label": "uncommitted future invocation"},
    )
    store.add_tool_event(
        session_id=source_id,
        vault_name=vault_name,
        tool_call_id="unique",
        tool_name="probe",
        event_type="result",
        result_text="Uncommitted future result",
    )
    uncommitted_child_id = "fork-uncommitted-reused-tool-child"
    store.fork_session(
        source_session_id=source_id,
        new_session_id=uncommitted_child_id,
        vault_name=vault_name,
        through_sequence_index=3,
        title="Exclude uncommitted reused invocation",
    )
    assert (
        store.get_tool_events(uncommitted_child_id, vault_name) == []
    ), "Reused event IDs must fail closed even when the future invocation has no canonical declaration"


async def test_tool_event_group_lineage(app: Application) -> None:
    vault = app.vault("ForkLineageVault")
    await app.start()
    from core.chat.chat_store import (
        tool_call_events_are_unambiguous,
    )
    from core.identity import LOCAL_USER_PRINCIPAL_ID
    from core.runtime.state import get_runtime_context

    store = get_runtime_context().chat_store
    vault_name = vault.name
    source_id = "fork-tool-event-groups-source"
    child_id = "fork-tool-event-groups-child"
    event_groups: dict[str, tuple[str, ...]] = {
        "interrupted": ("call",),
        "completed": ("call", "result"),
        "cached": ("call", "overflow_cached"),
        "missing": (),
        "orphan-result": ("result",),
        "orphan-cached": ("overflow_cached",),
        "result-before-call": ("result", "call"),
        "cached-before-call": ("overflow_cached", "call"),
        "duplicate-call": ("call", "call", "result"),
        "duplicate-result": ("call", "result", "result"),
        "duplicate-cached": ("call", "overflow_cached", "overflow_cached"),
        "mixed-terminals": ("call", "result", "overflow_cached"),
        "unknown-event": ("call", "unknown"),
        "mismatched-result-name": ("call", "result"),
        "mismatched-cached-name": ("call", "overflow_cached"),
    }
    safe_ids = {"interrupted", "completed", "cached"}
    store.ensure_session(
        source_id, vault_name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
    )
    canonical_messages = [
        _user("Check ordered tool event groups"),
        ModelResponse(
            parts=[
                ToolCallPart(tool_name="probe", args={}, tool_call_id=call_id)
                for call_id in event_groups
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="probe",
                    tool_call_id=call_id,
                    content=f"{call_id} canonical result",
                )
                for call_id in event_groups
            ]
        ),
        _assistant("All canonical tool results are present"),
    ]
    store.add_messages(source_id, vault_name, canonical_messages)
    for call_id, event_types in event_groups.items():
        for index, event_type in enumerate(event_types):
            store.add_tool_event(
                session_id=source_id,
                vault_name=vault_name,
                tool_call_id=call_id,
                tool_name=(
                    "different"
                    if call_id.startswith("mismatched-") and event_type != "call"
                    else "probe"
                ),
                event_type=event_type,
                result_text=(
                    None
                    if event_type == "call"
                    else f"{call_id} diagnostic result {index}"
                ),
            )
        response = app.request(
            f"/api/chat/sessions/{source_id}/tools/{call_id}?vault_name={vault_name}"
        )
        assert response.status_code == (
            200 if call_id in safe_ids else 404
        ), f"Tool details should expose only ordered unambiguous events: {call_id}"
    source_events = store.get_tool_events(source_id, vault_name)
    completed_events = store.get_tool_events_for_call(
        source_id, vault_name, "completed"
    )
    assert not tool_call_events_are_unambiguous(
        [completed_events[0], replace(completed_events[1], tool_call_id="different")]
    ), "The shared event-group boundary must reject mixed invocation IDs"
    store.fork_session(
        source_session_id=source_id,
        new_session_id=child_id,
        vault_name=vault_name,
        through_sequence_index=3,
        title="Ordered event groups",
    )
    assert (
        store.get_history(child_id, vault_name, mode="raw") == canonical_messages
    ), "Withholding ambiguous event groups must preserve complete canonical tool history"
    assert store.get_tool_events(child_id, vault_name) == [
        event for event in source_events if event.tool_call_id in safe_ids
    ], "Forks should inherit call-only, completed, and cached groups and omit every ambiguous group"
    assert (
        store.get_session_metadata(child_id, vault_name)["fork"][
            "copied_tool_event_count"
        ]
        == 5
    ), "Fork lineage should count only the five events in safe groups"
    assert (
        store.get_tool_events(source_id, vault_name) == source_events
    ), "Filtering fork event groups must leave all source diagnostics intact"
    for session_id in (source_id, child_id):
        response = app.request(
            f"/api/chat/sessions/{session_id}?vault_name={vault_name}"
        )
        assert response.status_code == 200, response.text
        assert {
            item["tool_call_id"] for item in response.json()["tool_calls"]
        } == safe_ids, "Session summaries and fork inheritance should share ordered event eligibility"
