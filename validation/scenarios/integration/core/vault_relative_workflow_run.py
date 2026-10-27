"""Validate explicit vault-relative workflow execution."""

from __future__ import annotations

import sys
from pathlib import Path

from pydantic_ai.messages import ToolReturn

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
from core.runtime.state import get_runtime_context
from core.tools.workflow_run import WorkflowRun
from validation.support.base_scenario import BaseScenario


class VaultRelativeWorkflowRunScenario(BaseScenario):
    """Run project-local workflows without adding them to managed discovery."""

    async def test_scenario(self):
        vault = self.create_vault("VaultRelativeWorkflowVault")
        workflow_path = "Research/Forest/automation/process-library.md"
        self.create_file(vault, workflow_path, PROJECT_WORKFLOW)
        self.create_file(
            vault,
            "Research/Forest/automation/context.md",
            CONTEXT_TEMPLATE,
        )
        self.create_file(
            vault,
            "Research/Forest/notes/code-example.md",
            ORDINARY_MARKDOWN,
        )
        failing_workflow_path = "Research/Forest/automation/failing-workflow.md"
        self.create_file(vault, failing_workflow_path, FAILING_WORKFLOW)
        outside_workflow = self.run_path / "outside-workflow.md"
        outside_workflow.write_text(PROJECT_WORKFLOW, encoding="utf-8")
        (vault / "Research/escape.md").symlink_to(outside_workflow)

        await self.start_system()
        tool = WorkflowRun.get_tool(str(vault))

        listing = await tool.function(operation="list")
        self.soft_assert(
            workflow_path not in listing,
            "Vault-relative workflows should not enter managed discovery",
        )

        with use_execution_authority(LOCAL_USER_AUTHORITY):
            run_out = await tool.function(
                operation="run",
                workflow_path=workflow_path,
            )
        self.soft_assert(
            "success: True" in run_out,
            "An explicit vault-relative workflow should run successfully",
        )
        self.soft_assert(
            f"workflow_path: {workflow_path}" in run_out,
            "The workflow result should identify its vault-relative source path",
        )
        self.soft_assert(
            (vault / "Research/Forest/library-processed.md").is_file(),
            "The vault-relative workflow should retain normal tool capabilities",
        )
        self.assert_event_contains(
            name="workflow_task_completed",
            expected={"workflow_path": workflow_path},
        )

        workflow_id = f"{vault.name}/@path:{workflow_path}"
        history = get_runtime_context().workflow_run_store.list_runs(workflow_id)
        self.soft_assert_equal(
            history[0].workflow_name if history else None,
            f"@path:{workflow_path}",
            "Durable workflow history should retain the explicit source path",
        )

        with use_execution_authority(LOCAL_USER_AUTHORITY):
            start_out = await tool.function(
                operation="start",
                workflow_path=workflow_path,
            )
        start_data = self._parse_kv_response(start_out)
        task_id = start_data.get("task_id", "")
        self.soft_assert(bool(task_id), "Path-based start should return a task id")
        task = await self._wait_for_execution_task(task_id)
        self.soft_assert_equal(
            task.get("status"),
            "completed",
            "A path-based background workflow should complete normally",
        )
        self.soft_assert_equal(
            task.get("metadata", {}).get("workflow_path"),
            workflow_path,
            "Execution task metadata should retain the workflow source path",
        )

        with use_execution_authority(LOCAL_USER_AUTHORITY):
            failing_start_out = await tool.function(
                operation="start",
                workflow_path=failing_workflow_path,
            )
        failing_task_id = self._parse_kv_response(failing_start_out).get("task_id", "")
        self.soft_assert(
            bool(failing_task_id),
            "A failing path-based background workflow should return a task id",
        )
        failing_task = await self._wait_for_execution_task(failing_task_id)
        self.soft_assert_equal(
            failing_task.get("metadata", {})
            .get("workflow_failure", {})
            .get("workflow_path"),
            failing_workflow_path,
            "Structured workflow failures should retain the workflow source path",
        )
        self.assert_event_contains(
            name="workflow_task_failed",
            expected={"workflow_path": failing_workflow_path},
        )

        invalid_requests = (
            (
                {"workflow_name": "managed", "workflow_path": workflow_path},
                "either workflow_name or workflow_path",
            ),
            ({"workflow_path": "../outside-workflow.md"}, "Path traversal"),
            (
                {"workflow_path": "Research/escape.md"},
                "Path escapes vault boundaries",
            ),
            (
                {"workflow_path": "Research/Forest/automation/context.md"},
                "run_type: workflow",
            ),
            (
                {"workflow_path": "Research/Forest/notes/code-example.md"},
                "run_type: workflow",
            ),
        )
        for request, expected_message in invalid_requests:
            with use_execution_authority(LOCAL_USER_AUTHORITY):
                result = await tool.function(operation="run", **request)
            self.soft_assert(
                isinstance(result, ToolReturn),
                f"Invalid path request should return a structured failure: {request}",
            )
            self.soft_assert(
                expected_message in result.return_value,
                f"Invalid path request should explain its boundary: {request}",
            )

        unsupported = await tool.function(operation="list", workflow_path=workflow_path)
        self.soft_assert(
            isinstance(unsupported, ToolReturn)
            and "only supported for run and start" in unsupported.return_value,
            "Non-execution operations should reject workflow_path explicitly",
        )

        scheduler_job_ids = {
            str(job.id) for job in get_runtime_context().scheduler.get_jobs()
        }
        self.soft_assert(
            all("@path:" not in job_id for job_id in scheduler_job_ids),
            "Explicit vault-relative workflows should not become scheduled jobs",
        )

        await self.stop_system()
        self.teardown_scenario()
        self.assert_no_failures()

    @staticmethod
    def _parse_kv_response(text: str) -> dict[str, str]:
        parsed: dict[str, str] = {}
        for raw_line in (text or "").splitlines():
            if ":" not in raw_line:
                continue
            key, value = raw_line.split(":", 1)
            parsed[key.strip()] = value.strip()
        return parsed


PROJECT_WORKFLOW = """---
run_type: workflow
enabled: false
description: Project-local research workflow
---
```python
await file_write(
    operation="write",
    path="Research/Forest/library-processed.md",
    content="processed\\n",
)
await finish(status="completed", reason="project-workflow-complete")
```
"""


CONTEXT_TEMPLATE = """---
run_type: context
description: Context template that must not run by path
---
```python
await assemble_context(instructions=["not executable as a workflow"])
```
"""


ORDINARY_MARKDOWN = """# Example

This note contains Python documentation but is not a workflow.

```python
print("example")
```
"""


FAILING_WORKFLOW = """---
run_type: workflow
description: Project-local workflow that fails deterministically
---
```python
raise RuntimeError("expected project workflow failure")
```
"""
