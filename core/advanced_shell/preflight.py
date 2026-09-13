"""Cached authenticated readiness checks for the advanced shell."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from core.logger import UnifiedLogger
from core.tools.advanced_shell import (
    FixedSshShellExecutor,
    ShellExecutionResult,
    ShellTransportConfig,
    ShellTransportError,
)

from .config import AdvancedShellConfig

DEFAULT_PREFLIGHT_CACHE_SECONDS = 15.0
PREFLIGHT_TIMEOUT_SECONDS = 5.0
logger = UnifiedLogger(tag="advanced-shell")


class AdvancedShellReadiness(StrEnum):
    """Sanitized advanced-shell readiness states safe for operator reporting."""

    INACTIVE = "inactive"
    IDENTITY_MISSING = "identity_missing"
    TRUST_MISSING = "trust_missing"
    SSH_UNAVAILABLE = "ssh_unavailable"
    DNS_FAILURE = "dns_failure"
    CONNECTION_FAILURE = "connection_failure"
    HOST_KEY_MISMATCH = "host_key_mismatch"
    AUTHENTICATION_FAILURE = "authentication_failure"
    UNAVAILABLE = "unavailable"
    READY = "ready"


@dataclass(frozen=True)
class AdvancedShellPreflightSnapshot:
    """One sanitized cached readiness result."""

    state: AdvancedShellReadiness
    message: str


ExecutorFactory = Callable[[ShellTransportConfig], FixedSshShellExecutor]


class AdvancedShellPreflightService:
    """Authenticate to the advanced-shell host and cache the sanitized outcome."""

    def __init__(
        self,
        config: AdvancedShellConfig,
        system_root: Path,
        *,
        key_root: Path | None = None,
        executor_factory: ExecutorFactory = FixedSshShellExecutor,
        cache_seconds: float = DEFAULT_PREFLIGHT_CACHE_SECONDS,
    ) -> None:
        self._config = config
        self._transport = ShellTransportConfig.from_infrastructure(
            config, system_root, key_root=key_root
        )
        self._executor_factory = executor_factory
        self._cache_seconds = cache_seconds
        self._lock = asyncio.Lock()
        self._cached: AdvancedShellPreflightSnapshot | None = None
        self._cached_at = 0.0
        self._reported_state: AdvancedShellReadiness | None = None

    async def status(self) -> AdvancedShellPreflightSnapshot:
        """Return a fresh-enough authenticated readiness snapshot."""
        if not self._config.enabled:
            return self._publish(_snapshot(AdvancedShellReadiness.INACTIVE))
        now = time.monotonic()
        if self._cached is not None and now - self._cached_at < self._cache_seconds:
            return self._cached
        async with self._lock:
            now = time.monotonic()
            if self._cached is not None and now - self._cached_at < self._cache_seconds:
                return self._cached
            snapshot = await self._check()
            self._cached = snapshot
            self._cached_at = time.monotonic()
            return self._publish(snapshot)

    def _publish(
        self, snapshot: AdvancedShellPreflightSnapshot
    ) -> AdvancedShellPreflightSnapshot:
        if snapshot.state is self._reported_state:
            return snapshot
        self._reported_state = snapshot.state
        log = (
            logger.info
            if snapshot.state
            in {
                AdvancedShellReadiness.INACTIVE,
                AdvancedShellReadiness.READY,
            }
            else logger.warning
        )
        log(
            "Advanced shell readiness changed",
            data={
                "event": "advanced_shell_readiness_changed",
                "state": snapshot.state.value,
            },
        )
        return snapshot

    async def _check(self) -> AdvancedShellPreflightSnapshot:
        if not self._transport.private_key_path.is_file():
            return _snapshot(AdvancedShellReadiness.IDENTITY_MISSING)
        if not self._transport.known_hosts_path.is_file():
            return _snapshot(AdvancedShellReadiness.TRUST_MISSING)
        try:
            result = await self._executor_factory(self._transport).execute(
                "true", timeout_seconds=PREFLIGHT_TIMEOUT_SECONDS
            )
        except FileNotFoundError:
            return _snapshot(AdvancedShellReadiness.SSH_UNAVAILABLE)
        except (ShellTransportError, OSError, ValueError):
            return _snapshot(AdvancedShellReadiness.UNAVAILABLE)
        return _classify_result(result)


def _classify_result(result: ShellExecutionResult) -> AdvancedShellPreflightSnapshot:
    if result.status == "completed" and result.exit_code == 0:
        return _snapshot(AdvancedShellReadiness.READY)
    diagnostic = result.stderr.casefold()
    if "remote host identification has changed" in diagnostic or (
        "host key verification failed" in diagnostic
    ):
        return _snapshot(AdvancedShellReadiness.HOST_KEY_MISMATCH)
    if "permission denied" in diagnostic:
        return _snapshot(AdvancedShellReadiness.AUTHENTICATION_FAILURE)
    if (
        "could not resolve hostname" in diagnostic
        or "name or service not known" in diagnostic
    ):
        return _snapshot(AdvancedShellReadiness.DNS_FAILURE)
    if result.status == "timed_out" or any(
        marker in diagnostic
        for marker in (
            "connection refused",
            "connection timed out",
            "no route to host",
            "network is unreachable",
        )
    ):
        return _snapshot(AdvancedShellReadiness.CONNECTION_FAILURE)
    return _snapshot(AdvancedShellReadiness.UNAVAILABLE)


def _snapshot(state: AdvancedShellReadiness) -> AdvancedShellPreflightSnapshot:
    messages = {
        AdvancedShellReadiness.INACTIVE: "Advanced shell is inactive.",
        AdvancedShellReadiness.IDENTITY_MISSING: "Advanced-shell client identity is missing.",
        AdvancedShellReadiness.TRUST_MISSING: "Advanced-shell host trust is missing.",
        AdvancedShellReadiness.SSH_UNAVAILABLE: "The SSH client is unavailable.",
        AdvancedShellReadiness.DNS_FAILURE: "The advanced-shell hostname cannot be resolved.",
        AdvancedShellReadiness.CONNECTION_FAILURE: "The advanced shell cannot be reached.",
        AdvancedShellReadiness.HOST_KEY_MISMATCH: "The advanced-shell host identity does not match the pinned identity.",
        AdvancedShellReadiness.AUTHENTICATION_FAILURE: "The advanced shell rejected Assistant.md authentication.",
        AdvancedShellReadiness.UNAVAILABLE: "Advanced-shell readiness could not be established.",
        AdvancedShellReadiness.READY: "The advanced shell is authenticated and ready.",
    }
    return AdvancedShellPreflightSnapshot(state=state, message=messages[state])
