"""Shared bounded retry mechanics for interrupted model streams."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

from core.settings import (
    get_model_stream_idle_timeout_seconds,
    get_model_stream_retries,
    get_model_stream_retry_base_delay_seconds,
    get_model_stream_retry_max_delay_seconds,
)


class ModelStreamIdleTimeout(TimeoutError):
    """Raised when a model stream produces no usable event before its deadline."""

    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"Model stream produced no usable event for {timeout_seconds:g} seconds."
        )


async def next_model_stream_event[StreamEventT](
    iterator: AsyncIterator[StreamEventT],
    *,
    timeout_seconds: float | None = None,
) -> StreamEventT:
    """Return the next semantic stream event under an optional idle deadline."""
    timeout = (
        get_model_stream_idle_timeout_seconds()
        if timeout_seconds is None
        else timeout_seconds
    )
    if timeout <= 0:
        return await anext(iterator)
    deadline = asyncio.timeout(timeout)
    try:
        async with deadline:
            return await anext(iterator)
    except TimeoutError as exc:
        if not deadline.expired():
            raise
        raise ModelStreamIdleTimeout(timeout) from exc


@dataclass(frozen=True)
class ModelStreamRetryPolicy:
    """Validated retry budget and exponential delay loaded from settings."""

    retries: int
    base_delay_seconds: float
    max_delay_seconds: float

    @classmethod
    def from_settings(cls) -> ModelStreamRetryPolicy:
        """Load the global model-stream retry contract."""
        return cls(
            retries=get_model_stream_retries(),
            base_delay_seconds=get_model_stream_retry_base_delay_seconds(),
            max_delay_seconds=get_model_stream_retry_max_delay_seconds(),
        )

    @property
    def max_attempts(self) -> int:
        """Return the initial attempt plus the configured retry count."""
        return 1 + self.retries

    def can_retry_after(self, attempt: int) -> bool:
        """Return whether another attempt remains after the given attempt."""
        return attempt < self.max_attempts

    def delay_after(self, attempt: int) -> float:
        """Return the bounded exponential delay after a failed attempt."""
        if attempt < 1:
            raise ValueError("attempt must be at least 1")
        return float(
            min(
                self.max_delay_seconds,
                self.base_delay_seconds * (2 ** (attempt - 1)),
            )
        )
