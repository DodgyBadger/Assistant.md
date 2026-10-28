"""Explicit provider-history inputs and integrity oracles, not generated conversations."""

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    NativeToolCallPart,
    NativeToolReturnPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from validation.support.tool_history_fixtures import tool_reply_cases


def assert_input_integrity() -> None:
    from core.chat.tool_history import analyze_tool_history

    for case in tool_reply_cases():
        integrity = analyze_tool_history(case.messages)
        assert {issue.code for issue in integrity.issues} == case.issue_codes, case.name
        assert integrity.ok == (not case.issue_codes), case.name
        assert (integrity.tool_call_count, integrity.tool_return_count) == (
            case.call_count,
            case.return_count,
        ), case.name
        if case.name == "valid-mixed-batch":
            assert integrity.multi_call_batch_count == 1
            assert integrity.multi_return_batch_count == 0
    for tool_call_id in ("", " \t "):
        integrity = analyze_tool_history(
            [
                ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name="probe", args={}, tool_call_id=tool_call_id
                        )
                    ]
                ),
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            tool_name="probe",
                            content="result",
                            tool_call_id=tool_call_id,
                        )
                    ]
                ),
            ]
        )
        assert {issue.code for issue in integrity.issues} == {
            "missing_tool_call_id",
            "missing_tool_return_id",
        }
        assert (integrity.tool_call_count, integrity.tool_return_count) == (1, 1)
        assert all(issue.severity == "error" for issue in integrity.issues)
    mismatched = analyze_tool_history(
        [
            ModelResponse(
                parts=[ToolCallPart(tool_name="alpha", args={}, tool_call_id="id")]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="beta", content="result", tool_call_id="id"
                    )
                ]
            ),
        ]
    )
    assert [issue.code for issue in mismatched.issues] == ["tool_return_name_mismatch"]
    assert mismatched.issues[0].message_index == 1
    assert mismatched.issues[0].tool_call_id == "id"
    assert not mismatched.ok
    assert analyze_tool_history(_batch_history()).ok
    assert analyze_tool_history(
        [
            ModelResponse(
                parts=[
                    NativeToolCallPart(
                        tool_name="web_search", args={}, tool_call_id="native-id"
                    ),
                    NativeToolReturnPart(
                        tool_name="web_search",
                        content="native result",
                        tool_call_id="native-id",
                    ),
                ]
            )
        ]
    ).ok


def _batch_history():
    return [
        ModelRequest(parts=[UserPromptPart(content="Use two tools.")]),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="source_lookup",
                    args={"path": "sources/a.md"},
                    tool_call_id="call-a",
                ),
                ToolCallPart(
                    tool_name="source_lookup",
                    args={"path": "sources/b.md"},
                    tool_call_id="call-b",
                ),
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="source_lookup",
                    content="Source A notes.",
                    tool_call_id="call-a",
                ),
                ToolReturnPart(
                    tool_name="source_lookup",
                    content="Source B notes.",
                    tool_call_id="call-b",
                ),
            ]
        ),
        ModelResponse(parts=[TextPart(content="Both sources reviewed.")]),
    ]


def _orphan_return_history():
    return [
        ModelRequest(parts=[UserPromptPart(content="Continue.")]),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="source_lookup",
                    content="No matching call.",
                    tool_call_id="missing-call",
                )
            ]
        ),
    ]


TOOL_HISTORY_BATCH_CODE = """
history_payload = await retrieve_history(scope="session", limit="all")
assembled = await assemble_context(history=history_payload.items)
batch = history_payload.items[1]
batch_item_indexes = []
for index, item in enumerate(history_payload.items):
    try:
        item.exchanges
        batch_item_indexes.append(index)
    except AttributeError:
        pass

assembled_batch_role_seen = False
for message in assembled.messages:
    try:
        message.exchanges
        assembled_batch_role_seen = True
    except AttributeError:
        pass

{
    "batch_item_indexes": batch_item_indexes,
    "exchange_count": len(batch.exchanges),
    "integrity": history_payload.metadata["tool_history_integrity"],
    "assembled_batch_role_seen": assembled_batch_role_seen,
}
"""


TOOL_HISTORY_ORPHAN_CODE = """
history_payload = await retrieve_history(scope="session", limit="all")
integrity = history_payload.metadata["tool_history_integrity"]
{
    "status": integrity["status"],
    "issue_codes": [issue["code"] for issue in integrity["issues"]],
}
"""
