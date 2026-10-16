"""Provider-shaped tool replies shared by integrity and admission scenarios."""

from dataclasses import dataclass

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)


@dataclass(frozen=True)
class ToolReplyCase:
    name: str
    messages: list[ModelMessage]
    issue_codes: frozenset[str]
    call_count: int
    return_count: int


def tool_reply_cases() -> tuple[ToolReplyCase, ...]:
    """Exercise retry replies and exact opaque invocation identities."""
    first_call = ModelResponse(parts=[ToolCallPart("probe", {}, "first")])
    retry = ModelRequest(
        parts=[
            RetryPromptPart("Retry this tool", tool_name="probe", tool_call_id="first")
        ]
    )
    corrected_call = ModelResponse(parts=[ToolCallPart("probe", {}, "corrected")])
    corrected_return = ModelRequest(
        parts=[ToolReturnPart("probe", "Succeeded", "corrected")]
    )
    cases = [
        (
            "valid-retry",
            [first_call, retry, corrected_call, corrected_return],
            set(),
            2,
            1,
        ),
        (
            "valid-mixed-batch",
            [
                ModelResponse(
                    parts=[
                        ToolCallPart("alpha", {}, "alpha"),
                        ToolCallPart("probe", {}, "first"),
                    ]
                ),
                ModelRequest(
                    parts=[ToolReturnPart("alpha", "Done", "alpha"), *retry.parts]
                ),
                corrected_call,
                corrected_return,
            ],
            set(),
            3,
            2,
        ),
        (
            "valid-opaque-ids",
            [
                ModelResponse(
                    parts=[
                        ToolCallPart("probe", {}, "id"),
                        ToolCallPart("probe", {}, "id "),
                    ]
                ),
                ModelRequest(
                    parts=[
                        ToolReturnPart("probe", "Done", "id"),
                        ToolReturnPart("probe", "Done", "id "),
                    ]
                ),
            ],
            set(),
            2,
            2,
        ),
        (
            "valid-split-return-batch",
            [
                ModelResponse(
                    parts=[
                        ToolCallPart("probe", {}, "first"),
                        ToolCallPart("probe", {}, "second"),
                        ToolCallPart("probe", {}, "third"),
                    ]
                ),
                ModelRequest(parts=[ToolReturnPart("probe", "Done", "first")]),
                ModelRequest(
                    parts=[
                        ToolReturnPart("probe", "Done", "second"),
                        ToolReturnPart("probe", "Done", "third"),
                    ]
                ),
            ],
            set(),
            3,
            3,
        ),
        (
            "valid-output-retry",
            [ModelRequest(parts=[RetryPromptPart("Fix output")])],
            set(),
            0,
            0,
        ),
        ("orphan-retry", [retry], {"orphan_tool_return"}, 0, 0),
        (
            "duplicate-mixed-replies",
            [
                first_call,
                ModelRequest(
                    parts=[*retry.parts, ToolReturnPart("probe", "Done", "first")]
                ),
            ],
            {"duplicate_tool_return_in_message", "orphan_tool_return"},
            1,
            1,
        ),
        (
            "mismatched-retry-name",
            [
                first_call,
                ModelRequest(
                    parts=[
                        RetryPromptPart(
                            "Retry", tool_name="other", tool_call_id="first"
                        )
                    ]
                ),
            ],
            {"tool_return_name_mismatch", "orphan_tool_call"},
            1,
            0,
        ),
    ]
    for label, reply_id in (
        ("blank", ""),
        ("whitespace", " \t "),
        ("distinct", "first "),
    ):
        for reply_kind in ("retry", "return"):
            reply = (
                RetryPromptPart("Retry", tool_name="probe", tool_call_id=reply_id)
                if reply_kind == "retry"
                else ToolReturnPart("probe", "Done", reply_id)
            )
            codes = {
                "orphan_tool_call",
                (
                    "missing_tool_return_id"
                    if label != "distinct"
                    else "orphan_tool_return"
                ),
            }
            cases.append(
                (
                    f"{label}-{reply_kind}-id",
                    [first_call, ModelRequest(parts=[reply])],
                    codes,
                    1,
                    int(reply_kind == "return"),
                )
            )
    return tuple(
        ToolReplyCase(
            name=name,
            messages=[
                ModelRequest(parts=[UserPromptPart("Use the tools")]),
                *messages,
                ModelResponse(parts=[TextPart("Finished")]),
                ModelRequest(parts=[UserPromptPart("Latest turn")]),
                ModelResponse(parts=[TextPart("Latest answer")]),
            ],
            issue_codes=frozenset(codes),
            call_count=call_count,
            return_count=return_count,
        )
        for name, messages, codes, call_count, return_count in cases
    )
