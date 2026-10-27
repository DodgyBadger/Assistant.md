"""Validation helpers for Pydantic AI stream test doubles."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager


def stream_events_context[**P, EventT](
    function: Callable[P, AsyncIterator[EventT]],
) -> Callable[P, AbstractAsyncContextManager[AsyncIterator[EventT]]]:
    """Adapt an async-generator fake to Pydantic's managed stream contract."""

    @asynccontextmanager
    async def open_stream(
        *args: P.args, **kwargs: P.kwargs
    ) -> AsyncIterator[AsyncIterator[EventT]]:
        events = function(*args, **kwargs)
        try:
            yield events
        finally:
            await events.aclose()

    return open_stream
