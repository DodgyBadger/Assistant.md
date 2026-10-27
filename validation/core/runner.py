"""Execute real application scenarios with evidence and failure classification."""

import asyncio
import importlib
import inspect
import json
import logging
import math
import os
import shutil
import sys
import tempfile
import traceback
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from validation.framework.lifecycle import ExecutionOutcome, RunContext, execute
from validation.framework.process import supervise

from .inventory import InventoryError, ScenarioCatalog, describe_scenario
from .paths import resolve_validation_root
from .results import ErrorClassification, ScenarioResult, ValidationRun

logger = logging.getLogger("validation-runner")


class ValidationRunner:
    """Validation execution engine with scenario-based testing."""

    def __init__(
        self,
        validation_root: str | Path | None = None,
        *,
        timeout_seconds: float | None = 300,
        cleanup_timeout_seconds: float = 30,
        environment_names: tuple[str, ...] = (),
        secret_environment_names: tuple[str, ...] = (),
    ):
        if timeout_seconds is not None and (
            timeout_seconds <= 0 or not math.isfinite(timeout_seconds)
        ):
            raise InventoryError("Execution timeout must be a finite positive number")
        if cleanup_timeout_seconds <= 0 or not math.isfinite(cleanup_timeout_seconds):
            raise InventoryError("Cleanup timeout must be a finite positive number")
        self.validation_root = (
            Path(validation_root) if validation_root else resolve_validation_root()
        )
        self.timeout_seconds = timeout_seconds
        self.cleanup_timeout_seconds = cleanup_timeout_seconds
        self.environment_names = environment_names
        self.secret_environment_names = secret_environment_names
        self.scenarios_dir = self.validation_root / "scenarios"
        self.runs_dir = self.validation_root / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def discover_scenarios(self) -> list[str]:
        """Collect all declared scenarios without importing application modules."""
        return ScenarioCatalog(self.validation_root).discover_scenarios()

    def execute_scenario(self, scenario_name: str) -> ScenarioResult:
        """Supervise one fresh worker without importing the application."""
        scenario_name = scenario_name.replace("\\", "/")
        try:
            directory = Path(
                tempfile.mkdtemp(
                    prefix=f"{datetime.now():%Y%m%d_%H%M%S}_{scenario_name.split('/')[-1]}_",
                    dir=self.runs_dir,
                )
            ).resolve()
            artifacts = directory / "artifacts"
            artifacts.mkdir()
        except OSError as error:
            return ScenarioResult(
                scenario_name,
                "framework_error",
                0,
                error_message=f"Worker evidence allocation failed ({type(error).__name__})",
                evidence_path=str(self.runs_dir),
                execution_phase="worker",
            )
        try:
            describe_scenario(
                self.scenarios_dir / f"{scenario_name}.py", self.scenarios_dir
            )
            environment = {
                name: os.environ[name]
                for name in (
                    "PATH",
                    "SYSTEMROOT",
                    "WINDIR",
                    "PATHEXT",
                    "LANG",
                    "LC_ALL",
                    "LC_CTYPE",
                    "TZ",
                )
                if name in os.environ
            }
            for name in (*self.environment_names, *self.secret_environment_names):
                if name in {
                    "VALIDATION_ROOT",
                    "VALIDATION_APP_ROOT",
                    "CONTAINER_DATA_ROOT",
                    "CONTAINER_SYSTEM_ROOT",
                    "PYTHONPATH",
                    "PYTHONHOME",
                    "PYTHONUSERBASE",
                    "HOME",
                    "TMPDIR",
                    "TMP",
                    "TEMP",
                    "XDG_CONFIG_HOME",
                    "XDG_CACHE_HOME",
                    "XDG_DATA_HOME",
                }:
                    raise InventoryError(
                        f"Worker execution path is framework-owned: {name}"
                    )
                if name not in os.environ:
                    raise InventoryError(
                        f"Requested worker environment variable is unset: {name}"
                    )
                environment[name] = os.environ[name]
            environment["VALIDATION_ROOT"] = str(self.validation_root.resolve())
            environment["VALIDATION_APP_ROOT"] = str(
                Path(__file__).resolve().parents[2]
            )
            for name, relative in {
                "HOME": "home",
                "TMPDIR": "tmp",
                "TMP": "tmp",
                "TEMP": "tmp",
                "XDG_CONFIG_HOME": "home/config",
                "XDG_CACHE_HOME": "home/cache",
                "XDG_DATA_HOME": "home/data",
            }.items():
                owned_path = directory / relative
                owned_path.mkdir(parents=True, exist_ok=True)
                environment[name] = str(owned_path)
            environment["PYTHONUNBUFFERED"] = "1"
            request = {
                "schema_version": 1,
                "scenario_name": scenario_name,
                "validation_root": str(self.validation_root.resolve()),
                "evidence_root": str(directory),
                "timeout_seconds": self.timeout_seconds,
                "cleanup_timeout_seconds": self.cleanup_timeout_seconds,
                "secret_environment_names": list(self.secret_environment_names),
            }
            request_path = directory / "worker-request.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            outcome = supervise(
                [
                    sys.executable,
                    "-s",
                    "-m",
                    "validation.core.worker",
                    str(request_path),
                ],
                cwd=Path(__file__).resolve().parents[2],
                env=environment,
                artifacts_dir=artifacts / "process",
                interrupt_grace_seconds=self.cleanup_timeout_seconds,
                redact_values=tuple(
                    os.environ[name] for name in self.secret_environment_names
                ),
                timeout_seconds=(
                    None
                    if self.timeout_seconds is None
                    else self.timeout_seconds + self.cleanup_timeout_seconds
                ),
            )
            process_result = asdict(outcome)
            (artifacts / "process-result.json").write_text(
                json.dumps(process_result), encoding="utf-8"
            )
            if outcome.interrupted or outcome.timed_out:
                result = ScenarioResult(
                    scenario_name,
                    "framework_error",
                    outcome.elapsed,
                    error_message=(
                        "Worker interrupted"
                        if outcome.interrupted
                        else "Worker exceeded its hard process deadline; cleanup is unconfirmed"
                    ),
                    evidence_path=str(directory),
                    execution_phase="worker",
                    timed_out=outcome.timed_out,
                    interrupted=outcome.interrupted,
                )
            else:
                payload = json.loads(
                    (directory / "worker-result.json").read_text(encoding="utf-8")
                )
                if payload.get("schema_version") != 1:
                    raise InventoryError("Invalid worker result version")
                result = ScenarioResult.from_dict(payload["result"])
                expected_exit = 130 if result.interrupted else 0 if result.passed else 1
                if (
                    result.scenario_name != scenario_name
                    or result.evidence_path != str(directory)
                    or outcome.returncode != expected_exit
                ):
                    raise InventoryError(
                        "Worker result identity or exit status mismatch"
                    )
                if result.passed:
                    lifecycle = json.loads(
                        (artifacts / "lifecycle-result.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    if (
                        lifecycle.get("schema_version") != 1
                        or lifecycle.get("passed") is not True
                    ):
                        raise InventoryError(
                            "Worker success lacks a passing lifecycle result"
                        )
        except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
            result = ScenarioResult(
                scenario_name,
                "framework_error",
                0,
                error_message="Worker supervision interrupted",
                evidence_path=str(directory),
                execution_phase="worker",
                interrupted=True,
            )
        except Exception as error:
            # Do not echo environment values or untrusted worker payloads here.
            result = ScenarioResult(
                scenario_name,
                "framework_error",
                0,
                error_message=f"Worker protocol or launch failed ({type(error).__name__}); inspect process evidence",
                evidence_path=str(directory),
                execution_phase="worker",
            )
        final = artifacts / "supervisor-result.json"
        temporary = final.with_suffix(".tmp")
        try:
            temporary.write_text(
                json.dumps({"schema_version": 1, "result": result.to_dict()}),
                encoding="utf-8",
            )
            temporary.replace(final)
        except OSError as error:
            result = replace(
                result,
                status="framework_error" if result.passed else result.status,
                error_message=result.error_message
                or "Could not persist supervisor result",
                execution_phase=result.execution_phase or "evidence",
                lifecycle_errors=[
                    *result.lifecycle_errors,
                    {
                        "phase": "evidence",
                        "name": "supervisor_result",
                        "exception_type": type(error).__name__,
                        "message": "Could not persist supervisor result",
                    },
                ],
            )
        return result

    def _execute_scenario_in_process(
        self,
        scenario_name: str,
        directory: Path,
        *,
        secret_inputs: dict[str, str] | None = None,
    ) -> ScenarioResult:
        """
        Execute a single scenario directly.

        Args:
            scenario_name: Scenario name, may include folder path (e.g., "integration/basic_haiku")
        """
        start_time = datetime.now()
        scenario_name = scenario_name.replace("\\", "/")
        scenario_instance = None
        context = None

        def redact_error(value: str) -> str:
            if context is not None:
                return context.redact(value)
            for secret in sorted((secret_inputs or {}).values(), key=len, reverse=True):
                if secret:
                    value = value.replace(secret, "[REDACTED]")
            return value

        try:
            scenario_file = self.scenarios_dir / f"{scenario_name}.py"
            if not scenario_file.exists():
                raise FileNotFoundError(f"Scenario file not found: {scenario_file}")
            definition = describe_scenario(scenario_file, self.scenarios_dir)
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                raise InventoryError(
                    "The synchronous runner cannot execute inside an active event loop; use the async lifecycle API"
                )
            from validation.adapters.assistantmd import AssistantMDAdapter

            context = RunContext(directory, secrets=secret_inputs)
            adapter = AssistantMDAdapter()

            async def body() -> None:
                nonlocal scenario_instance
                # This facade owns asyncio.run's loop and its default executor.
                # Drain worker threads after application closure, while capture is active.
                loop = asyncio.get_running_loop()
                context.defer_cleanup("runner.executor", loop.shutdown_default_executor)
                module = importlib.import_module(
                    f"validation.scenarios.{scenario_name.replace('/', '.')}"
                )
                from validation.core.base_scenario import BaseScenario

                imported_source = getattr(module, "__file__", None)
                if (
                    imported_source is None
                    or Path(imported_source).resolve() != scenario_file.resolve()
                ):
                    raise InventoryError(
                        f"Imported scenario source differs from selected source: {scenario_file}"
                    )
                scenario_class = getattr(module, definition.class_name, None)
                if not inspect.isclass(scenario_class) or not issubclass(
                    scenario_class, BaseScenario
                ):
                    raise InventoryError(
                        f"Invalid runtime scenario class: {scenario_name}"
                    )
                scenario_instance = scenario_class()
                value = scenario_instance.test_scenario()
                if inspect.isawaitable(value):
                    await value
                scenario_instance.assert_no_failures()

            def finalize(outcome: ExecutionOutcome) -> None:
                if scenario_instance is None:
                    return
                details = ""
                if outcome.primary_error is not None:
                    details = context.redact(
                        self._format_scenario_error(
                            outcome.primary_error, scenario_name
                        )
                    )
                elif outcome.cleanup_errors or outcome.evidence_errors:
                    details = "Validation cleanup or evidence finalization failed"
                scenario_instance.mark_scenario_outcome(
                    "passed" if outcome.passed else "failed", details
                )

            outcome = asyncio.run(
                execute(
                    context,
                    adapter,
                    body,
                    timeout_seconds=self.timeout_seconds,
                    cleanup_timeout_seconds=self.cleanup_timeout_seconds,
                    finalize=finalize,
                )
            )
            secondary = [
                asdict(error)
                for error in (*outcome.cleanup_errors, *outcome.evidence_errors)
            ]
            primary = outcome.primary_error
            if primary is None and outcome.timed_out:
                primary = TimeoutError(
                    "Validation execution or cleanup exceeded its deadline"
                )
            if primary is None and secondary:
                primary = RuntimeError(
                    "Validation lifecycle did not complete: " + secondary[0]["message"]
                )
            classification = None
            error_msg = None
            stack_trace = None
            if primary is not None:
                classification = self._classify_error(primary, scenario_name)
                if (
                    outcome.primary_phase == "setup"
                    or outcome.primary_error is None
                    or outcome.cancelled
                    or outcome.timed_out
                ):
                    classification = self._classify_error(
                        InventoryError(str(primary)), scenario_name
                    )
                error_msg = context.redact(
                    self._format_scenario_error(primary, scenario_name)
                )
                stack_trace = context.redact(
                    "".join(traceback.format_exception(primary))
                )
            status = (
                "passed"
                if outcome.passed
                else classification.status if classification else "framework_error"
            )
            return ScenarioResult(
                scenario_name=scenario_name,
                status=status,
                execution_time=(datetime.now() - start_time).total_seconds(),
                error_message=error_msg,
                error_classification=classification,
                evidence_path=str(directory),
                stack_trace=stack_trace,
                execution_phase=outcome.primary_phase
                or (
                    "cleanup"
                    if outcome.cleanup_errors
                    else "evidence" if outcome.evidence_errors else None
                ),
                lifecycle_errors=secondary,
                timed_out=outcome.timed_out,
                interrupted=outcome.cancelled
                or isinstance(outcome.primary_error, SystemExit),
            )
        except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
            closing_errors = self._close_unmanaged_context(context)
            return ScenarioResult(
                scenario_name,
                "framework_error",
                (datetime.now() - start_time).total_seconds(),
                error_message="Validation interrupted before lifecycle completion",
                evidence_path=str(context.root if context else self.runs_dir),
                execution_phase="interrupted",
                interrupted=True,
                lifecycle_errors=closing_errors,
            )
        except Exception as error:
            closing_errors = self._close_unmanaged_context(context)
            classification = self._classify_error(error, scenario_name)
            message = self._format_scenario_error(error, scenario_name)
            trace = traceback.format_exc()
            return ScenarioResult(
                scenario_name=scenario_name,
                status=classification.status,
                execution_time=(datetime.now() - start_time).total_seconds(),
                error_message=redact_error(message),
                error_classification=classification,
                lifecycle_errors=closing_errors,
                evidence_path=str(context.root if context else self.runs_dir),
                stack_trace=redact_error(trace),
            )

    @staticmethod
    def _close_unmanaged_context(context: RunContext | None) -> list[dict[str, str]]:
        if context is None or context.closed:
            return []
        try:
            context.close()
        except BaseException as error:
            return [
                {
                    "phase": "evidence",
                    "name": "unmanaged_close",
                    "exception_type": type(error).__name__,
                    "message": context.redact(str(error)),
                    "traceback": context.redact(
                        "".join(traceback.format_exception(error))
                    ),
                }
            ]
        return []

    def _format_scenario_error(
        self, exception: BaseException, scenario_name: str
    ) -> str:
        """Build a concise, actionable error message for scenario failures."""
        exc_type = type(exception).__name__
        exc_text = str(exception).strip()
        if not exc_text and exc_type == "AssertionError":
            exc_text = "Assertion failed"
        elif not exc_text:
            exc_text = "No additional error message"

        extracted_tb = traceback.extract_tb(exception.__traceback__)
        target_frame = None

        scenario_rel = f"validation/scenarios/{scenario_name}.py".replace("\\", "/")
        for frame in reversed(extracted_tb):
            normalized = frame.filename.replace("\\", "/")
            if normalized.endswith(scenario_rel):
                target_frame = frame
                break

        if target_frame is None and extracted_tb:
            target_frame = extracted_tb[-1]

        location = ""
        source_line = ""
        if target_frame is not None:
            location = (
                f"{target_frame.filename}:{target_frame.lineno} "
                f"(in {target_frame.name})"
            )
            source_line = (target_frame.line or "").strip()

        tail = ""
        if extracted_tb:
            tail_frames = extracted_tb[-4:]
            tail = "".join(traceback.format_list(tail_frames)).strip()

        lines = [f"Scenario execution error: {exc_type}: {exc_text}"]
        if location:
            lines.append(f"Failure location: {location}")
        if source_line:
            lines.append(f"Failing code: {source_line}")
        if tail:
            lines.append("Traceback tail:")
            lines.append(tail)

        return "\n".join(lines)

    def _classify_error(
        self, exception: BaseException, scenario_name: str
    ) -> ErrorClassification:
        """Classify scenario and infrastructure failures for reports."""
        exception_type = type(exception).__name__

        if exception_type in ["AssertionError"]:
            return ErrorClassification(
                type="SCENARIO FAILURE",
                status="failed",
                severity="low",
                recommendation="Review scenario assertions and expected outputs",
                emoji="❌",
            )
        if exception_type in [
            "FileNotFoundError",
            "PermissionError",
            "OSError",
            "InventoryError",
        ]:
            return ErrorClassification(
                type="FRAMEWORK ERROR",
                status="framework_error",
                severity="medium",
                recommendation="Check test setup - file paths, permissions, or test environment",
                emoji="💥",
            )
        elif exception_type in ["ImportError", "ModuleNotFoundError"]:
            return ErrorClassification(
                type="FRAMEWORK ERROR",
                status="framework_error",
                severity="medium",
                recommendation="Check test imports and dependencies",
                emoji="💥",
            )
        elif exception_type in [
            "ValueError",
            "TypeError",
            "AttributeError",
            "KeyError",
        ]:
            return ErrorClassification(
                type="SYSTEM ERROR",
                status="system_bug",
                severity="high",
                recommendation="Review stack trace to identify system code that needs error handling",
                emoji="🚨",
            )
        else:
            return ErrorClassification(
                type="UNEXPECTED ERROR",
                status="error",
                severity="medium",
                recommendation=f"Investigate {exception_type} in system code",
                emoji="❓",
            )

    def _cleanup_old_runs(self, keep_count: int = 10):
        """Clean up old validation runs, keeping only the most recent ones based on timestamp in directory name."""
        try:
            # Get all run directories that match the timestamp pattern
            run_dirs = []
            for d in self.runs_dir.iterdir():
                if d.is_dir() and not d.name.startswith("."):
                    # Extract timestamp from directory name (format: YYYYMMDD_HHMMSS_scenarioname)
                    parts = d.name.split("_")
                    if len(parts) >= 2:
                        try:
                            # Try to parse the timestamp from the first two parts
                            timestamp_str = f"{parts[0]}_{parts[1]}"
                            timestamp = datetime.strptime(
                                timestamp_str, "%Y%m%d_%H%M%S"
                            )
                            run_dirs.append((d, timestamp))
                        except ValueError:
                            # Skip directories that don't match the expected format
                            continue

            if len(run_dirs) <= keep_count:
                return  # Nothing to cleanup

            # Sort by timestamp (newest first)
            run_dirs.sort(key=lambda x: x[1], reverse=True)

            # Remove old runs beyond keep_count
            dirs_to_remove = run_dirs[keep_count:]
            removed_count = 0

            for old_dir, _ in dirs_to_remove:
                try:
                    shutil.rmtree(old_dir)
                    removed_count += 1
                    logger.debug(f"Removed old validation run: {old_dir.name}")
                except Exception as e:
                    logger.warning(f"Failed to remove old run {old_dir.name}: {e}")

            if removed_count > 0:
                logger.info(
                    f"Cleaned up {removed_count} old validation runs (keeping latest {keep_count}, total will be {keep_count + 1})"
                )

        except Exception as e:
            logger.warning(f"Failed to cleanup old validation runs: {e}")

    def run_scenarios(
        self,
        scenario_names: list[str] | None = None,
        *,
        requested_scenarios: list[str] | None = None,
    ) -> ValidationRun:
        """Execute the selection and preserve terminal accounting on interruption."""
        run_start = datetime.now()
        run_id = run_start.strftime("%Y%m%d_%H%M%S_%f")

        if scenario_names is None:
            scenario_names = self.discover_scenarios()
        if not scenario_names:
            raise InventoryError("No scenarios selected to execute")

        scenario_results = []
        interrupted = False
        active_index = None
        try:
            # Retention only runs after a non-empty selection is established.
            self._cleanup_old_runs(keep_count=9)
            for index, scenario_name in enumerate(scenario_names):
                active_index = index
                logger.info(f"Executing scenario: {scenario_name}")
                result = self.execute_scenario(scenario_name)
                scenario_results.append(result)
                active_index = None
                if result.interrupted:
                    interrupted = True
                    break
        except (KeyboardInterrupt, SystemExit, asyncio.CancelledError) as error:
            interrupted = True
            if active_index is not None and len(scenario_results) == active_index:
                scenario_results.append(
                    ScenarioResult(
                        scenario_names[len(scenario_results)],
                        "framework_error",
                        0,
                        error_message=f"Validation interrupted: {type(error).__name__}",
                        execution_phase="execution",
                        interrupted=True,
                    )
                )

        if interrupted:
            for scenario_name in scenario_names[len(scenario_results) :]:
                scenario_results.append(
                    ScenarioResult(
                        scenario_name,
                        "framework_error",
                        0,
                        error_message="Not started because validation was interrupted",
                        execution_phase="not_started",
                        interrupted=True,
                    )
                )

        # Calculate statistics
        run_end = datetime.now()
        passed = sum(1 for r in scenario_results if r.status == "passed")
        failed = sum(1 for r in scenario_results if r.status == "failed")
        errors = sum(
            1
            for r in scenario_results
            if r.status in ["error", "system_bug", "framework_error"]
        )
        success_rate = (passed / len(scenario_results)) * 100 if scenario_results else 0

        validation_run = ValidationRun(
            run_id=run_id,
            start_time=run_start,
            end_time=run_end,
            total_scenarios=len(scenario_results),
            passed_scenarios=passed,
            failed_scenarios=failed,
            error_scenarios=errors,
            success_rate=success_rate,
            scenario_results=scenario_results,
            requested_scenarios=list(
                scenario_names if requested_scenarios is None else requested_scenarios
            ),
            interrupted=interrupted,
            expanded_scenarios=list(scenario_names),
        )

        return validation_run
