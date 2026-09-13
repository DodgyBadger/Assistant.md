"""Validate restart-bound advanced-shell infrastructure configuration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from core.settings import AppSettings

ADVANCED_SHELL_STATE_DIRECTORY = "advanced-shell"
CLIENT_IDENTITY_FILENAME = "client_identity"
KNOWN_HOSTS_FILENAME = "known_hosts"
MAXIMUM_COORDINATE_LENGTH = 253


class AdvancedShellConfigurationError(ValueError):
    """Raised when advanced-shell infrastructure settings are invalid."""


class ExecutionMode(StrEnum):
    """Product execution modes selected at application startup."""

    RESTRICTED = "restricted"
    ADVANCED = "advanced"


@dataclass(frozen=True)
class AdvancedShellStatePaths:
    """Fixed AssistantMD-owned client identity and trust paths."""

    directory: Path
    client_identity: Path
    known_hosts: Path


@dataclass(frozen=True)
class AdvancedShellConfig:
    """Sanitizable fixed-destination advanced-shell coordinates."""

    execution_mode: ExecutionMode
    host: str
    port: int
    user: str
    host_key_alias: str | None

    @classmethod
    def restricted_default(cls) -> AdvancedShellConfig:
        """Return the deterministic default used by isolated application tests."""
        return cls(
            execution_mode=ExecutionMode.RESTRICTED,
            host="advanced-shell",
            port=2222,
            user="advanced-shell",
            host_key_alias=None,
        )

    @property
    def enabled(self) -> bool:
        """Return whether deployment configuration selects advanced mode."""
        return self.execution_mode is ExecutionMode.ADVANCED

    def state_paths(self, system_root: Path) -> AdvancedShellStatePaths:
        """Resolve fixed client identity paths below the protected system root."""
        directory = system_root / ADVANCED_SHELL_STATE_DIRECTORY
        return AdvancedShellStatePaths(
            directory=directory,
            client_identity=directory / CLIENT_IDENTITY_FILENAME,
            known_hosts=directory / KNOWN_HOSTS_FILENAME,
        )


def load_advanced_shell_config(settings: AppSettings) -> AdvancedShellConfig:
    """Load and validate restart-bound advanced-shell coordinates."""
    configured_mode = settings.execution_mode or ExecutionMode.RESTRICTED.value
    try:
        execution_mode = ExecutionMode(configured_mode.strip())
    except ValueError as exc:
        raise AdvancedShellConfigurationError(
            "ASSISTANTMD_EXECUTION_MODE must be restricted or advanced."
        ) from exc

    active_profiles = {
        profile.strip()
        for profile in settings.compose_profiles.split(",")
        if profile.strip()
    }
    advanced_profile_active = ExecutionMode.ADVANCED.value in active_profiles
    if advanced_profile_active and execution_mode is not ExecutionMode.ADVANCED:
        raise AdvancedShellConfigurationError(
            "COMPOSE_PROFILES includes advanced, so "
            "ASSISTANTMD_EXECUTION_MODE must also be advanced."
        )
    if (
        active_profiles
        and execution_mode is ExecutionMode.ADVANCED
        and not advanced_profile_active
    ):
        raise AdvancedShellConfigurationError(
            "ASSISTANTMD_EXECUTION_MODE is advanced, so COMPOSE_PROFILES must "
            "also include advanced for a Compose deployment."
        )

    host = _validate_coordinate("ASSISTANTMD_SHELL_HOST", settings.shell_host)
    user = _validate_coordinate("ASSISTANTMD_SHELL_USER", settings.shell_user)
    alias = _optional_coordinate(
        "ASSISTANTMD_SHELL_HOST_KEY_ALIAS", settings.shell_host_key_alias
    )
    port = settings.shell_port
    if not 1 <= port <= 65535:
        raise AdvancedShellConfigurationError(
            "ASSISTANTMD_SHELL_PORT must be between 1 and 65535."
        )
    return AdvancedShellConfig(
        execution_mode=execution_mode,
        host=host,
        port=port,
        user=user,
        host_key_alias=alias,
    )


def _optional_coordinate(name: str, value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return _validate_coordinate(name, value)


def _validate_coordinate(name: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise AdvancedShellConfigurationError(f"{name} cannot be empty.")
    if len(normalized) > MAXIMUM_COORDINATE_LENGTH:
        raise AdvancedShellConfigurationError(
            f"{name} cannot exceed {MAXIMUM_COORDINATE_LENGTH} characters."
        )
    if normalized.startswith("-") or any(
        character.isspace() or ord(character) < 0x21 or ord(character) > 0x7E
        for character in normalized
    ):
        raise AdvancedShellConfigurationError(
            f"{name} must contain only visible non-whitespace ASCII and cannot start with '-'."
        )
    return normalized
