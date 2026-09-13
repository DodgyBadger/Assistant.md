"""
BaseScenario class with high-level assertion API and boundary enforcement.

This module provides the foundation for V2 validation scenarios that focus on
real user workflows with readable, high-level operations.
"""

import asyncio
import inspect
import json
import sys
from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import yaml

from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
from core.logger import UnifiedLogger
from core.runtime.execution_tasks import ExecutionTaskSource
from core.runtime.state import get_runtime_context

from .api_client import APIClient, APIResponse
from .paths import resolve_validation_root
from .system_controller import SystemController
from .time_controller import TimeController
from .vault_manager import VaultManager

# Type definitions
VaultPath = Path  # Simple alias for now


def with_local_user_authority[ScenarioResult](
    function: Callable[..., Coroutine[Any, Any, ScenarioResult]],
) -> Callable[..., Coroutine[Any, Any, ScenarioResult]]:
    """Run a direct-call scenario as the single user represented by the UI."""

    @wraps(function)
    async def wrapped(*args: Any, **kwargs: Any) -> ScenarioResult:
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            return await function(*args, **kwargs)

    return wrapped


class WorkflowResult:
    """Result from assistant workflow execution."""

    def __init__(
        self,
        status: str,
        created_files: list[str] = None,
        error_message: str = None,
        logs: list[str] = None,
    ):
        self.status = status
        self.created_files = created_files or []
        self.error_message = error_message
        self.logs = logs or []


