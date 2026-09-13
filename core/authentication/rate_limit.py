"""Bounded process-local throttling for failed ingress authentication."""

from __future__ import annotations

import time
from collections import OrderedDict, deque
from collections.abc import Callable
from threading import Lock

DEFAULT_FAILURE_LIMIT = 10
DEFAULT_FAILURE_WINDOW_SECONDS = 60.0
DEFAULT_MAXIMUM_TRACKED_PEERS = 4096


class AuthenticationFailureLimiter:
    """Track a bounded sliding window of failures by immediate peer."""

    def __init__(
        self,
        *,
        failure_limit: int = DEFAULT_FAILURE_LIMIT,
        window_seconds: float = DEFAULT_FAILURE_WINDOW_SECONDS,
        maximum_tracked_peers: int = DEFAULT_MAXIMUM_TRACKED_PEERS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_limit < 1 or window_seconds <= 0 or maximum_tracked_peers < 1:
            raise ValueError("Authentication failure limiter bounds must be positive.")
        self._failure_limit = failure_limit
        self._window_seconds = window_seconds
        self._maximum_tracked_peers = maximum_tracked_peers
        self._clock = clock
        self._failures: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = Lock()

    def is_limited(self, peer_key: str) -> bool:
        """Return whether the peer has reached its current failure allowance."""
        now = self._clock()
        with self._lock:
            failures = self._active_failures(peer_key, now)
            return len(failures) >= self._failure_limit

    def record_failure(self, peer_key: str) -> None:
        """Record one rejected authentication attempt within bounded storage."""
        now = self._clock()
        with self._lock:
            failures = self._active_failures(peer_key, now)
            failures.append(now)
            self._failures.move_to_end(peer_key)
            while len(self._failures) > self._maximum_tracked_peers:
                self._failures.popitem(last=False)

    def record_success(self, peer_key: str) -> None:
        """Clear accumulated failures after valid proof from the same peer."""
        with self._lock:
            self._failures.pop(peer_key, None)

    def _active_failures(self, peer_key: str, now: float) -> deque[float]:
        failures = self._failures.setdefault(peer_key, deque())
        cutoff = now - self._window_seconds
        while failures and failures[0] <= cutoff:
            failures.popleft()
        if not failures:
            self._failures.move_to_end(peer_key)
        return failures
