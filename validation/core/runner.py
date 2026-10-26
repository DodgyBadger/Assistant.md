"""Execute real application scenarios with evidence and failure classification."""

import asyncio
import importlib
import inspect
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.logger import UnifiedLogger

from .inventory import InventoryError, ScenarioCatalog, describe_scenario
from .paths import resolve_validation_root
from .results import ErrorClassification, ScenarioResult, ValidationRun

logger = UnifiedLogger(tag="validation-runner", default_sinks=["validation", "logfire"])


class ValidationRunner:
    """Validation execution engine with scenario-based testing."""

    def __init__(self, validation_root: str | Path | None = None):
        self.validation_root = (
            Path(validation_root) if validation_root else resolve_validation_root()
        )
        self.scenarios_dir = self.validation_root / "scenarios"
        self.runs_dir = self.validation_root / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def discover_scenarios(self) -> list[str]:
        """Collect all declared scenarios without importing application modules."""
        return ScenarioCatalog(self.validation_root).discover_scenarios()

    def execute_scenario(self, scenario_name: str) -> ScenarioResult:
        """
        Execute a single scenario directly.

        Args:
            scenario_name: Scenario name, may include folder path (e.g., "integration/basic_haiku")
        """
        start_time = datetime.now()

        # Normalize path separators for cross-platform support
        scenario_name = scenario_name.replace("\\", "/")

        try:
            # Build file path from scenario name
            scenario_file = self.scenarios_dir / f"{scenario_name}.py"
            if not scenario_file.exists():
                raise FileNotFoundError(f"Scenario file not found: {scenario_file}")

            # Convert scenario name to module name (e.g., "integration/basic_haiku" -> "validation.scenarios.integration.basic_haiku")
            module_name = f"validation.scenarios.{scenario_name.replace('/', '.')}"
            definition = describe_scenario(scenario_file, self.scenarios_dir)
            module = importlib.import_module(module_name)

            from validation.core.base_scenario import BaseScenario

            scenario_class = getattr(module, definition.class_name, None)
            if not inspect.isclass(scenario_class) or not issubclass(
                scenario_class, BaseScenario
            ):
                raise InventoryError(f"Invalid runtime scenario class: {scenario_name}")

            # Execute the scenario (async support)
            scenario_instance = scenario_class()

            if asyncio.iscoroutinefunction(scenario_instance.test_scenario):
                asyncio.run(scenario_instance.test_scenario())
            else:
                scenario_instance.test_scenario()
            if hasattr(scenario_instance, "assert_no_failures"):
                scenario_instance.assert_no_failures()
            if hasattr(scenario_instance, "mark_scenario_outcome"):
                scenario_instance.mark_scenario_outcome("passed")
            if hasattr(scenario_instance, "teardown_scenario"):
                scenario_instance.teardown_scenario()

            execution_time = (datetime.now() - start_time).total_seconds()

            scenario_result = ScenarioResult(
                scenario_name=scenario_name,
                status="passed",
                execution_time=execution_time,
                evidence_path=str(scenario_instance.run_path),
            )

            return scenario_result

        except Exception as e:
            execution_time = (datetime.now() - start_time).total_seconds()
            error_msg = self._format_scenario_error(e, scenario_name)
            stack_trace = traceback.format_exc()

            # Reuse V1 error classification
            error_classification = self._classify_error(e, scenario_name)
            evidence_path = str(self.runs_dir)
            if "scenario_instance" in locals() and hasattr(
                scenario_instance, "run_path"
            ):
                evidence_path = str(scenario_instance.run_path)
                if hasattr(scenario_instance, "mark_scenario_outcome"):
                    scenario_instance.mark_scenario_outcome("failed", error_msg)
                if hasattr(scenario_instance, "teardown_scenario"):
                    try:
                        scenario_instance.teardown_scenario()
                    except Exception:
                        # Preserve original scenario failure as primary signal.
                        pass

            scenario_result = ScenarioResult(
                scenario_name=scenario_name,
                status=error_classification.status,
                execution_time=execution_time,
                evidence_path=evidence_path,
                error_message=error_msg,
                error_classification=error_classification,
                stack_trace=stack_trace,
            )
            return scenario_result

    def _format_scenario_error(self, exception: Exception, scenario_name: str) -> str:
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
        self, exception: Exception, scenario_name: str
    ) -> ErrorClassification:
        """Classify errors using V1 logic."""
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
        """Run V2 validation scenarios."""
        run_start = datetime.now()
        run_id = run_start.strftime("%Y%m%d_%H%M%S_%f")

        # Clean up old runs before starting new ones (keep 9 + 1 new = 10 total)
        self._cleanup_old_runs(keep_count=9)

        # Discover scenarios if not specified
        if scenario_names is None:
            scenario_names = self.discover_scenarios()

        if not scenario_names:
            logger.warning("No scenarios found to execute")

        # Execute scenarios sequentially
        scenario_results = []
        for scenario_name in scenario_names:
            logger.info(f"Executing scenario: {scenario_name}")
            result = self.execute_scenario(scenario_name)
            scenario_results.append(result)

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
            requested_scenarios=list(requested_scenarios or scenario_names),
            expanded_scenarios=list(scenario_names),
        )

        return validation_run
