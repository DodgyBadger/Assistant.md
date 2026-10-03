"""Tool-call history integrity helpers."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from pydantic import TypeAdapter
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)

_MODEL_MESSAGE_ADAPTER: TypeAdapter[ModelMessage] = TypeAdapter(ModelMessage)


@dataclass(frozen=True)
class ToolHistoryIssue:
    """One tool-call history integrity issue."""

    code: str
    severity: str
    tool_call_id: str
    message_index: int
    detail: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation."""
        return asdict(self)


@dataclass(frozen=True)
class ToolHistoryIntegrity:
    """Summary of tool-call history integrity."""

    status: str
    tool_call_count: int
    tool_return_count: int
    multi_call_batch_count: int
    multi_return_batch_count: int
    issues: tuple[ToolHistoryIssue, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        """Return whether no integrity problems were found."""
        return not self.issues

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation."""
        return {
            "status": self.status,
            "tool_call_count": self.tool_call_count,
            "tool_return_count": self.tool_return_count,
            "multi_call_batch_count": self.multi_call_batch_count,
            "multi_return_batch_count": self.multi_return_batch_count,
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class ToolHistoryInvocation:
    """Minimal call or reply identity for provider and stored metadata scans."""

    tool_call_id: str
    tool_name: str | None
    is_retry: bool = False


@dataclass
class ToolHistoryProtocolState:
    """Incrementally validate invocation identity and immediate reply adjacency."""

    pending: dict[str, tuple[int, str | None]] = field(default_factory=dict)
    issues: list[ToolHistoryIssue] = field(default_factory=list)

    def record_message(
        self,
        index: int,
        calls: Sequence[ToolHistoryInvocation],
        replies: Sequence[ToolHistoryInvocation],
    ) -> None:
        """Advance through one message without requiring its content or arguments."""
        tool_calls = [part.tool_call_id for part in calls]
        tool_replies = [part.tool_call_id for part in replies]

        for duplicate_id in _duplicates([item for item in tool_calls if item.strip()]):
            self.issues.append(
                ToolHistoryIssue(
                    code="duplicate_tool_call_in_message",
                    severity="error",
                    tool_call_id=duplicate_id,
                    message_index=index,
                    detail="A single message contains duplicate tool-call ids.",
                )
            )

        for duplicate_id in _duplicates(
            [item for item in tool_replies if item.strip()]
        ):
            self.issues.append(
                ToolHistoryIssue(
                    code="duplicate_tool_return_in_message",
                    severity="error",
                    tool_call_id=duplicate_id,
                    message_index=index,
                    detail="A single message contains duplicate tool-return ids.",
                )
            )

        for call_part, tool_call_id in zip(calls, tool_calls, strict=True):
            if not tool_call_id.strip():
                self.issues.append(
                    ToolHistoryIssue(
                        code="missing_tool_call_id",
                        severity="error",
                        tool_call_id="",
                        message_index=index,
                        detail="A tool call requires a non-empty invocation id.",
                    )
                )
                continue
            if tool_call_id in self.pending:
                self.issues.append(
                    ToolHistoryIssue(
                        code="duplicate_unreturned_tool_call",
                        severity="error",
                        tool_call_id=tool_call_id,
                        message_index=index,
                        detail="A tool-call id was reused before its prior call returned.",
                    )
                )
            self.pending[tool_call_id] = (index, call_part.tool_name)

        for return_part, tool_call_id in zip(replies, tool_replies, strict=True):
            if not tool_call_id.strip():
                self.issues.append(
                    ToolHistoryIssue(
                        code="missing_tool_return_id",
                        severity="error",
                        tool_call_id="",
                        message_index=index,
                        detail="A tool return requires a non-empty invocation id.",
                    )
                )
                continue
            invocation = self.pending.get(tool_call_id)
            if invocation is None:
                self.issues.append(
                    ToolHistoryIssue(
                        code="orphan_tool_return",
                        severity="error",
                        tool_call_id=tool_call_id,
                        message_index=index,
                        detail="A tool return has no preceding unmatched tool call.",
                    )
                )
                continue
            call_index, tool_name = invocation
            if return_part.tool_name != tool_name:
                self.issues.append(
                    ToolHistoryIssue(
                        code="tool_return_name_mismatch",
                        severity="error",
                        tool_call_id=tool_call_id,
                        message_index=index,
                        detail="A tool return names a different tool from its call.",
                    )
                )
                if return_part.is_retry:
                    continue
            self.pending.pop(tool_call_id)
            if index != call_index + 1:
                self.issues.append(
                    ToolHistoryIssue(
                        code="non_adjacent_tool_return",
                        severity="warning",
                        tool_call_id=tool_call_id,
                        message_index=index,
                        detail="A tool return is not in the message immediately after its call.",
                    )
                )

    def unmatched_call_issues(self) -> tuple[ToolHistoryIssue, ...]:
        """Report pending calls only when the complete history boundary is known."""
        return tuple(
            ToolHistoryIssue(
                code="orphan_tool_call",
                severity="error",
                tool_call_id=tool_call_id,
                message_index=call_index,
                detail="A tool call has no matching tool return.",
            )
            for tool_call_id, (call_index, _tool_name) in self.pending.items()
        )


def analyze_tool_history(messages: Sequence[ModelMessage]) -> ToolHistoryIntegrity:
    """Analyze provider-native messages for tool-call/return integrity."""
    protocol = ToolHistoryProtocolState()
    call_count = 0
    return_count = 0
    multi_call_batch_count = 0
    multi_return_batch_count = 0
    for index, message in enumerate(messages):
        calls, replies = model_message_tool_invocations(message)
        message_return_count = sum(not part.is_retry for part in replies)
        call_count += len(calls)
        return_count += message_return_count
        multi_call_batch_count += int(len(calls) > 1)
        multi_return_batch_count += int(message_return_count > 1)
        protocol.record_message(index, calls, replies)
    issues = [*protocol.issues, *protocol.unmatched_call_issues()]

    return ToolHistoryIntegrity(
        status="ok" if not issues else "issues",
        tool_call_count=call_count,
        tool_return_count=return_count,
        multi_call_batch_count=multi_call_batch_count,
        multi_return_batch_count=multi_return_batch_count,
        issues=tuple(issues),
    )


def model_message_tool_invocations(
    message: ModelMessage,
) -> tuple[list[ToolHistoryInvocation], list[ToolHistoryInvocation]]:
    """Extract custom-tool calls and replies; provider-native tools are self-contained."""
    return (
        [
            ToolHistoryInvocation(part.tool_call_id, part.tool_name)
            for part in _tool_call_parts(message)
        ],
        [
            ToolHistoryInvocation(
                part.tool_call_id,
                part.tool_name,
                is_retry=isinstance(part, RetryPromptPart),
            )
            for part in _tool_reply_parts(message)
        ],
    )


def analyze_tool_history_payloads(
    payloads: Sequence[dict[str, Any]],
) -> ToolHistoryIntegrity:
    """Analyze JSON/dict message payloads for tool-call/return integrity."""
    messages: list[ModelMessage] = []
    issues: list[ToolHistoryIssue] = []
    for index, payload in enumerate(payloads):
        try:
            messages.append(_MODEL_MESSAGE_ADAPTER.validate_python(payload))
        except Exception as exc:  # noqa: BLE001
            issues.append(
                ToolHistoryIssue(
                    code="invalid_message_payload",
                    severity="error",
                    tool_call_id="",
                    message_index=index,
                    detail=f"Message payload could not be decoded: {type(exc).__name__}: {exc}",
                )
            )
    integrity = analyze_tool_history(messages)
    if not issues:
        return integrity
    return ToolHistoryIntegrity(
        status="issues",
        tool_call_count=integrity.tool_call_count,
        tool_return_count=integrity.tool_return_count,
        multi_call_batch_count=integrity.multi_call_batch_count,
        multi_return_batch_count=integrity.multi_return_batch_count,
        issues=(*issues, *integrity.issues),
    )


def _tool_call_parts(message: ModelMessage) -> list[ToolCallPart]:
    if not isinstance(message, ModelResponse):
        return []
    return [part for part in message.parts if isinstance(part, ToolCallPart)]


def _tool_reply_parts(
    message: ModelMessage,
) -> list[ToolReturnPart | RetryPromptPart]:
    """Include named tool retries as replies; output retries have no invocation."""
    if not isinstance(message, ModelRequest):
        return []
    return [
        part
        for part in message.parts
        if isinstance(part, ToolReturnPart)
        or (isinstance(part, RetryPromptPart) and part.tool_name is not None)
    ]


def _duplicates(values: Sequence[str]) -> list[str]:
    counts = Counter(values)
    return sorted(value for value, count in counts.items() if count > 1)
