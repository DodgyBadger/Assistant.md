"""Bounded live handoff metadata; no page content or human input."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Literal
from uuid import uuid4

HandoffReason = Literal["sign_in", "credential", "verification"]
HandoffStatus = Literal[
    "waiting",
    "human",
    "returned",
    "stopped",
    "expired",
    "closed",
    "cleanup_unconfirmed",
]


@dataclass(frozen=True)
class HandoffView:
    handoff_id: str
    task_id: str
    browser_id: str
    page_id: str
    reason: HandoffReason
    status: HandoffStatus

    def as_metadata(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class BrowserHandoff:
    task_id: str
    browser_id: str
    page_id: str
    reason: HandoffReason
    handoff_id: str = field(default_factory=lambda: uuid4().hex)
    status: HandoffStatus = "waiting"
    revision_key: str | None = None
    replacement_task_id: str | None = None
    completed: asyncio.Future[HandoffStatus] = field(
        default_factory=lambda: asyncio.get_running_loop().create_future()
    )

    @property
    def human_controlled(self) -> bool:
        return self.status in {"waiting", "human", "stopped"}

    def view(self) -> HandoffView:
        return HandoffView(
            self.handoff_id,
            self.task_id,
            self.browser_id,
            self.page_id,
            self.reason,
            self.status,
        )

    def finish(self, status: HandoffStatus) -> None:
        self.status = status
        if not self.completed.done():
            self.completed.set_result(status)


HandoffPublisher = Callable[[HandoffView, Callable[[], bool]], Awaitable[None]]
