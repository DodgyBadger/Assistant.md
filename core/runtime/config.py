"""
Runtime configuration for AssistantMD bootstrap.

Provides structured configuration for system initialization with
validation, defaults, and path management.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.constants import DEFAULT_MAX_SCHEDULER_WORKERS
from core.runtime.public_url import PublicOrigin


@dataclass
class RuntimeConfig:
    """
    Configuration for AssistantMD runtime bootstrap.

    Provides path management, scheduler settings, and feature flags
    for both production and validation environments.

    Attributes:
        data_root: Root path for all vault data
        system_root: Path for system data (job store, logs, etc.)
        max_scheduler_workers: Maximum APScheduler worker threads
        enable_api: Whether to enable FastAPI endpoints
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        features: Feature flags and configuration overrides
    """

    data_root: Path
    system_root: Path
    max_scheduler_workers: int = DEFAULT_MAX_SCHEDULER_WORKERS
    enable_api: bool = True
    log_level: str = "INFO"
    public_origin: PublicOrigin | None = None
    features: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        # Convert string paths to Path objects
        if isinstance(self.data_root, str):
            self.data_root = Path(self.data_root)
        if isinstance(self.system_root, str):
            self.system_root = Path(self.system_root)

        # Validate paths exist or can be created
        try:
            self.data_root.mkdir(parents=True, exist_ok=True)
            self.system_root.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError) as e:
            raise RuntimeConfigError(f"Cannot create required directories: {e}") from e

        # Validate scheduler workers
        if self.max_scheduler_workers < 1:
            raise RuntimeConfigError("max_scheduler_workers must be at least 1")

        # Validate log level
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if self.log_level.upper() not in valid_levels:
            raise RuntimeConfigError(
                f"Invalid log_level '{self.log_level}'. Must be one of: {valid_levels}"
            )

    @classmethod
    def for_production(
        cls,
        data_root: str | Path,
        system_root: str | Path,
        public_url: str | None = None,
    ) -> "RuntimeConfig":
        """Create production configuration with standard settings."""
        return cls(
            data_root=Path(data_root),
            system_root=Path(system_root),
            max_scheduler_workers=DEFAULT_MAX_SCHEDULER_WORKERS,
            enable_api=True,
            log_level="INFO",
            public_origin=(PublicOrigin.parse(public_url) if public_url else None),
        )

    @classmethod
    def for_validation(cls, run_path: Path, test_data_root: Path) -> "RuntimeConfig":
        """Create validation configuration with test isolation."""
        return cls(
            data_root=test_data_root,
            system_root=run_path / "system",
            max_scheduler_workers=DEFAULT_MAX_SCHEDULER_WORKERS,
            enable_api=False,
            log_level="DEBUG",
            features={
                "validation": True,
                "validation_artifacts_dir": str(run_path / "artifacts"),
            },
        )


class RuntimeConfigError(Exception):
    """Raised when runtime configuration is invalid."""

    pass
