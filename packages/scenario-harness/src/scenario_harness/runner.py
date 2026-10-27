"""Execute real application scenarios with evidence and failure classification."""

import asyncio
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
from typing import Any

from .discovery import InventoryError, describe_scenario
from .errors import classify_error, format_scenario_error
from .execution import ScenarioProgram
from .lifecycle import ExecutionOutcome, RunContext, execute
from .process import supervise
from .project import ProjectSpec, resolve_reference
from .results import ScenarioResult, ValidationRun

logger = logging.getLogger("validation-runner")


class GenericRunner:
    """Validation execution engine with scenario-based testing."""

    def __init__(
        self,
        project: ProjectSpec,
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
        self.project = project
        self.validation_root = project.suite_root
        self.timeout_seconds = timeout_seconds
        self.cleanup_timeout_seconds = cleanup_timeout_seconds
        self.environment_names = environment_names
        self.secret_environment_names = secret_environment_names
        self.scenarios_dir = self.validation_root / "scenarios"
        self.runs_dir = self.validation_root / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def discover_scenarios(self) -> list[str]:
        """Collect all declared scenarios without importing application modules."""
        return [item.selector for item in self.project.collect()]

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
                self.scenarios_dir / f"{scenario_name}.py",
                self.scenarios_dir,
                base_class=self.project.base_class,
                entrypoint=self.project.entrypoint,
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
                if (
                    name in self.project.reserved_environment
                    or name in self.project.environment_paths
                    or name
                    in {
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
                    }
                ):
                    raise InventoryError(
                        f"Worker execution path is framework-owned: {name}"
                    )
                if name not in os.environ:
                    raise InventoryError(
                        f"Requested worker environment variable is unset: {name}"
                    )
                environment[name] = os.environ[name]
            for name, path_kind in self.project.environment_paths.items():
                environment[name] = str(getattr(self.project, path_kind).resolve())
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
                "project": self.project.to_dict(),
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
                    str(Path(__file__).with_name("worker.py")),
                    str(request_path),
                ],
                cwd=self.project.root,
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
        program: ScenarioProgram | None = None
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
            describe_scenario(
                scenario_file,
                self.scenarios_dir,
                base_class=self.project.base_class,
                entrypoint=self.project.entrypoint,
            )
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                raise InventoryError(
                    "The synchronous runner cannot execute inside an active event loop; use the async lifecycle API"
                )
            context = RunContext(directory, secrets=secret_inputs)
            adapter = resolve_reference(self.project.adapter, self.project.root)()

            async def body() -> None:
                nonlocal program
                loop = asyncio.get_running_loop()
                context.defer_cleanup("runner.executor", loop.shutdown_default_executor)
                loader = resolve_reference(
                    self.project.scenario_loader, self.project.root
                )
                program = loader(context, self.project, scenario_name, scenario_file)
                value = program.run()
                if inspect.isawaitable(value):
                    await value

            def finalize(outcome: ExecutionOutcome) -> Any:
                if program is not None:
                    return program.finalize(outcome)
                return None

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
                classification = classify_error(primary)
                if (
                    outcome.primary_phase == "setup"
                    or outcome.primary_error is None
                    or outcome.cancelled
                    or outcome.timed_out
                ):
                    classification = classify_error(InventoryError(str(primary)))
                error_msg = context.redact(
                    format_scenario_error(primary, scenario_file)
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
            classification = classify_error(error)
            message = format_scenario_error(
                error, self.scenarios_dir / f"{scenario_name}.py"
            )
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

    def _cleanup_old_runs(self, keep_count: int = 10) -> None:
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
