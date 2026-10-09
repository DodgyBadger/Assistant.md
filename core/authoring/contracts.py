"""Contracts for the experimental Monty-backed authoring runtime."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from core.runtime.buffers import BufferStore
    from core.utils.file_state import WorkflowFileStateManager

BUILTIN_CAPABILITY_NAMES: frozenset[str] = frozenset(
    {
        "read_cache",
        "pending_files",
        "retrieve_history",
        "assemble_context",
        "parse_markdown",
        "finish",
    }
)


class AuthoringCapabilityError(ValueError):
    """Base error for capability registration failures."""


class UnknownAuthoringCapabilityError(AuthoringCapabilityError):
    """Raised when code or frontmatter references an unknown capability."""


class AuthoringFinishSignal(RuntimeError):
    """Raised internally when authored code ends execution intentionally."""

    PREFIX = "__authoring_finish__:"

    def __init__(self, *, status: str, reason: str = "") -> None:
        payload = {"status": status, "reason": reason}
        super().__init__(f"{self.PREFIX}{json.dumps(payload, sort_keys=True)}")
        self.status = status
        self.reason = reason

    @classmethod
    def try_parse(cls, value: str) -> tuple[str, str] | None:
        if not isinstance(value, str) or not value.startswith(cls.PREFIX):
            return None
        payload = json.loads(value[len(cls.PREFIX) :])
        status = str(payload.get("status") or "").strip().lower()
        reason = str(payload.get("reason") or "")
        if not status:
            return None
        return status, reason


class AuthoringToolCallError(RuntimeError):
    """Raised when a direct Monty tool call returns a structured failure."""

    def __init__(
        self,
        *,
        tool_name: str,
        operation: str = "",
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.tool_name = str(tool_name or "unknown")
        self.operation = str(operation or "")
        self.reason = str(reason or "Tool call failed")
        self.metadata = dict(metadata or {})
        operation_label = f" operation '{self.operation}'" if self.operation else ""
        super().__init__(f"{self.tool_name}{operation_label} failed: {self.reason}")


@dataclass(frozen=True)
class AuthoringCapabilityCall:
    """One sandbox-to-host capability invocation."""

    capability_name: str
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AuthoringExecutionContext:
    """Stable execution context passed to capability handlers."""

    workflow_id: str
    host: AuthoringHost


@dataclass(frozen=True)
class RetrievedItem:
    """One retrieved artifact returned to Monty-authored Python."""

    ref: str | None
    content: str
    exists: bool
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrieveResult:
    """Envelope for retrieve(...) results."""

    type: str
    ref: str
    items: tuple[RetrievedItem, ...] = ()


@dataclass(frozen=True)
class ContextMessage:
    """One normalized chat-history message for downstream context assembly."""

    role: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    text: str = ""

    def __post_init__(self) -> None:
        if not self.text:
            object.__setattr__(
                self, "text", _role_content_to_text(self.role, self.content)
            )

    def to_text(self) -> str:
        """Return a clean prompt-oriented representation."""
        return self.text

    def __str__(self) -> str:
        return self.to_text()


@dataclass(frozen=True)
class HistoryMessage:
    """One preserved conversation message returned by authoring history retrieval."""

    role: str
    content: str
    message: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    text: str = ""

    def __post_init__(self) -> None:
        if not self.text:
            object.__setattr__(
                self, "text", _role_content_to_text(self.role, self.content)
            )

    def to_text(self) -> str:
        """Return clean message text without provider-native payload internals."""
        return self.text

    def __str__(self) -> str:
        return self.to_text()


@dataclass(frozen=True)
class ToolExchange:
    """One atomic tool call/return exchange returned by authoring history retrieval."""

    tool_call_id: str
    tool_name: str
    request_message: dict[str, Any]
    response_message: dict[str, Any]
    call_arguments: dict[str, Any] | None = None
    result_text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    text: str = ""

    def __post_init__(self) -> None:
        if not self.text:
            object.__setattr__(
                self,
                "text",
                _tool_exchange_to_text(
                    self.tool_name, self.call_arguments, self.result_text
                ),
            )

    def to_text(self) -> str:
        """Return clean prompt text while keeping raw tool parts available separately."""
        return self.text

    def __str__(self) -> str:
        return self.to_text()


def _role_content_to_text(role: str, content: str) -> str:
    normalized_role = (role or "message").strip().lower() or "message"
    text = content or ""
    return f"{normalized_role}: {text}".rstrip()


def _tool_exchange_to_text(
    tool_name: str,
    call_arguments: dict[str, Any] | None,
    result_text: str | None,
) -> str:
    tool_label = tool_name.strip() or "unknown_tool"
    lines = [f"tool_exchange: {tool_label}"]
    if call_arguments:
        lines.append(
            "arguments: "
            + json.dumps(
                call_arguments,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
        )
    if result_text:
        lines.append(f"result: {result_text}")
    return "\n".join(lines)


@dataclass(frozen=True)
class ToolExchangeBatch:
    """One atomic batch of tool call/return exchanges."""

    request_message: dict[str, Any]
    response_message: dict[str, Any]
    exchanges: tuple[ToolExchange, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    text: str = ""

    def __post_init__(self) -> None:
        if not self.text:
            object.__setattr__(
                self,
                "text",
                "\n\n".join(
                    exchange.text
                    for exchange in self.exchanges
                    if exchange.text.strip()
                ),
            )

    def to_text(self) -> str:
        """Return clean prompt text while keeping raw tool parts available separately."""
        return self.text

    def __str__(self) -> str:
        return self.to_text()


@dataclass(frozen=True)
class RetrievedHistoryResult:
    """Envelope for structured history retrieval into authored Python."""

    source: str
    scope: str
    session_id: str | None
    item_count: int
    items: tuple[HistoryMessage | ToolExchange | ToolExchangeBatch, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    text: str = ""

    def __post_init__(self) -> None:
        if not self.text:
            object.__setattr__(
                self,
                "text",
                "\n\n".join(item.text for item in self.items if item.text.strip()),
            )


@dataclass(frozen=True)
class RetrievedSessionsResult:
    """Envelope for chat-session metadata retrieval into authored Python."""

    selection: str
    status: str
    item_count: int
    items: tuple[RetrievedItem, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    text: str = ""

    def __post_init__(self) -> None:
        if not self.text:
            object.__setattr__(
                self,
                "text",
                "\n".join(item.content for item in self.items if item.content.strip()),
            )


@dataclass(frozen=True)
class LatestMessage:
    """Read-only latest message exposed to authored Python outside history."""

    role: str = ""
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    text: str = ""
    exists: bool = False

    def __post_init__(self) -> None:
        if not self.text and self.role:
            object.__setattr__(
                self, "text", _role_content_to_text(self.role, self.content)
            )
        if self.role and not self.exists:
            object.__setattr__(self, "exists", True)


@dataclass(frozen=True)
class PendingFilesResult:
    """Envelope for pending_files(...) results."""

    operation: str
    status: str
    items: tuple[RetrievedItem, ...] = ()
    completed_count: int = 0


@dataclass(frozen=True)
class ScriptToolResult:
    """Envelope for script-facing direct tool results."""

    return_value: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    content: Any | None = None
    items: tuple[RetrievedItem, ...] = ()


@dataclass(frozen=True)
class AssembleContextResult:
    """Validated structured context ready for a downstream chat call."""

    messages: tuple[
        ContextMessage | HistoryMessage | ToolExchange | ToolExchangeBatch, ...
    ] = ()
    instructions: tuple[str, ...] = ()


@dataclass(frozen=True)
class MarkdownHeading:
    """One markdown heading discovered in parsed content."""

    level: int
    text: str
    line_start: int


@dataclass(frozen=True)
class MarkdownSection:
    """One heading-delimited markdown section."""

    heading: str
    level: int
    content: str
    line_start: int


@dataclass(frozen=True)
class MarkdownCodeBlock:
    """One fenced code block discovered in parsed content."""

    language: str | None
    content: str
    line_start: int | None = None


@dataclass(frozen=True)
class MarkdownImage:
    """One markdown image reference discovered in parsed content."""

    src: str
    alt: str
    title: str | None = None
    line_start: int | None = None


@dataclass(frozen=True)
class ParsedMarkdown:
    """Structured markdown decomposition for authored Python exploration."""

    frontmatter: dict[str, Any] = field(default_factory=dict)
    body: str = ""
    headings: tuple[MarkdownHeading, ...] = ()
    sections: tuple[MarkdownSection, ...] = ()
    code_blocks: tuple[MarkdownCodeBlock, ...] = ()
    images: tuple[MarkdownImage, ...] = ()


@dataclass(frozen=True)
class FinishResult:
    """Envelope for an intentional authored termination."""

    status: str
    reason: str = ""


@runtime_checkable
class AuthoringHost(Protocol):
    """Host-side runtime state exposed to helper executors."""

    vault_path: str | None
    reference_date: datetime
    week_start_day: int
    run_buffers: BufferStore
    session_buffers: BufferStore
    state_manager: WorkflowFileStateManager | None
    session_key: str | None

    def get_monty_inputs(self) -> dict[str, Any]: ...

    def get_monty_dataclasses(self) -> tuple[type, ...]: ...


CapabilityHandler = Any


@dataclass(frozen=True)
class AuthoringCapabilityDefinition:
    """Registered capability metadata and runtime adapter."""

    name: str
    doc: str
    handler: CapabilityHandler
    contract: dict[str, Any] = field(default_factory=dict)
