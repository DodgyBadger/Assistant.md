"""Validate partial runtime bootstrap cleanup on configuration failure."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

_direct_run_root: tempfile.TemporaryDirectory[str] | None = None
if __name__ == "__main__":
    from core.runtime.paths import set_bootstrap_roots

    _direct_run_root = tempfile.TemporaryDirectory(
        prefix="assistantmd-runtime-bootstrap-cleanup-"
    )
    direct_root = Path(_direct_run_root.name)
    data_root = direct_root / "data"
    system_root = direct_root / "system"
    data_root.mkdir()
    system_root.mkdir()
    set_bootstrap_roots(data_root=data_root, system_root=system_root)

from core.runtime.bootstrap import bootstrap_runtime  # noqa: E402
from core.runtime.config import RuntimeConfig, RuntimeConfigError  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class _FakeMCPConnectionService:
    def __init__(self, **_kwargs: object) -> None:
        pass


class _FakeMCPManager:
    instance: _FakeMCPManager | None = None

    def __init__(self, **_kwargs: object) -> None:
        self.started = False
        self.shutdown_count = 0
        type(self).instance = self

    def start(self) -> None:
        self.started = True

    async def shutdown(self) -> None:
        self.shutdown_count += 1


class RuntimeBootstrapCleanupScenario(BaseScenario):
    """Prove configuration failures retain their type after MCP cleanup."""

    async def test_scenario(self) -> None:
        config = RuntimeConfig.for_validation(
            self.run_path,
            self.run_path / "data",
        )
        migration_status = SimpleNamespace(pending_count=0, targets=[])
        legacy_result = SimpleNamespace(
            phase="not_present",
            imported_count=0,
            skipped_oauth_count=0,
            source_retired=False,
        )
        invalid_status = SimpleNamespace(
            is_healthy=False,
            errors=[SimpleNamespace(name="provider", message="is invalid")],
        )
        _FakeMCPManager.instance = None

        with ExitStack() as patches:
            patches.enter_context(
                patch(
                    "core.runtime.bootstrap.initialize_secrets_bootstrap",
                    return_value=SimpleNamespace(ready=True),
                )
            )
            patches.enter_context(
                patch(
                    "core.runtime.bootstrap.run_system_migrations",
                    return_value=migration_status,
                )
            )
            patches.enter_context(
                patch("core.runtime.bootstrap.get_encrypted_secrets_service")
            )
            patches.enter_context(
                patch(
                    "core.runtime.bootstrap.migrate_legacy_secrets_yaml",
                    return_value=legacy_result,
                )
            )
            patches.enter_context(
                patch(
                    "core.runtime.bootstrap.MCPConnectionService",
                    _FakeMCPConnectionService,
                )
            )
            patches.enter_context(
                patch("core.runtime.bootstrap.MCPConnectionManager", _FakeMCPManager)
            )
            patches.enter_context(patch("core.runtime.bootstrap.MCPOAuthCoordinator"))
            patches.enter_context(
                patch("core.runtime.bootstrap.refresh_settings_cache")
            )
            patches.enter_context(patch("core.runtime.bootstrap.seed_system_templates"))
            patches.enter_context(
                patch(
                    "core.runtime.bootstrap.validate_settings",
                    return_value=invalid_status,
                )
            )

            try:
                await bootstrap_runtime(config)
            except RuntimeConfigError as exc:
                self.soft_assert_equal(
                    str(exc),
                    "provider: is invalid",
                    "Bootstrap should preserve the configuration error message",
                )
            except Exception as exc:
                self.soft_assert(
                    False,
                    "Bootstrap should preserve RuntimeConfigError, not wrap it as "
                    f"{type(exc).__name__}",
                )
            else:
                self.soft_assert(False, "Invalid configuration should fail bootstrap")

        manager = _FakeMCPManager.instance
        self.soft_assert(
            manager is not None, "Bootstrap should construct the MCP manager"
        )
        if manager is not None:
            self.soft_assert(manager.started, "Bootstrap should start the MCP manager")
            self.soft_assert_equal(
                manager.shutdown_count,
                1,
                "Configuration failure should shut down the started MCP manager once",
            )

        self.assert_no_failures()
        self.teardown_scenario()


if __name__ == "__main__":
    asyncio.run(RuntimeBootstrapCleanupScenario().test_scenario())
