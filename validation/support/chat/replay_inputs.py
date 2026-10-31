"""Explicit canonical message and tool-event inputs for replay journeys."""


def seed_bracket_history(store, vault, bracket_session_id, quote_prompt, link_prompt):
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    from core.identity import LOCAL_USER_PRINCIPAL_ID

    store.ensure_session(
        bracket_session_id, vault.name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
    )
    store.add_messages(
        bracket_session_id,
        vault.name,
        [
            ModelRequest(parts=[UserPromptPart(content=quote_prompt)]),
            ModelResponse(parts=[ToolCallPart("probe", {}, "bracket-probe")]),
            ModelRequest(
                parts=[ToolReturnPart("probe", "Private tool result", "bracket-probe")]
            ),
            ModelResponse(parts=[TextPart("The quoted passage means this.")]),
            ModelRequest(parts=[UserPromptPart(content=link_prompt)]),
            ModelResponse(parts=[TextPart("The link explains this.")]),
        ],
    )


def seed_missing_id_history(store, vault, missing_id_session):
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        NativeToolReturnPart,
        RetryPromptPart,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    from core.identity import LOCAL_USER_PRINCIPAL_ID

    store.ensure_session(
        missing_id_session, vault.name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
    )
    store.add_messages(
        missing_id_session,
        vault.name,
        [
            ModelRequest(parts=[UserPromptPart(content="Probe tool display.")]),
            ModelResponse(parts=[ToolCallPart("probe", {}, "")]),
            ModelRequest(parts=[ToolReturnPart("probe", "Private result", "")]),
            ModelResponse(
                parts=[NativeToolReturnPart("probe", "Private native result", "")]
            ),
            ModelRequest(
                parts=[
                    RetryPromptPart("Private retry", tool_name="probe", tool_call_id="")
                ]
            ),
            ModelResponse(parts=[TextPart("Visible answer.")]),
        ],
    )


def seed_committed_tool_history(store, vault, session_id):
    from datetime import UTC, datetime

    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    from core.identity import LOCAL_USER_PRINCIPAL_ID

    store.ensure_session(
        session_id, vault.name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
    )
    store.add_messages(
        session_id,
        vault.name,
        [
            ModelRequest(parts=[UserPromptPart(content="Use both probe tools.")]),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="probe_alpha",
                        args={"path": "A.md"},
                        tool_call_id="probe-a",
                    ),
                    ToolCallPart(
                        tool_name="probe_beta",
                        args={"path": "B.md"},
                        tool_call_id="probe-b",
                    ),
                    ToolCallPart(
                        tool_name="probe_gamma",
                        args={"path": "C.md"},
                        tool_call_id="probe-c",
                    ),
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="probe_alpha",
                        content="alpha result",
                        tool_call_id="probe-a",
                    ),
                    ToolReturnPart(
                        tool_name="probe_beta",
                        content="beta result",
                        tool_call_id="probe-b",
                    ),
                    ToolReturnPart(
                        tool_name="probe_gamma",
                        content="gamma interrupted",
                        tool_call_id="probe-c",
                    ),
                ]
            ),
            ModelResponse(parts=[TextPart(content="Both probes completed.")]),
        ],
    )
    store.add_tool_event(
        session_id=session_id,
        vault_name=vault.name,
        tool_call_id="probe-c",
        tool_name="probe_gamma",
        event_type="call",
        args={"path": "C.md"},
    )
    store.add_tool_event(
        session_id=session_id,
        vault_name=vault.name,
        tool_call_id="probe-c",
        tool_name="probe_gamma",
        event_type="result",
        result_text="gamma interrupted",
        result_metadata={"status": "cancelled", "token_count": 4},
    )
    store.add_tool_event(
        session_id=session_id,
        vault_name=vault.name,
        tool_call_id="cancelled-call",
        tool_name="probe_cancelled",
        event_type="call",
        args={"path": "cancelled.md"},
    )
    store.add_tool_event(
        session_id=session_id,
        vault_name=vault.name,
        tool_call_id="probe-b",
        tool_name="probe_beta",
        event_type="call",
        args={"path": "B.md"},
    )
    store.add_tool_event(
        session_id=session_id,
        vault_name=vault.name,
        tool_call_id="probe-b",
        tool_name="probe_beta",
        event_type="result",
        result_text="beta result",
        result_metadata={
            "status": "denied",
            "failure_kind": "probe_failure",
            "token_count": 2,
        },
    )
    store.add_tool_event(
        session_id=session_id,
        vault_name=vault.name,
        tool_call_id="probe-a",
        tool_name="probe_alpha",
        event_type="call",
        args={"path": "A.md"},
    )
    store.add_tool_event(
        session_id=session_id,
        vault_name=vault.name,
        tool_call_id="probe-a",
        tool_name="probe_alpha",
        event_type="result",
        result_text="alpha result",
        result_metadata={
            "fetched_at": datetime(2026, 8, 9, 1, 2, tzinfo=UTC),
            "token_count": 3,
        },
    )


def seed_deferred_collision(store, vault, session_id):
    from pydantic_ai.messages import (
        ModelResponse,
        ToolCallPart,
    )

    store.add_messages(
        session_id,
        vault.name,
        [
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="probe_deferred",
                        args={"path": "deferred-generation.md"},
                        tool_call_id="probe-a",
                    )
                ]
            )
        ],
    )


def seed_reused_call(store, vault, session_id):
    from pydantic_ai.messages import (
        ModelResponse,
        ToolCallPart,
    )

    store.add_messages(
        session_id,
        vault.name,
        [
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="probe_reused",
                        args={"path": "new-generation.md"},
                        tool_call_id="probe-a",
                    )
                ]
            )
        ],
    )


def seed_reused_result(store, vault, session_id):
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        ToolReturnPart,
    )

    store.add_messages(
        session_id,
        vault.name,
        [
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="probe_reused",
                        content="new generation result",
                        tool_call_id="probe-a",
                    )
                ]
            ),
            ModelResponse(parts=[TextPart(content="Reused probe completed.")]),
        ],
    )
    store.add_tool_event(
        session_id=session_id,
        vault_name=vault.name,
        tool_call_id="probe-a",
        tool_name="probe_reused",
        event_type="call",
        args={"path": "new-generation.md"},
    )
    store.add_tool_event(
        session_id=session_id,
        vault_name=vault.name,
        tool_call_id="probe-a",
        tool_name="probe_reused",
        event_type="result",
        result_text="new generation result",
    )


def seed_duplicate_events(store, vault, duplicate_event_session):
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        ToolCallPart,
        ToolReturnPart,
    )

    from core.identity import LOCAL_USER_PRINCIPAL_ID

    store.ensure_session(
        duplicate_event_session, vault.name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
    )
    store.add_messages(
        duplicate_event_session,
        vault.name,
        [
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="probe_duplicate_event",
                        args={"path": "single-declaration.md"},
                        tool_call_id="duplicate-event-call",
                    )
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="probe_duplicate_event",
                        content="duplicate event result",
                        tool_call_id="duplicate-event-call",
                    )
                ]
            ),
        ],
    )
    for path in ("first-event.md", "second-event.md"):
        store.add_tool_event(
            session_id=duplicate_event_session,
            vault_name=vault.name,
            tool_call_id="duplicate-event-call",
            tool_name="probe_duplicate_event",
            event_type="call",
            args={"path": path},
        )
    store.add_tool_event(
        session_id=duplicate_event_session,
        vault_name=vault.name,
        tool_call_id="duplicate-event-call",
        tool_name="probe_duplicate_event",
        event_type="result",
        result_text="duplicate event result",
    )
