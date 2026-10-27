"""Application-owned pytest fixtures; no scenario-harness execution machinery."""

from __future__ import annotations

import asyncio
import base64
import os
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest_asyncio

from .evidence import Evidence
from .observations import Observations

if TYPE_CHECKING:
    from httpx2 import Response

    from validation.support.system_controller import SystemController
    from validation.support.vault_manager import VaultManager


@dataclass
class Application:
    controller: SystemController
    vaults: VaultManager
    evidence: Evidence
    events: Observations

    @property
    def root(self) -> Path:
        return self.evidence.root

    def vault(self, name: str) -> Path:
        self.evidence.step("create_vault", vault=name)
        return self.vaults.create_vault(name)

    def write(self, vault: Path, path: str, content: str) -> None:
        self.vaults.create_file(vault, path, content)

    async def start(self) -> None:
        await self.controller.start_system()
        self.evidence.step("application_started")

    def request(self, endpoint: str, method: str = "GET", data: Any = None) -> Response:
        response = self.controller.get_api_test_client().request(
            method, endpoint, json=data
        )
        try:
            body = response.json()
        except ValueError:
            body = response.text
        self.evidence.append(
            "api.jsonl",
            {
                "method": method,
                "endpoint": endpoint,
                "request": data,
                "status": response.status_code,
                "response": body,
            },
        )
        return response

    def configuration(self) -> dict[str, Any]:
        from validation.support.configuration import read_configuration

        snapshot = read_configuration(self.root / "system")
        self.evidence.append("configuration.jsonl", snapshot)
        return snapshot

    def install_configuration(self, snapshot: dict[str, Any]) -> None:
        from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
        from core.runtime.reload_service import reload_configuration
        from validation.support.configuration import (
            install_configuration,
            validate_configuration,
        )

        validate_configuration(snapshot)
        install_configuration(self.root / "system", snapshot)
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            reload_configuration()
        self.evidence.step("configuration_fixture_installed")

    async def wait_task(self, task_id: str) -> dict[str, Any]:
        # Retain the original scenario's task-coordinator read boundary.
        from core.runtime.state import get_runtime_context

        deadline = asyncio.get_running_loop().time() + 60
        while True:
            snapshot = await get_runtime_context().task_coordinator.get_task(task_id)
            assert snapshot is not None, task_id
            task = {**snapshot.__dict__, "metadata": dict(snapshot.metadata or {})}
            if task["status"] in {
                "completed",
                "failed",
                "cancelled",
                "timed_out",
                "skipped",
            }:
                self.evidence.append("task-results.jsonl", task)
                return task
            assert (
                asyncio.get_running_loop().time() < deadline
            ), f"Task did not terminate: {task_id}"
            await asyncio.sleep(0.25)

    def parse_result(self, text: str) -> dict[str, str]:
        self.evidence.append("tool-results.jsonl", {"text": text})
        return {
            key.strip(): value.strip()
            for line in text.splitlines()
            if ":" in line
            for key, value in [line.split(":", 1)]
        }


@pytest_asyncio.fixture
async def app(evidence: Evidence) -> AsyncIterator[Application]:
    from core.runtime.paths import (
        restore_bootstrap_roots,
        set_bootstrap_roots,
        snapshot_bootstrap_roots,
    )
    from core.runtime.state import has_runtime_context

    assert (
        not has_runtime_context()
    ), "Previous test left the application runtime active"
    roots = snapshot_bootstrap_roots()
    environment = {
        name: os.environ.get(name)
        for name in ("ASSISTANTMD_SECRETS_KEY", "LOGFIRE_TOKEN")
    }
    key = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
    evidence.secrets.append(key)
    os.environ["ASSISTANTMD_SECRETS_KEY"] = key
    os.environ.pop("LOGFIRE_TOKEN", None)
    warmup = evidence.root / "bootstrap"
    for name in ("data", "system"):
        (warmup / name).mkdir(parents=True)
    set_bootstrap_roots(warmup / "data", warmup / "system")
    controller = None
    try:
        from core.logger import capture_validation_events, close_activity_log_for_root
        from validation.support.system_controller import SystemController
        from validation.support.vault_manager import VaultManager

        events = Observations(evidence)
        with capture_validation_events(
            evidence.artifacts, sink=events.capture, projector=evidence.redact
        ):
            controller = SystemController(evidence.root, secret_key=key)
            application = Application(
                controller, VaultManager(evidence.root), evidence, events
            )
            evidence.step("application_fixture_ready")
            try:
                yield application
            finally:
                try:
                    await controller.close()
                finally:
                    evidence.save(
                        "lifecycle.json",
                        {
                            "runtime_active_after_close": has_runtime_context(),
                            "event_count": events.checkpoint(),
                        },
                    )
                    evidence.step(
                        "application_closed", runtime_active=has_runtime_context()
                    )
                    close_activity_log_for_root(evidence.root / "system")
                    close_activity_log_for_root(warmup / "system")
    finally:
        for module_name, reset in (
            ("core.settings.store", "refresh_settings_cache"),
            ("core.secrets", "reset_secrets_bootstrap_status"),
            ("core.llm.model_utils", "invalidate_model_cache"),
        ):
            module = sys.modules.get(module_name)
            if module is not None:
                getattr(module, reset)()
        restore_bootstrap_roots(roots)
        for name, value in environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
