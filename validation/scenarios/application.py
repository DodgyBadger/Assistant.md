"""Real AssistantMD lifecycle and actions for staged plugin-backed cases."""

from __future__ import annotations

import asyncio
import base64
import os
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio
from httpx2 import ASGITransport, AsyncClient
from scenario_harness import Observations, ScenarioRun

if TYPE_CHECKING:
    from httpx2 import Response

    from validation.support.system_controller import SystemController
    from validation.support.vault_manager import VaultManager


_uncertain_fixture_failure: BaseException | None = None


def _ambient_credential(name: str) -> bool:
    return (
        name.endswith(("_API_KEY", "_TOKEN", "_API_TOKEN"))
        or name.startswith("ASSISTANTMD_SECRETS_")
        or name in {"GOOGLE_APPLICATION_CREDENTIALS", "AZURE_OPENAI_AD_TOKEN"}
    )


def _stop_after_fixture_failure(
    request: pytest.FixtureRequest, evidence: ScenarioRun, error: BaseException
) -> None:
    """Abort after the real phase report; never reuse an uncertain app process."""
    global _uncertain_fixture_failure
    _uncertain_fixture_failure = error
    request.session.shouldfail = (
        "Application fixture cleanup is unconfirmed; start a fresh pytest process"
    )
    # Mark the session before writing evidence so an I/O failure cannot permit reuse.
    try:
        evidence.save_json(
            "fixture-failure.json",
            {
                "type": type(error).__name__,
                "message": str(error),
                "reuse_allowed": False,
            },
        )
    except Exception as diagnostic_error:
        error.add_note(
            f"Could not retain fixture-failure artifact: {type(diagnostic_error).__name__}"
        )


@dataclass
class Application:
    controller: SystemController
    vaults: VaultManager
    evidence: ScenarioRun
    events: Observations

    @property
    def root(self) -> Path:
        return self.evidence.root

    def vault(self, name: str) -> Path:
        self.evidence.step("create_vault", vault=name)
        return self.vaults.create_vault(name)

    def write(self, vault: Path, path: str, content: str) -> None:
        self.vaults.create_file(vault, path, content)

    async def start(self, *, public_url: str | None = None) -> None:
        await self.controller.start_system(public_url=public_url)
        self.evidence.step("application_started")

    def request(
        self,
        endpoint: str,
        method: str = "GET",
        data: Any = None,
        *,
        params: dict[str, Any] | None = None,
    ) -> Response:
        response = self.controller.get_api_test_client().request(
            method, endpoint, json=data, params=params
        )
        return self._record_api_response(response, endpoint, method, data, params)

    def _record_api_response(
        self,
        response: Response,
        endpoint: str,
        method: str,
        data: Any,
        params: dict[str, Any] | None,
    ) -> Response:
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
                "params": params,
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

    async def request_async(
        self,
        endpoint: str,
        method: str = "GET",
        data: Any = None,
        *,
        params: dict[str, Any] | None = None,
    ) -> Response:
        # Runtime background work and async HTTP handlers must share the event
        # loop, as they do in production. A TestClient portal owns another loop.
        client = self.controller.get_api_test_client()
        async with AsyncClient(
            transport=ASGITransport(app=client.app, client=("testclient", 50000)),
            base_url=str(client.base_url),
            headers=client.headers,
            cookies=client.cookies,
            follow_redirects=client.follow_redirects,
        ) as ingress:
            response = await ingress.request(method, endpoint, json=data, params=params)
        client.cookies.update(response.cookies)
        return self._record_api_response(response, endpoint, method, data, params)

    async def wait_task(self, task_id: str) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + 60
        while True:
            response = await self.request_async(f"/api/tasks/{task_id}")
            assert response.status_code == 200, response.text
            task: dict[str, Any] = response.json()
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
async def app(
    scenario_run: ScenarioRun, request: pytest.FixtureRequest
) -> AsyncIterator[Application]:
    evidence = scenario_run
    from core.runtime.paths import (
        restore_bootstrap_roots,
        set_bootstrap_roots,
        snapshot_bootstrap_roots,
    )
    from core.runtime.state import has_runtime_context

    if _uncertain_fixture_failure is not None or has_runtime_context():
        previous = _uncertain_fixture_failure
        error = RuntimeError("Previous application ownership is unconfirmed")
        _stop_after_fixture_failure(request, evidence, error)
        raise error from previous
    roots = snapshot_bootstrap_roots()
    core_case = request.node.path.resolve().is_relative_to(
        Path(__file__).parent / "integration/core"
    )
    from validation.support.core_processes import reset_core_commands

    if core_case:
        reset_core_commands(evidence.root)
    environment_names = {"ASSISTANTMD_SECRETS_KEY", "LOGFIRE_TOKEN"}
    if core_case:
        environment_names.update(
            name for name in os.environ if _ambient_credential(name)
        )
    environment = {name: os.environ.get(name) for name in environment_names}
    try:
        controller = None
        try:
            key = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
            evidence.register_secret(key)
            if core_case:
                for name in environment:
                    os.environ.pop(name, None)
            os.environ["ASSISTANTMD_SECRETS_KEY"] = key
            os.environ.pop("LOGFIRE_TOKEN", None)
            warmup = evidence.root / "bootstrap"
            for name in ("data", "system"):
                (warmup / name).mkdir(parents=True)
            set_bootstrap_roots(warmup / "data", warmup / "system")
            from core.logger import (
                capture_validation_events,
                close_activity_log_for_root,
            )
            from validation.support.system_controller import SystemController
            from validation.support.vault_manager import VaultManager

            events = evidence.observations
            with capture_validation_events(
                evidence.artifacts, sink=events.capture, projector=evidence.redact
            ):
                controller = SystemController(evidence.root, secret_key=key)
                try:
                    application = Application(
                        controller, VaultManager(evidence.root), evidence, events
                    )
                    evidence.step("application_fixture_ready")
                    yield application
                finally:
                    try:
                        await controller.close()
                        if controller.cleanup_failure is not None:
                            raise RuntimeError(
                                "Previous application shutdown failed; cleanup is unconfirmed"
                            ) from controller.cleanup_failure
                    finally:
                        evidence.save(
                            "lifecycle.json",
                            {
                                "runtime_active_after_close": has_runtime_context(),
                                "controller_cleanup_confirmed": controller.cleanup_failure
                                is None,
                                "event_count": events.checkpoint(),
                            },
                        )
                        evidence.step(
                            "application_closed", runtime_active=has_runtime_context()
                        )
                        close_activity_log_for_root(evidence.root / "system")
                        close_activity_log_for_root(warmup / "system")
        finally:
            try:
                for module_name, reset in (
                    ("core.settings.store", "refresh_settings_cache"),
                    ("core.secrets", "reset_secrets_bootstrap_status"),
                    ("core.llm.model_utils", "invalidate_model_cache"),
                ):
                    module = sys.modules.get(module_name)
                    if module is not None:
                        getattr(module, reset)()
            finally:
                restore_bootstrap_roots(roots)
                if core_case:
                    reset_core_commands()
                if core_case:
                    for name in list(os.environ):
                        if _ambient_credential(name):
                            os.environ.pop(name, None)
                for name, value in environment.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value
    except BaseException as error:
        _stop_after_fixture_failure(request, evidence, error)
        raise