class BaseScenario(ABC):
    """
    Base class for V2 validation scenarios with boundary enforcement.

    This class provides high-level operations that prevent scenarios from
    drifting into unit testing patterns. All methods focus on user-level
    actions and outcomes.
    """

    def __init__(self):
        """Initialize scenario with evidence collection and control systems."""
        self.scenario_name = self.__class__.__name__.replace("Test", "").replace(
            "Scenario", ""
        )
        self.logger = UnifiedLogger(
            tag=f"scenario-v2-{self.scenario_name.lower()}",
            default_sinks=["validation", "logfire"],
        )

        # Create run directory for this scenario
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_path = (
            resolve_validation_root()
            / "runs"
            / f"{timestamp}_{self.scenario_name.lower()}"
        )
        self.run_path.mkdir(parents=True, exist_ok=True)

        # Initialize artifact collection
        self.artifacts_dir = self.run_path / "artifacts"
        self.artifacts_dir.mkdir(exist_ok=True)

        self.timeline_file = self.artifacts_dir / "timeline.md"
        self.system_interactions_file = self.artifacts_dir / "system_interactions.log"
        # Initialize timeline
        self._init_timeline()

        # Initialize control systems (lazy loaded)
        self._vault_manager = None
        self._system_controller = None
        self._time_controller = None
        self._api_client = None
        self._soft_failures: list[dict[str, str]] = []
        self._teardown_completed = False

    # === ABSTRACT METHOD ===

    @abstractmethod
    async def test_scenario(self):
        """
        Main test method - implement scenario logic here.

        This method should read like a user story using the high-level
        assertion and control methods provided by this base class.

        Note: This is async to support real workflow execution.
        """
        pass

    # === VAULT SETUP ===

    def create_vault(self, name: str) -> VaultPath:
        """Create clean vault for testing."""
        self._log_timeline(f"Creating vault: {name}")
        vault_path = self._get_vault_manager().create_vault(name)
        return vault_path  # Already a Path object

    def copy_files(
        self,
        source_path: str,
        vault: VaultPath,
        dest_dir: str = "",
        dest_filename: str = None,
    ):
        """Copy files/directories from source to vault.

        Args:
            source_path: Path relative to the configured application root
            vault: Target vault
            dest_dir: Optional subdirectory within vault
            dest_filename: Optional filename to rename single file (allows overwriting)
        """
        rename_info = f" as {dest_filename}" if dest_filename else ""
        self._log_timeline(
            f"Copying files: {source_path} → {vault.name}/{dest_dir or 'root'}{rename_info}"
        )
        self._get_vault_manager().copy_files(
            source_path, vault, dest_dir, dest_filename
        )

    def create_file(self, vault: VaultPath, file_path: str, content: str):
        """Create single file with content in vault.

        Args:
            vault: Target vault
            file_path: Path within vault
            content: File content (can be large block of text)
        """
        self._log_timeline(f"Creating file: {file_path}")
        self._get_vault_manager().create_file(vault, file_path, content)

    def make_pdf(self, text: str) -> bytes:
        """Create a minimal PDF and return its bytes."""
        try:
            import fitz  # PyMuPDF
        except ImportError as exc:
            raise RuntimeError(
                "PyMuPDF (fitz) is required to generate test PDFs"
            ) from exc

        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), text)
        return doc.tobytes()

    # === TIME & SYSTEM CONTROL ===

    def set_date(self, date: str):
        """Set system date for testing (@run-on, time patterns)."""
        self._log_timeline(f"Setting system date: {date}")
        time_controller = self._get_time_controller()
        time_controller.set_date(date)

        # Also apply datetime monkey patch for scheduled jobs (same pattern as path isolation)
        if hasattr(self, "_system_controller") and self._system_controller:
            self._system_controller.set_test_date(time_controller.current_test_date)

    def advance_time(self, hours: int = 1, minutes: int = 0):
        """Advance the reference date used for time patterns (does not poke scheduler)."""
        self._log_timeline(f"Advancing time: +{hours}h {minutes}m")
        self._get_time_controller().advance_time(hours=hours, minutes=minutes)

    def set_context_manager_now(self, value: datetime | None):
        """Override context manager cache clock (validation only)."""
        if value is None:
            label = "clearing override"
        else:
            label = value.isoformat()
        self._log_timeline(f"Setting context manager cache time: {label}")
        self._get_system_controller().set_context_manager_now(value)

    async def start_system(self):
        """Start the AssistantMD system."""
        self._log_timeline("Starting AssistantMD system")
        controller = self._get_system_controller()
        time_controller = self._time_controller
        if time_controller and time_controller.current_test_date is not None:
            controller.set_test_date(time_controller.current_test_date)
        await controller.start_system()

    async def stop_system(self):
        """Stop the system gracefully."""
        self._log_timeline("Stopping AssistantMD system")
        await self._get_system_controller().stop_system()

    async def restart_system(self):
        """Full system restart cycle."""
        self._log_timeline("Restarting AssistantMD system")
        await self._get_system_controller().restart_system()

    # === WORKFLOW EXECUTION ===

    async def run_workflow(
        self,
        vault: VaultPath,
        workflow_name: str,
        step_name: str = None,
        expect_failure: bool = False,
    ) -> WorkflowResult:
        """Manually trigger workflow execution via the public API."""
        self._log_timeline(f"Running workflow: {workflow_name} in vault {vault.name}")
        if expect_failure:
            self.logger.info(
                "Expected workflow failure case starting",
                data={
                    "vault": vault.name,
                    "workflow_name": workflow_name,
                    "step_name": step_name,
                },
            )

        files_before = self._collect_vault_files(vault)
        try:
            execution_result = (
                await get_runtime_context().workflow_governor.execute_workflow(
                    global_id=f"{vault.name}/{workflow_name}",
                    source=ExecutionTaskSource.API,
                    authority=LOCAL_USER_AUTHORITY,
                    step_name=step_name,
                    expect_failure=expect_failure,
                )
            )
        except Exception as exc:
            error_message = str(exc) or "Workflow execution failed"
            if expect_failure:
                self._log_timeline(f"✅ Workflow failed as expected: {error_message}")
                self.logger.info(
                    "Expected workflow failure observed",
                    data={
                        "vault": vault.name,
                        "workflow_name": workflow_name,
                        "step_name": step_name,
                        "error_message": error_message,
                    },
                )
            else:
                self._log_timeline(f"❌ Workflow failed: {error_message}")
            return WorkflowResult(status="failed", error_message=error_message)

        if execution_result.status not in {"completed", "skipped"}:
            return WorkflowResult(
                status=execution_result.status or "failed",
                error_message=execution_result.reason or "Workflow execution failed",
            )

        files_after = self._collect_vault_files(vault)
        created_files = sorted(files_after - files_before)
        self._log_timeline(
            f"✅ Workflow completed successfully. Created {len(created_files)} files"
        )
        for file_path in created_files:
            self._log_timeline(f"   📄 Created: {file_path}")

        return WorkflowResult(status="completed", created_files=created_files)

    async def _wait_for_execution_task(
        self,
        task_id: str,
        *,
        timeout_seconds: float = 60.0,
    ) -> dict[str, Any]:
        """Poll the public task endpoint until a workflow task reaches a terminal state."""
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        terminal_statuses = {"completed", "failed", "cancelled", "timed_out", "skipped"}
        while True:
            snapshot = await get_runtime_context().task_coordinator.get_task(task_id)
            if snapshot is None:
                raise AssertionError(f"Execution task lookup failed for {task_id}")
            task = snapshot.__dict__.copy()
            task["metadata"] = dict(snapshot.metadata or {})
            if task.get("status") in terminal_statuses:
                return task
            if asyncio.get_running_loop().time() >= deadline:
                raise AssertionError(
                    f"Execution task did not finish within {timeout_seconds:g}s: {task_id}"
                )
            await asyncio.sleep(0.25)

    async def trigger_job(
        self,
        vault: VaultPath,
        assistant_name: str,
        *,
        timeout_seconds: float | None = None,
    ) -> bool:
        """Trigger a scheduled job and wait for completion."""
        global_id = f"{vault.name}/{assistant_name}"
        self._log_timeline(f"Triggering job: {global_id}")

        # Trigger the job
        self._get_system_controller().trigger_job_manually(global_id)

        # Wait for completion; most scenarios keep production-like unbounded waits.
        self._log_timeline(f"Waiting for job completion: {global_id}")
        success = await self._get_system_controller().wait_for_scheduled_run(
            global_id,
            timeout_seconds=timeout_seconds,
        )

        if success:
            self._log_timeline(f"✅ Job completed successfully: {global_id}")
        else:
            self._log_timeline(f"❌ Job execution timeout: {global_id}")

        return success

    async def trigger_vault_rescan(self):
        """Force system to rescan for new vaults/workflows via the public API."""
        self._log_timeline("Triggering vault rescan")
        self.call_api("/api/vaults/rescan", method="POST")

    # === VALIDATION EVENT HELPERS ===

    def validation_events(self) -> list[dict[str, Any]]:
        """Load validation events from artifact files."""
        events_dir = self.run_path / "artifacts" / "validation_events"
        events: list[dict[str, Any]] = []
        if not events_dir.exists():
            return events

        for path in sorted(events_dir.glob("*.yaml")):
            events.append(self.load_yaml(path) or {})

        return events

    def event_checkpoint(self) -> int:
        """Return the current count of validation events."""
        return len(self.validation_events())

    def events_since(self, checkpoint: int) -> list[dict[str, Any]]:
        """Return events emitted after the provided checkpoint."""
        events = self.validation_events()
        return events[checkpoint:]

    def find_events(
        self,
        events: list[dict[str, Any]] | None = None,
        *,
        name: str | None = None,
        data: dict[str, Any] | None = None,
        **data_subset: Any,
    ) -> list[dict[str, Any]]:
        """Find events by name and partial data match."""
        if events is None:
            events = self.validation_events()

        expected = dict(data or {})
        expected.update(data_subset)

        matches = []
        for event in events:
            if name and event.get("name") != name:
                continue
            if expected and not self._event_data_matches(
                event.get("data", {}), expected
            ):
                continue
            matches.append(event)
        return matches

    def latest_event(
        self,
        events: list[dict[str, Any]] | None = None,
        *,
        name: str | None = None,
        data: dict[str, Any] | None = None,
        **data_subset: Any,
    ) -> dict[str, Any] | None:
        """Return the most recent matching event."""
        matches = self.find_events(events, name=name, data=data, **data_subset)
        return matches[-1] if matches else None

    def assert_event_contains(
        self,
        events: list[dict[str, Any]] | None = None,
        *,
        name: str,
        expected: dict[str, Any],
    ) -> dict[str, Any]:
        """Assert a matching event exists and return it."""
        matches = self.find_events(events, name=name, data=expected)
        if not matches:
            raise AssertionError(f"No validation event '{name}' matched: {expected}")
        return matches[-1]

    # === SOFT ASSERTIONS ===

    def soft_assert(self, condition: bool, message: str):
        """Record assertion failures without interrupting scenario execution."""
        if condition:
            return
        caller = inspect.currentframe().f_back
        location = ""
        if caller is not None:
            location = (
                f"{caller.f_code.co_filename}:{caller.f_lineno}"
                f" ({caller.f_code.co_name})"
            )
        self._soft_failures.append(
            {
                "message": str(message),
                "location": location,
            }
        )
        if location:
            self._log_timeline(f"⚠️ Soft assert failed at {location}: {message}")
        else:
            self._log_timeline(f"⚠️ Soft assert failed: {message}")

    def soft_assert_equal(self, actual: Any, expected: Any, message: str = ""):
        """Soft assertion helper for equality checks."""
        detail = message or "Values are not equal"
        self.soft_assert(
            actual == expected,
            f"{detail} | expected={expected!r}, actual={actual!r}",
        )

    def soft_assert_event_contains(
        self,
        events: list[dict[str, Any]] | None = None,
        *,
        name: str,
        expected: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Soft variant of assert_event_contains; returns event when found."""
        try:
            return self.assert_event_contains(events, name=name, expected=expected)
        except AssertionError as exc:
            self.soft_assert(False, str(exc))
            return None

    def assert_no_failures(self):
        """Raise one aggregated assertion if any soft assertions failed."""
        if not self._soft_failures:
            return
        lines = ["Soft assertion failures:"]
        for idx, failure in enumerate(self._soft_failures, start=1):
            message = failure.get("message", "Assertion failed")
            location = failure.get("location", "")
            if location:
                lines.append(f"{idx}. {message} @ {location}")
            else:
                lines.append(f"{idx}. {message}")
        raise AssertionError("\n".join(lines))

    # === API ENDPOINT TESTING ===

    def call_api(
        self,
        endpoint: str,
        method: str = "GET",
        data: dict = None,
        params: dict = None,
        headers: dict = None,
    ) -> APIResponse:
        """Call AssistantMD API endpoint."""
        self._log_timeline(f"API call: {method} {endpoint}")
        response = self._get_api_client().call_api(
            endpoint, method, data, params=params, headers=headers
        )
        self._log_timeline(f"   -> Status {response.status_code}")
        return response

    async def call_api_async(
        self,
        endpoint: str,
        method: str = "GET",
        data: dict | None = None,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> APIResponse:
        """Call an endpoint without blocking runtime work on the scenario loop."""
        return await asyncio.to_thread(
            self.call_api,
            endpoint,
            method,
            data,
            params,
            headers,
        )

    async def run_chat_task(
        self,
        data: dict,
        *,
        timeout_seconds: float = 10.0,
    ) -> dict[str, Any]:
        """Start a task-owned chat turn and collect its buffered events."""
        start_response = self.call_api("/api/chat/tasks", method="POST", data=data)
        result: dict[str, Any] = {
            "start_response": start_response,
            "session_id": None,
            "task_id": None,
            "task_ids": [],
            "events": [],
            "terminal_event": None,
            "text": "",
        }
        if start_response.status_code != 200:
            return result

        payload = start_response.json()
        task_id = payload.get("task", {}).get("task_id")
        result["session_id"] = payload.get("session_id")
        result["task_id"] = task_id
        result["task_ids"].append(task_id)
        if not task_id:
            return result

        from core.chat.task_execution import CHAT_TASK_EVENT_BUFFER

        async def _collect_events() -> None:
            cursor = 0
            current_task_id = task_id
            while True:
                redirected = False
                events = await CHAT_TASK_EVENT_BUFFER.events_after(
                    current_task_id, cursor
                )
                for buffered_event in events:
                    cursor = buffered_event.sequence
                    event = dict(buffered_event.data)
                    event.setdefault("event", buffered_event.event)
                    event.setdefault("sequence", buffered_event.sequence)
                    result["events"].append(event)
                    if event.get("reset_response") and event.get("event") in {
                        "chat_retry_scheduled",
                        "chat_retry_redirect",
                    }:
                        result["text"] = ""
                    if event.get("event") == "chat_retry_redirect":
                        replacement_task_id = event.get("replacement_task_id")
                        if isinstance(replacement_task_id, str) and replacement_task_id:
                            current_task_id = replacement_task_id
                            result["task_ids"].append(replacement_task_id)
                            cursor = 0
                            redirected = True
                            break
                    choices = event.get("choices") or []
                    if choices:
                        delta = choices[0].get("delta") or {}
                        content = delta.get("content")
                        if isinstance(content, str):
                            result["text"] += content
                    if buffered_event.is_terminal:
                        result["terminal_event"] = event
                        return
                if redirected:
                    continue
                await asyncio.sleep(0.01)

        try:
            await asyncio.wait_for(_collect_events(), timeout=timeout_seconds)
        except TimeoutError as exc:
            from core.runtime.state import get_runtime_context

            task = await get_runtime_context().task_coordinator.get_task(task_id)
            buffered_events = await CHAT_TASK_EVENT_BUFFER.events_after(task_id, 0)
            last_event = buffered_events[-1] if buffered_events else None
            raise AssertionError(
                "Timed out waiting for chat task terminal event "
                f"after {timeout_seconds:g}s: "
                f"task_id={task_id}, "
                f"status={task.status if task else None}, "
                f"cancel_requested={task.cancel_requested if task else None}, "
                f"terminal_reason={task.terminal_reason if task else None}, "
                f"buffered_event_count={len(buffered_events)}, "
                f"last_buffered_event={last_event.event if last_event else None}, "
                f"last_buffered_sequence={last_event.sequence if last_event else None}"
            ) from exc
        return result

    def _parse_sse_events(self, text: str) -> list[dict[str, Any]]:
        """Parse JSON payloads from an SSE response body."""
        events: list[dict[str, Any]] = []
        for block in text.split("\n\n"):
            data_lines = [
                line.removeprefix("data: ").strip()
                for line in block.splitlines()
                if line.startswith("data: ")
            ]
            if not data_lines:
                continue
            raw = "\n".join(data_lines)
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(payload)
        return events

    # === ARTIFACT COLLECTION ===

    def teardown_scenario(self):
        """Clean up after scenario execution."""
        if self._teardown_completed:
            return
        self._log_timeline("Scenario teardown - saving system interactions")
        try:
            self._save_system_interactions()
            self._teardown_completed = True
        except Exception:
            raise

    def mark_scenario_outcome(self, status: str, details: str = ""):
        """Append an explicit scenario-level final outcome to the timeline."""
        normalized = status.strip().upper()
        if details:
            self._log_timeline(f"=== SCENARIO {normalized}: {details} ===")
        else:
            self._log_timeline(f"=== SCENARIO {normalized} ===")

    # === INTERNAL HELPER METHODS ===

    def _init_timeline(self):
        """Initialize the timeline file."""
        with open(self.timeline_file, "w") as f:
            f.write(f"# {self.scenario_name} Execution Timeline\n\n")
            f.write(f"**Started**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")

    def _collect_vault_files(self, vault: VaultPath) -> set:
        """Return relative file paths for all files in a vault."""
        vault_files = set()
        if vault.exists():
            for file_path in vault.rglob("*"):
                if file_path.is_file():
                    vault_files.add(str(file_path.relative_to(vault)))
        return vault_files

    def _log_timeline(self, message: str):
        """Log action to timeline."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        with open(self.timeline_file, "a") as f:
            f.write(f"**{timestamp}**: {message}\n")

    def _save_system_interactions(self):
        """Save system interaction logs."""
        if self._api_client:
            self._api_client.save_interaction_log(self.system_interactions_file)

    def load_yaml(self, path: Path):
        """Load YAML content from a file."""
        with open(path, encoding="utf-8") as handle:
            return yaml.safe_load(handle)

    def _event_data_matches(self, actual: Any, expected: Any) -> bool:
        """Return True when expected is a deep partial match of actual."""
        if isinstance(expected, dict):
            if not isinstance(actual, dict):
                return False
            for key, value in expected.items():
                if key not in actual:
                    return False
                if not self._event_data_matches(actual[key], value):
                    return False
            return True

        if isinstance(expected, list):
            if not isinstance(actual, list):
                return False
            for item in expected:
                if not any(
                    self._event_data_matches(actual_item, item)
                    for actual_item in actual
                ):
                    return False
            return True

        return actual == expected

    # === LAZY LOADING OF CONTROL SYSTEMS ===

    def _get_vault_manager(self):
        """Lazy load vault manager."""
        if not self._vault_manager:
            self._vault_manager = VaultManager(self.run_path)
        return self._vault_manager

    def _get_system_controller(self):
        """Lazy load system controller."""
        if not self._system_controller:
            self._system_controller = SystemController(self.run_path)
        return self._system_controller

    def _get_time_controller(self):
        """Lazy load time controller."""
        if not self._time_controller:
            self._time_controller = TimeController()
        return self._time_controller

    def _get_api_client(self):
        """Lazy load API client."""
        if not self._api_client:
            test_vault_root = self.run_path / "test_vaults"
            test_vault_root.mkdir(parents=True, exist_ok=True)
            controller = self._get_system_controller()
            self._api_client = APIClient(
                test_vault_root, controller.get_api_test_client()
            )
        return self._api_client
