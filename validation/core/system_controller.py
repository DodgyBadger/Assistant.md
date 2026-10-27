"""
System lifecycle controller for V2 validation scenarios.

Manages AssistantMD system startup, shutdown, and configuration.
"""

import asyncio
import base64
import datetime as dt_module
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
from fastapi import FastAPI
from fastapi.testclient import TestClient

import core.authoring.runtime.host as authoring_host_module
from api.application import create_application
from core.authentication import load_authentication_policy
from core.authoring.template_discovery import discover_vaults
from core.logger import UnifiedLogger
from core.runtime.bootstrap import bootstrap_runtime
from core.runtime.config import RuntimeConfig
from core.runtime.paths import (
    BootstrapRoots,
    restore_bootstrap_roots,
    set_bootstrap_roots,
    snapshot_bootstrap_roots,
)
from core.runtime.state import (
    clear_runtime_context,
    get_runtime_context,
    has_runtime_context,
)
from core.secrets import reset_secrets_bootstrap_status
from core.secrets.crypto import SECRET_KEY_ENV
from core.settings import AppSettings


class SchedulerJobInfo:
    """Information about a scheduler job for validation."""

    def __init__(self, job_id: str, name: str, trigger: str, args: list[Any]):
        self.job_id = job_id
        self.name = name
        self.trigger = trigger
        self.args = args


