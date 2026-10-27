"""Own AssistantMD bootstrap, resources and logging within one scenario lifetime."""

from __future__ import annotations

import base64
import os
import sys
from typing import TYPE_CHECKING, Any

from scenario_harness.lifecycle import RunContext

if TYPE_CHECKING:
    from core.llm.model_utils import ModelCache
    from core.runtime.paths import BootstrapRoots
    from validation.core.base_scenario import BaseScenario
    from validation.core.system_controller import SystemController


class AssistantMDAdapter:
    def __init__(self) -> None:
        self.context: RunContext | None = None
        self.scenario: BaseScenario | None = None
        self._original_roots: BootstrapRoots | None = None
        self._original_model_cache: ModelCache | None = None
        self._original_environment: dict[str, str | None] = {}
        self._capture_errors: list[Exception] = []
        self._secret_key = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")

    async def setup(self, context: RunContext) -> None:
        from core.runtime.paths import set_bootstrap_roots, snapshot_bootstrap_roots
        from core.runtime.state import has_runtime_context

        if has_runtime_context():
            raise RuntimeError(
                "Validation requires exclusive ownership of the application runtime"
            )
        self.context = context
        self._original_roots = snapshot_bootstrap_roots()
        self._original_environment = {
            name: os.environ.get(name)
            for name in ("ASSISTANTMD_SECRETS_KEY", "LOGFIRE_TOKEN")
        }
        context.defer_cleanup("assistantmd.restore_environment", self._restore)
        if "core.llm.model_utils" in sys.modules:
            from core.llm.model_utils import capture_model_cache

            self._original_model_cache = capture_model_cache()
        context.register_secret(self._secret_key)
        context.application = self
        system_root = context.root / "system"
        # Imports may initialize settings. Keep that warm-up installation separate
        # from the actual scenario fixture, whose initial policy is seeded explicitly.
        bootstrap_data = context.root / "bootstrap/data"
        bootstrap_system = context.root / "bootstrap/system"
        bootstrap_data.mkdir(parents=True, exist_ok=True)
        bootstrap_system.mkdir(parents=True, exist_ok=True)
        set_bootstrap_roots(bootstrap_data, bootstrap_system)
        os.environ["ASSISTANTMD_SECRETS_KEY"] = self._secret_key

        from core.logger import capture_validation_events, close_activity_log_for_root

        # Register before entering capture so even partial setup has an owner.
        capture = capture_validation_events(
            context.artifacts_dir,
            sink=self._capture_event,
            projector=context.redact,
        )
        context.defer_cleanup(
            "assistantmd.bootstrap_activity_log",
            lambda: close_activity_log_for_root(bootstrap_system),
        )
        context.defer_cleanup(
            "assistantmd.activity_log", lambda: close_activity_log_for_root(system_root)
        )
        context.defer_cleanup(
            "assistantmd.logging_capture", lambda: capture.__exit__(None, None, None)
        )
        capture.__enter__()
        from core.llm.model_utils import refresh_model_cache

        refresh_model_cache()
        context.emit("application_adapter_ready", application="AssistantMD")

    def _capture_event(self, record: dict[str, Any]) -> None:
        assert self.context is not None
        try:
            self.context.emit("application_event", record=record)
        except Exception as exc:
            self._capture_errors.append(exc)

    def attach_scenario(self, scenario: BaseScenario) -> None:
        if self.scenario is not None:
            raise RuntimeError(
                "One application scenario is allowed per execution context"
            )
        assert self.context is not None
        self.scenario = scenario
        self.context.defer_cleanup(
            "assistantmd.scenario_evidence", scenario.teardown_scenario
        )

    def create_controller(self, scenario: BaseScenario) -> SystemController:
        from validation.core.system_controller import SystemController

        assert self.context is not None
        controller = SystemController(scenario.run_path, secret_key=self._secret_key)
        # Ownership is registered before prepare can acquire resources or fail.
        self.context.defer_cleanup("assistantmd.controller", controller.close)
        from pathlib import Path

        from core.runtime.paths import set_bootstrap_roots

        # Keep the scenario's logical roots active through worker draining.
        # Actual directories and settings are acquired lazily by prepare().
        set_bootstrap_roots(
            Path(controller.test_data_root), scenario.run_path / "system"
        )
        return controller

    async def teardown(self, context: RunContext) -> None:
        if self._capture_errors:
            raise ExceptionGroup("Application log capture failed", self._capture_errors)

    def _restore(self) -> None:
        from core.runtime.paths import restore_bootstrap_roots

        assert self._original_roots is not None
        try:
            # Cleanup must not import and initialize application components that
            # setup never acquired, particularly while caller roots are active.
            settings = sys.modules.get("core.settings.store")
            if settings is not None:
                settings.refresh_settings_cache()
            secrets = sys.modules.get("core.secrets")
            if secrets is not None:
                secrets.reset_secrets_bootstrap_status()
            models = sys.modules.get("core.llm.model_utils")
            if models is not None:
                if self._original_model_cache is None:
                    models.invalidate_model_cache()
                else:
                    models.restore_model_cache(self._original_model_cache)
        finally:
            restore_bootstrap_roots(self._original_roots)
            for name, value in self._original_environment.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
