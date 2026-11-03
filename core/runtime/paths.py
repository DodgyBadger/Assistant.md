"""
Runtime-aware path helpers.

Provides centralized access to data, system, and docs roots with explicit
bootstrap support. Once a runtime context exists, it becomes the single source
of truth; otherwise, callers must set bootstrap roots explicitly.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from core.runtime.state import (
    RuntimeStateError,
    get_runtime_context,
    has_runtime_context,
)

# Default roots for container environments
DEFAULT_DATA_ROOT = Path("/app/data")
DEFAULT_SYSTEM_ROOT = Path("/app/system")

_bootstrap_data_root: Path | None = None
_bootstrap_system_root: Path | None = None


@dataclass(frozen=True)
class BootstrapRoots:
    """Exact bootstrap root state, including unset roots."""

    data_root: Path | None
    system_root: Path | None


def snapshot_bootstrap_roots() -> BootstrapRoots:
    """Capture bootstrap roots independently of the active runtime."""
    return BootstrapRoots(_bootstrap_data_root, _bootstrap_system_root)


def restore_bootstrap_roots(roots: BootstrapRoots) -> None:
    """Restore a previously captured bootstrap root state."""
    global _bootstrap_data_root, _bootstrap_system_root
    _bootstrap_data_root = roots.data_root
    _bootstrap_system_root = roots.system_root


def set_bootstrap_roots(data_root: Path, system_root: Path) -> None:
    """
    Set bootstrap roots for code paths that run before runtime context exists.

    Args:
        data_root: Root path for vault data
        system_root: Root path for system data
    """
    global _bootstrap_data_root, _bootstrap_system_root
    _bootstrap_data_root = Path(data_root)
    _bootstrap_system_root = Path(system_root)


def clear_bootstrap_roots() -> None:
    """Clear any previously set bootstrap roots."""
    global _bootstrap_data_root, _bootstrap_system_root
    _bootstrap_data_root = None
    _bootstrap_system_root = None


def resolve_bootstrap_data_root() -> Path:
    """Determine bootstrap data root from environment or defaults."""
    return Path(os.getenv("CONTAINER_DATA_ROOT", DEFAULT_DATA_ROOT))


def resolve_bootstrap_system_root() -> Path:
    """Determine bootstrap system root from environment or defaults."""
    return Path(os.getenv("CONTAINER_SYSTEM_ROOT", DEFAULT_SYSTEM_ROOT))


def _require_root(
    name: str, context_root: Path | None, bootstrap_root: Path | None
) -> Path:
    """Return a resolved root, preferring runtime context then bootstrap root."""
    if context_root:
        return context_root
    if bootstrap_root:
        return bootstrap_root
    raise RuntimeStateError(
        f"No runtime context available for {name}. "
        "Set bootstrap roots before accessing path helpers."
    )


def get_data_root() -> Path:
    """Return the active data root (vaults)."""
    context_root = None
    if has_runtime_context():
        context_root = Path(get_runtime_context().config.data_root)
    return _require_root("data_root", context_root, _bootstrap_data_root)


def get_system_root() -> Path:
    """Return the active system root (settings, secrets, logs, DBs)."""
    context_root = None
    if has_runtime_context():
        context_root = Path(get_runtime_context().config.system_root)
    return _require_root("system_root", context_root, _bootstrap_system_root)
