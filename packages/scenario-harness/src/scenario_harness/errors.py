"""Generic failure formatting and classification."""

from __future__ import annotations

import traceback
from pathlib import Path

from .results import ErrorClassification


def format_scenario_error(exception: BaseException, source: Path) -> str:
    """Build a concise, actionable error message for scenario failures."""
    exc_type = type(exception).__name__
    exc_text = str(exception).strip()
    if not exc_text and exc_type == "AssertionError":
        exc_text = "Assertion failed"
    elif not exc_text:
        exc_text = "No additional error message"

    extracted_tb = traceback.extract_tb(exception.__traceback__)
    target_frame = None

    scenario_rel = str(source).replace("\\", "/")
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


def classify_error(exception: BaseException) -> ErrorClassification:
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
