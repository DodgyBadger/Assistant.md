"""Monty authoring engine — validates and executes Python-block workflow templates."""

from __future__ import annotations

from typing import Any

from core.authoring.template_loader import parse_authoring_template_text
from core.identity import ExecutionAuthority
from core.runtime.execution_tasks import ExecutionTaskSource
from core.runtime.state import get_runtime_context


def validate_workflow_definition(
    *,
    workflow_id: str,
    file_path: str,
    sections: dict[str, Any],
    validated_config: dict[str, Any],
) -> None:
    """Validate workflow structure by parsing the Python block at load time."""
    del sections, validated_config, workflow_id
    with open(file_path, encoding="utf-8") as handle:
        content = handle.read()
    parse_authoring_template_text(content)


async def run_workflow(job_args: dict[str, Any], **kwargs: Any) -> object:
    """Execute a Monty-authored markdown workflow template."""
    global_id = job_args["global_id"]
    owner_principal_id = str(job_args.get("owner_principal_id") or "").strip()
    if not owner_principal_id:
        raise RuntimeError("Scheduled workflow is missing its owner principal.")
    step_name = kwargs.get("step_name")
    expect_failure = bool(kwargs.get("expected_failure", False))

    if "/" not in global_id:
        raise ValueError(
            f"Invalid global_id format. Expected 'vault/name', got: {global_id}"
        )

    runtime = get_runtime_context()
    return await runtime.workflow_governor.execute_workflow(
        global_id=global_id,
        source=ExecutionTaskSource.SCHEDULER,
        step_name=step_name,
        expect_failure=expect_failure,
        authority=ExecutionAuthority(owner_principal_id),
    )
