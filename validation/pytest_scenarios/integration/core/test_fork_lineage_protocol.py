from __future__ import annotations

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    NativeToolCallPart,
    NativeToolReturnPart,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.fork_lineage_inputs import (
    _assistant,
    _user,
)
from validation.support.tool_history_fixtures import tool_reply_cases

pytestmark = pytest.mark.scenario


async def test_retry_fork_points(app: Application) -> None:
    vault = app.vault("ForkLineageVault")
    await app.start()
    from core.chat.chat_store import (
        canonical_assistant_fork_points,
    )
    from core.identity import LOCAL_USER_PRINCIPAL_ID
    from core.runtime.state import get_runtime_context

    store = get_runtime_context().chat_store
    vault_name = vault.name
    call = ModelResponse(parts=[ToolCallPart("probe", {}, "first")])
    retry = ModelRequest(
        parts=[RetryPromptPart("Retry", tool_name="probe", tool_call_id="first")]
    )
    wrong_retry = ModelRequest(
        parts=[RetryPromptPart("Retry", tool_name="other", tool_call_id="first")]
    )
    cases: tuple[tuple[str, list[ModelMessage], set[int]], ...] = (
        ("named-retry", [_user("Check"), call, retry, _assistant("Done")], {3}),
        (
            "named-retry-new-call",
            [
                _user("Check"),
                call,
                retry,
                ModelResponse(parts=[ToolCallPart("probe", {}, "corrected")]),
                ModelRequest(parts=[ToolReturnPart("probe", "Done", "corrected")]),
                _assistant("Done"),
            ],
            {5},
        ),
        (
            "output-retry-pending-call",
            [
                _user("Check"),
                call,
                ModelRequest(parts=[RetryPromptPart("Fix output")]),
                _assistant("Done"),
            ],
            set(),
        ),
        (
            "wrong-tool-retry",
            [_user("Check"), call, wrong_retry, _assistant("Done")],
            set(),
        ),
        (
            "earlier-complete-prefix",
            [
                _user("Check"),
                _assistant("Earlier answer"),
                call,
                wrong_retry,
                _assistant("Done"),
            ],
            {1},
        ),
    )
    for label, messages, expected_points in cases:
        source_id = f"fork-retry-{label}"
        store.ensure_session(
            source_id, vault_name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
        )
        store.add_messages(source_id, vault_name, messages)
        raw = store.get_stored_messages(source_id, vault_name, mode="raw")
        assert canonical_assistant_fork_points(raw) == expected_points, label
        candidates = [
            index
            for index, message in enumerate(messages)
            if isinstance(message, ModelResponse)
        ]
        assert (
            store.get_canonical_fork_points_for_sequences(
                source_id, vault_name, candidates
            )
            == expected_points
        ), label
        for boundary in expected_points:
            response = app.request(
                f"/api/chat/sessions/{source_id}/fork",
                method="POST",
                data={"vault_name": vault_name, "through_sequence_index": boundary},
            )
            assert response.status_code == 200, (label, response.text)
            child_id = response.json()["session"]["session_id"]
            assert [
                item.message
                for item in store.get_stored_messages(child_id, vault_name, mode="raw")
            ] == messages[: boundary + 1], label
        final_boundary = len(messages) - 1
        if final_boundary in expected_points:
            continue
        rejected_child_id = f"{source_id}-rejected-child"
        try:
            store.fork_session(
                source_session_id=source_id,
                new_session_id=rejected_child_id,
                vault_name=vault_name,
                through_sequence_index=final_boundary,
                title="Unsafe retry fork",
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"Unsafe retry prefix must not fork: {label}")
        assert store.get_session(rejected_child_id, vault_name) is None, label
        response = app.request(
            f"/api/chat/sessions/{source_id}/fork",
            method="POST",
            data={"vault_name": vault_name, "through_sequence_index": final_boundary},
        )
        assert response.status_code == 400, (label, response.text)


async def test_fork_protocol_parity(app: Application) -> None:
    vault = app.vault("ForkLineageVault")
    await app.start()
    from core.chat.chat_store import (
        canonical_assistant_fork_points,
    )
    from core.chat.tool_history import analyze_tool_history
    from core.identity import LOCAL_USER_PRINCIPAL_ID
    from core.runtime.state import get_runtime_context

    store = get_runtime_context().chat_store
    vault_name = vault.name
    cases = [(case.name, case.messages) for case in tool_reply_cases()]
    call = ModelResponse(parts=[ToolCallPart("probe", {}, "first")])
    reply = ModelRequest(parts=[ToolReturnPart("probe", "Done", "first")])
    native_call = NativeToolCallPart("web_search", {}, "first")
    native_return = NativeToolReturnPart("web_search", "Done", "first")
    cases.extend(
        [
            (
                "wrong-return-name",
                [call, ModelRequest(parts=[ToolReturnPart("other", "Done", "first")])],
            ),
            ("non-adjacent-return", [call, _user("Intervening message"), reply]),
            (
                "non-adjacent-retry",
                [
                    call,
                    _user("Intervening message"),
                    ModelRequest(
                        parts=[
                            RetryPromptPart(
                                "Retry", tool_name="probe", tool_call_id="first"
                            )
                        ]
                    ),
                ],
            ),
            (
                "duplicate-call-batch",
                [ModelResponse(parts=[*call.parts, *call.parts]), reply],
            ),
            ("duplicate-pending-call", [call, call, reply]),
            ("future-pending-call", [call]),
            ("completed-reused-id", [call, reply, call, reply]),
            ("native-tool-pair", [ModelResponse(parts=[native_call, native_return])]),
            (
                "native-return-does-not-close-custom-call",
                [call, ModelResponse(parts=[native_return])],
            ),
            (
                "native-call-does-not-open-custom-invocation",
                [ModelResponse(parts=[native_call]), reply],
            ),
        ]
    )
    for label, tool_call_id in (("blank-call-id", ""), ("whitespace-call-id", " \t ")):
        cases.append(
            (
                label,
                [
                    ModelResponse(parts=[ToolCallPart("probe", {}, tool_call_id)]),
                    ModelRequest(parts=[ToolReturnPart("probe", "Done", tool_call_id)]),
                ],
            )
        )
    for label, protocol_messages in cases:
        source_id = f"fork-parity-{label}"
        messages = [
            _user("Before tool activity"),
            _assistant("Earlier complete answer"),
            *protocol_messages,
            _assistant("Final answer"),
        ]
        store.ensure_session(
            source_id, vault_name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
        )
        store.add_messages(source_id, vault_name, messages)
        candidates = [
            index
            for index, message in enumerate(messages)
            if isinstance(message, ModelResponse)
        ]
        expected = {
            index
            for index in candidates
            if analyze_tool_history(messages[: index + 1]).ok
        }
        raw = store.get_stored_messages(source_id, vault_name, mode="raw")
        assert canonical_assistant_fork_points(raw) == expected, label
        assert (
            store.get_canonical_fork_points_for_sequences(
                source_id, vault_name, candidates
            )
            == expected
        ), label
        for candidate in candidates:
            assert store.get_canonical_fork_points_for_sequences(
                source_id, vault_name, [candidate]
            ) == expected.intersection({candidate}), (label, candidate)
