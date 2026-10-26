"""Shared runtime state for the Monty-backed authoring surface."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from pydantic_ai.messages import (
    MULTI_MODAL_CONTENT_TYPES,
    BinaryAudio,
    BinaryImage,
    CachePoint,
    TextContent,
)

from core.authoring.contracts import (
    AssembleContextResult,
    AuthoringHost,
    ContextMessage,
    HistoryMessage,
    LatestMessage,
    MarkdownCodeBlock,
    MarkdownHeading,
    MarkdownImage,
    MarkdownSection,
    ParsedMarkdown,
    PendingFilesResult,
    RetrievedHistoryResult,
    RetrievedItem,
    RetrieveResult,
    ScriptToolResult,
    ToolExchange,
    ToolExchangeBatch,
)
from core.runtime.buffers import BufferStore
from core.runtime.paths import get_data_root
from core.utils.file_state import WorkflowFileStateManager
from core.utils.patterns import PatternUtilities


def _current_datetime_today() -> datetime:
    """Resolve today's date at runtime so validation can monkey-patch the module clock."""
    return datetime.today()


@dataclass(frozen=True)
class MontyDateTokens:
    """Python-friendly wrapper over the shared date token vocabulary."""

    reference_date: datetime
    week_start_day: int = 0

    def today(self, fmt: str | None = None) -> str:
        return self._resolve("today", fmt)

    def yesterday(self, fmt: str | None = None) -> str:
        return self._resolve("yesterday", fmt)

    def tomorrow(self, fmt: str | None = None) -> str:
        return self._resolve("tomorrow", fmt)

    def this_week(self, fmt: str | None = None) -> str:
        return self._resolve("this-week", fmt)

    def last_week(self, fmt: str | None = None) -> str:
        return self._resolve("last-week", fmt)

    def next_week(self, fmt: str | None = None) -> str:
        return self._resolve("next-week", fmt)

    def this_month(self, fmt: str | None = None) -> str:
        return self._resolve("this-month", fmt)

    def last_month(self, fmt: str | None = None) -> str:
        return self._resolve("last-month", fmt)

    def day_name(self, fmt: str | None = None) -> str:
        return self._resolve("day-name", fmt)

    def month_name(self, fmt: str | None = None) -> str:
        return self._resolve("month-name", fmt)

    def _resolve(self, token: str, fmt: str | None) -> str:
        resolved = self._resolve_datetime(token)
        if resolved is None:
            return token
        if fmt is None:
            return PatternUtilities.resolve_date_pattern(
                token,
                reference_date=self.reference_date,
                week_start_day=self.week_start_day,
            )
        return resolved.strftime(fmt)

    def _resolve_datetime(self, token: str) -> datetime | None:
        if token == "today":
            return self.reference_date
        if token == "yesterday":
            return self.reference_date - timedelta(days=1)
        if token == "tomorrow":
            return self.reference_date + timedelta(days=1)
        if token == "this-week":
            return PatternUtilities._get_week_start_date(
                self.reference_date, self.week_start_day, 0
            )
        if token == "last-week":
            return PatternUtilities._get_week_start_date(
                self.reference_date, self.week_start_day, -1
            )
        if token == "next-week":
            return PatternUtilities._get_week_start_date(
                self.reference_date, self.week_start_day, 1
            )
        if token == "this-month":
            return self.reference_date.replace(day=1)
        if token == "last-month":
            last_month = self.reference_date.replace(day=1) - timedelta(days=1)
            return last_month.replace(day=1)
        if token in {"day-name", "month-name"}:
            return self.reference_date
        return None


@dataclass(frozen=True)
class Workspace:
    """Read-only workspace selected for the current chat session."""

    path: str = ""
    exists: bool = False

    def __post_init__(self) -> None:
        normalized = (self.path or "").strip()
        if normalized != self.path:
            object.__setattr__(self, "path", normalized)
        if normalized and not self.exists:
            object.__setattr__(self, "exists", True)
        if not normalized and self.exists:
            object.__setattr__(self, "exists", False)


@dataclass
class WorkflowAuthoringHost(AuthoringHost):
    """Workflow-scoped runtime state shared by helper executors."""

    workflow_id: str
    vault_path: str | None = None
    reference_date: datetime = field(default_factory=_current_datetime_today)
    week_start_day: int = 0
    run_buffers: BufferStore = field(default_factory=BufferStore)
    session_buffers: BufferStore = field(default_factory=BufferStore)
    state_manager: WorkflowFileStateManager | None = None
    session_key: str | None = None
    chat_session_id: str | None = None
    message_history: list | None = None
    # Context assembly sets this when message_history is already curated prior history.
    prefer_message_history: bool = False
    latest_message: LatestMessage = field(default_factory=LatestMessage)
    workspace: Workspace = field(default_factory=Workspace)

    def __post_init__(self) -> None:
        if self.vault_path is None:
            if "/" not in self.workflow_id:
                raise ValueError(
                    f"Invalid workflow_id format. Expected 'vault/name', got: {self.workflow_id}"
                )
            vault_name, _workflow_name = self.workflow_id.split("/", 1)
            self.vault_path = os.path.join(str(get_data_root()), vault_name)
        if self.state_manager is None and "/" in self.workflow_id:
            vault_name, _workflow_name = self.workflow_id.split("/", 1)
            self.state_manager = WorkflowFileStateManager(vault_name, self.workflow_id)
        if self.session_key is None:
            self.session_key = self.workflow_id
        if self.chat_session_id is None:
            self.chat_session_id = self.session_key

    def get_monty_inputs(self) -> dict[str, object]:
        """Return reserved Monty globals injected by the host."""
        return {
            "date": MontyDateTokens(
                reference_date=self.reference_date,
                week_start_day=self.week_start_day,
            ),
            "latest_message": self.latest_message,
            "workspace": self.workspace,
        }

    def get_monty_dataclasses(self) -> tuple[type, ...]:
        """Return dataclass types Monty should expose for reserved globals."""
        return (
            MontyDateTokens,
            Workspace,
            RetrievedItem,
            RetrieveResult,
            ScriptToolResult,
            PendingFilesResult,
            AssembleContextResult,
            ContextMessage,
            LatestMessage,
            HistoryMessage,
            MarkdownHeading,
            MarkdownSection,
            MarkdownCodeBlock,
            MarkdownImage,
            ParsedMarkdown,
            RetrievedHistoryResult,
            ToolExchange,
            ToolExchangeBatch,
            *MULTI_MODAL_CONTENT_TYPES,
            BinaryImage,
            BinaryAudio,
            TextContent,
            CachePoint,
        )
