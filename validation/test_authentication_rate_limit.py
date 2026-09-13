"""Deterministic tests for bounded authentication-failure throttling."""

from __future__ import annotations

from core.authentication import AuthenticationFailureLimiter


def test_failure_window_expires_and_success_clears() -> None:
    now = [100.0]
    limiter = AuthenticationFailureLimiter(
        failure_limit=2,
        window_seconds=10,
        clock=lambda: now[0],
    )

    limiter.record_failure("peer")
    limiter.record_failure("peer")
    assert limiter.is_limited("peer")

    limiter.record_success("peer")
    assert not limiter.is_limited("peer")
    limiter.record_failure("peer")
    limiter.record_failure("peer")
    now[0] += 11
    assert not limiter.is_limited("peer")


def test_peer_storage_is_bounded() -> None:
    limiter = AuthenticationFailureLimiter(
        failure_limit=1,
        maximum_tracked_peers=2,
    )

    limiter.record_failure("first")
    limiter.record_failure("second")
    limiter.record_failure("third")

    assert not limiter.is_limited("first")