class SystemController:
    """Controls AssistantMD system lifecycle for validation."""

    def __init__(self, run_path: Path, *, secret_key: str | None = None) -> None:
        self.run_path = run_path
        self.logger = UnifiedLogger(
            tag="system-controller",
            default_sinks=["validation", "logfire"],
        )
        self.is_running = False
        self._process: subprocess.Popen | None = None

        # Test data root for scheduler jobs
        self.test_data_root = str(self.run_path / "test_vaults")
        self._system_root = self.run_path / "system"
        self._original_secret_key: str | None = None
        self._original_roots: BootstrapRoots | None = None
        self._original_datetime: Any = None
        self._prepared = False
        self._acquiring = False
        self._validation_secret_key = (
            secret_key
            if secret_key is not None
            else base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
        )

        # Store current date for restoration
        self._current_test_date = None
        # Runtime context instead of direct component access
        self._runtime = None
        self._discovered_vaults: list[str] = []
        self._loaded_workflows: list[Any] = []  # Workflow definitions imported later
        self._startup_errors: list[Any] = []  # ConfigurationError imported later
        self._startup_results: dict[str, Any] = {}
        self._context_manager_now: datetime | None = None

        # Job execution tracking
        self._job_executions: dict[str, list[datetime]] = {}
        self._pending_jobs: dict[str, asyncio.Future] = {}

        # FastAPI app + test client mirroring production router
        self._api_app: FastAPI | None = None
        self._api_client: TestClient | None = None

    def prepare(self) -> None:
        """Acquire isolated roots, secrets and the shared API client."""
        if self._prepared:
            return
        if self._acquiring:
            raise RuntimeError(
                "Validation preparation failed; close controller before retrying"
            )
        if has_runtime_context():
            raise RuntimeError(
                "Cannot prepare validation while another runtime is active"
            )
        self._original_roots = snapshot_bootstrap_roots()
        self._original_secret_key = os.environ.get(SECRET_KEY_ENV)
        self._original_datetime = authoring_host_module.datetime
        self._acquiring = True
        Path(self.test_data_root).mkdir(parents=True, exist_ok=True)
        self._system_root.mkdir(parents=True, exist_ok=True)
        set_bootstrap_roots(Path(self.test_data_root), self._system_root)
        self._seed_validation_settings()
        from core.llm.model_utils import refresh_model_cache

        refresh_model_cache()
        os.environ[SECRET_KEY_ENV] = self._validation_secret_key
        if self._api_app is None:
            self._api_app = self._create_api_app()
        if self._api_client is None:
            self._api_client = TestClient(self._api_app)
        self._prepared = True

    def _seed_validation_settings(self) -> None:
        """Seed validation settings with the deterministic model provider."""
        from validation.core.configuration import seed_configuration

        seed_configuration(self._system_root)

    def _create_api_app(self) -> FastAPI:
        """Construct FastAPI app matching production router for validation."""
        policy = load_authentication_policy(
            AppSettings(ASSISTANTMD_AUTH_MODE="disabled")
        )
        app = create_application(
            authentication_policy=policy,
            include_ui=True,
        )
        app.state.runtime = None
        return app

    async def start_system(self):
        """Start real system components with test data isolation using runtime bootstrap."""
        if self.is_running:
            return
        bootstrapping = False
        try:
            self.prepare()
            if has_runtime_context():
                raise RuntimeError(
                    "Cannot start validation while another runtime is active"
                )
            reset_secrets_bootstrap_status()
            # Create runtime configuration for validation
            config = RuntimeConfig.for_validation(
                run_path=self.run_path, test_data_root=Path(self.test_data_root)
            )

            # Bootstrap runtime services with test configuration
            bootstrapping = True
            self._runtime = await bootstrap_runtime(config)
            bootstrapping = False
            if self._context_manager_now is not None:
                self._runtime.config.features["context_manager_now"] = (
                    self._context_manager_now.isoformat()
                )

            # Add job execution listeners
            self._runtime.scheduler.add_listener(
                self._on_job_executed, EVENT_JOB_EXECUTED
            )
            self._runtime.scheduler.add_listener(self._on_job_error, EVENT_JOB_ERROR)

            # Cache validation data for interface compatibility
            self._discovered_vaults = discover_vaults(self.test_data_root)
            self._loaded_workflows = self._runtime.workflow_loader._workflows.copy()
            self._startup_errors = (
                self._runtime.workflow_loader.get_configuration_errors()
            )
            # Get startup results from runtime context summary
            self._startup_results = {
                "vaults_discovered": len(self._discovered_vaults),
                "workflows_loaded": len(self._loaded_workflows),
                "enabled_workflows": len(
                    [w for w in self._loaded_workflows if w.enabled]
                ),
                "scheduler_jobs_synced": len(self._runtime.scheduler.get_jobs()),
            }

            self.is_running = True
            assert self._api_app is not None
            self._api_app.state.runtime = self._runtime

        except BaseException as e:
            if bootstrapping and self._runtime is None and has_runtime_context():
                self._runtime = get_runtime_context()
            try:
                await self.stop_system()
            except BaseException as cleanup_error:
                e.add_note(
                    f"Validation startup cleanup failed: {type(cleanup_error).__name__}"
                )
            raise

    async def stop_system(self):
        """Stop the system gracefully using runtime context."""
        runtime = self._runtime
        try:
            if runtime is not None:
                await runtime.shutdown()
        finally:
            self._runtime = None
            if (
                runtime is not None
                and has_runtime_context()
                and get_runtime_context() is runtime
            ):
                clear_runtime_context()
            if self._acquiring:
                if not has_runtime_context():
                    reset_secrets_bootstrap_status()
                authoring_host_module.datetime = self._original_datetime
                self._current_test_date = None
                if self._original_secret_key is None:
                    os.environ.pop(SECRET_KEY_ENV, None)
                else:
                    os.environ[SECRET_KEY_ENV] = self._original_secret_key
                assert self._original_roots is not None
                restore_bootstrap_roots(self._original_roots)
                self._prepared = False
                self._acquiring = False
            if self._api_app is not None:
                self._api_app.state.runtime = None
            for future in self._pending_jobs.values():
                if not future.done():
                    future.cancel()
            self._pending_jobs.clear()
            self.is_running = False
            if self._process:
                process, self._process = self._process, None
                process.terminate()
                process.wait(timeout=10)

    async def close(self) -> None:
        """Release all controller resources, including its owned test client."""
        try:
            await self.stop_system()
        finally:
            if self._api_client is not None:
                self._api_client.close()
                self._api_client = None
            self._api_app = None

    def set_context_manager_now(self, value: datetime | None) -> None:
        """Override cache clock used by context manager in validation runs."""
        self._context_manager_now = value
        if self._runtime is None:
            return
        if value is None:
            self._runtime.config.features.pop("context_manager_now", None)
        else:
            self._runtime.config.features["context_manager_now"] = value.isoformat()

    async def restart_system(self):
        """Full system restart cycle."""
        await self.stop_system()
        await asyncio.sleep(1)
        await self.start_system()

    async def trigger_vault_rescan(self):
        """Force system to rescan for new vaults/workflows using runtime context."""
        if not self.is_running:
            raise RuntimeError("System must be running to trigger rescan")

        self.logger.info("Triggering vault rescan")

        # Use runtime context reload functionality
        results = await self._runtime.reload_workflows(manual=True)

        # Update cached data for interface compatibility
        self._discovered_vaults = discover_vaults(self.test_data_root)
        self._loaded_workflows = self._runtime.workflow_loader._workflows.copy()
        self._startup_errors = self._runtime.workflow_loader.get_configuration_errors()
        self._startup_results = results

    def get_discovered_vaults(self) -> list[str]:
        """Get vaults discovered during startup using real discovery logic."""
        return self._discovered_vaults.copy()

    def get_loaded_workflows(self) -> list[Any]:
        """Get parsed workflow configurations using real workflow_loader."""
        return self._loaded_workflows.copy()

    def get_scheduler_jobs(self) -> list[SchedulerJobInfo]:
        """Get actual APScheduler job objects for validation."""
        if not self._runtime or not self._runtime.scheduler:
            return []

        jobs = []
        for job in self._runtime.scheduler.get_jobs():
            job_info = SchedulerJobInfo(
                job_id=job.id,
                name=job.name,
                trigger=str(job.trigger),
                args=list(job.args),
            )
            jobs.append(job_info)

        return jobs

    def get_startup_errors(self) -> list[Any]:
        """Get configuration errors encountered during startup."""
        return self._startup_errors.copy()

    def get_startup_results(self) -> dict[str, Any]:
        """Get complete startup results including statistics."""
        return self._startup_results.copy()

    def get_api_test_client(self) -> TestClient:
        """Expose shared FastAPI TestClient matching production routing."""
        self.prepare()
        assert self._api_client is not None
        return self._api_client

    def get_job_property(self, global_id: str, property_name: str):
        """Get any property from a scheduled job in APScheduler."""
        if not self._runtime or not self._runtime.scheduler:
            raise RuntimeError("System must be running to get job properties")

        # Convert global_id to scheduler job_id
        safe_job_id = global_id.replace("/", "__")

        # Find the job in APScheduler
        job = next(
            (
                job
                for job in self._runtime.scheduler.get_jobs()
                if job.id == safe_job_id
            ),
            None,
        )
        if not job:
            raise ValueError(f"Job {safe_job_id} not found in scheduler")

        # Get the requested property
        if not hasattr(job, property_name):
            raise ValueError(f"Job does not have property: {property_name}")

        return getattr(job, property_name)

    def _on_job_executed(self, event):
        """Handle job execution events from APScheduler."""
        job_id = event.job_id
        execution_time = datetime.now()

        # Track execution
        if job_id not in self._job_executions:
            self._job_executions[job_id] = []
        self._job_executions[job_id].append(execution_time)

        # Complete any pending futures waiting for this job
        if job_id in self._pending_jobs:
            future = self._pending_jobs.pop(job_id)
            if not future.done():
                future.set_result(execution_time)

        self.logger.info(f"Job executed: {job_id} at {execution_time}")
        self.logger.set_sinks(["validation"]).info(
            "job_executed",
            data={
                "job_id": job_id,
                "global_id": job_id.replace("__", "/"),
                "executed_at": execution_time.isoformat(),
            },
        )

    def _on_job_error(self, event):
        """Handle job error events from APScheduler."""
        job_id = event.job_id
        exception = event.exception

        # Complete any pending futures with the exception
        if job_id in self._pending_jobs:
            future = self._pending_jobs.pop(job_id)
            if not future.done():
                future.set_exception(exception)

        self.logger.error(f"Job failed: {job_id} - {exception}")

    async def wait_for_scheduled_run(
        self,
        global_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> bool:
        """Wait for a scheduled job to execute."""
        if not self.is_running:
            raise RuntimeError("System must be running to wait for scheduled runs")

        # Convert global_id to scheduler job_id
        safe_job_id = global_id.replace("/", "__")

        # Check if job exists
        job = next(
            (
                job
                for job in self._runtime.scheduler.get_jobs()
                if job.id == safe_job_id
            ),
            None,
        )
        if not job:
            raise ValueError(f"Job {safe_job_id} not found in scheduler")

        # Create future to wait for execution
        future = asyncio.Future()
        self._pending_jobs[safe_job_id] = future

        try:
            if timeout_seconds is None:
                await future
            else:
                await asyncio.wait_for(future, timeout=timeout_seconds)
            return True
        except TimeoutError as exc:
            self._pending_jobs.pop(safe_job_id, None)
            job = next(
                (
                    job
                    for job in self._runtime.scheduler.get_jobs()
                    if job.id == safe_job_id
                ),
                None,
            )
            pending_job_ids = sorted(self._pending_jobs)
            raise AssertionError(
                "Scheduled job did not complete "
                f"within {timeout_seconds:g}s: "
                f"global_id={global_id}, "
                f"job_id={safe_job_id}, "
                f"scheduler_running={self._runtime.scheduler.running if self._runtime else None}, "
                f"next_run_time={getattr(job, 'next_run_time', None) if job else None}, "
                f"execution_count={len(self._job_executions.get(safe_job_id, []))}, "
                f"pending_job_ids={pending_job_ids}"
            ) from exc
        except Exception as e:
            # Job executed but with error
            self.logger.error(f"Scheduled job {global_id} failed: {e}")
            return False

    def get_job_executions(self, global_id: str) -> list[datetime]:
        """Get execution times for a specific job."""
        safe_job_id = global_id.replace("/", "__")
        return self._job_executions.get(safe_job_id, []).copy()

    def trigger_job_manually(self, global_id: str):
        """Manually trigger a scheduled job for testing."""
        if not self.is_running:
            raise RuntimeError("System must be running to trigger jobs")

        safe_job_id = global_id.replace("/", "__")
        job = next(
            (
                job
                for job in self._runtime.scheduler.get_jobs()
                if job.id == safe_job_id
            ),
            None,
        )
        if not job:
            raise ValueError(f"Job {safe_job_id} not found in scheduler")

        # Trigger the job immediately
        job.modify(next_run_time=datetime.now())
        self.logger.info(
            f"Manually triggered job: {global_id}",
            data={
                "global_id": global_id,
                "job_id": safe_job_id,
            },
        )

    def set_test_date(self, test_date):
        """Set test date for scheduled job execution."""
        self.prepare()
        self._current_test_date = test_date

        # Apply datetime monkey patch for scheduled jobs
        mock_datetime = self._create_mock_datetime(test_date)
        authoring_host_module.datetime = mock_datetime

    def _create_mock_datetime(self, test_date):
        """Create mock datetime module that returns test_date for today() calls."""

        class MockDateTime:
            # Forward most methods to real datetime
            strftime = dt_module.datetime.strftime
            now = dt_module.datetime.now
            combine = dt_module.datetime.combine

            @staticmethod
            def today():
                return test_date

        return MockDateTime
