"""Authority-aware advanced-shell capability resolution for primary chats."""

from __future__ import annotations

from typing import Protocol

from pydantic_ai.tools import Tool

from core.identity import ExecutionAuthority
from core.logger import UnifiedLogger
from core.tools.advanced_shell import (
    AdvancedShell,
    FixedSshShellExecutor,
    ShellTransportConfig,
)

from .authority import advanced_shell_authority_allowed
from .config import AdvancedShellConfig
from .preflight import AdvancedShellPreflightSnapshot, AdvancedShellReadiness

logger = UnifiedLogger(tag="advanced-shell-capability")


class AdvancedShellPreflight(Protocol):
    """Readiness boundary consumed by capability resolution."""

    async def status(self) -> AdvancedShellPreflightSnapshot:
        """Return the current sanitized readiness snapshot."""
        ...


class AdvancedShellCapabilityService:
    """Resolve one deployment-owned advanced shell for an execution principal."""

    def __init__(
        self,
        config: AdvancedShellConfig,
        transport: ShellTransportConfig,
        preflight: AdvancedShellPreflight,
    ) -> None:
        self._config = config
        self._executor = FixedSshShellExecutor(transport)
        self._preflight = preflight

    @property
    def enabled(self) -> bool:
        """Return whether deployment configuration enables advanced-shell execution."""
        return self._config.enabled

    @property
    def transport_config(self) -> ShellTransportConfig:
        """Return fixed deployment coordinates for non-model advanced-shell clients."""
        return self._executor.config

    async def readiness(self) -> AdvancedShellPreflightSnapshot:
        """Return authenticated readiness for non-model advanced-shell consumers."""
        return await self._preflight.status()

    async def resolve_for_primary_chat(
        self, authority: ExecutionAuthority
    ) -> Tool | None:
        """Return the shell tool only when this deployment's advanced shell is ready."""
        if not self._config.enabled:
            return None
        if not advanced_shell_authority_allowed(authority):
            logger.warning(
                "Advanced shell capability denied",
                data={
                    "event": "advanced_shell_capability_denied",
                    "principal_id": authority.principal_id,
                    "reason": "unsupported_tenancy",
                },
            )
            return None
        snapshot = await self._preflight.status()
        available = snapshot.state is AdvancedShellReadiness.READY
        logger.info(
            "Advanced shell capability resolved",
            data={
                "event": "advanced_shell_capability_resolved",
                "principal_id": authority.principal_id,
                "available": available,
                "readiness": snapshot.state.value,
            },
        )
        if not available:
            return None
        return AdvancedShell.for_executor(self._executor)
