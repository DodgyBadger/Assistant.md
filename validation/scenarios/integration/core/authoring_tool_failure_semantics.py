"""Validate tool failure semantics across model and Monty workflow boundaries."""

from __future__ import annotations

import sys
from pathlib import Path

from pydantic_ai.messages import ToolReturn

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import core.tools.session_ops as session_ops_module
from core.identity import LOCAL_USER_AUTHORITY
from core.runtime.execution_tasks import ExecutionTaskSource
from core.runtime.state import get_runtime_context
from core.tools.file_read import FileRead
from validation.core.base_scenario import BaseScenario


class AuthoringToolFailureSemanticsScenario(BaseScenario):
    """Prove direct tools use Python exceptions without changing model-facing calls."""

    async def test_scenario(self):
        vault = self.create_vault("AuthoringToolFailureVault")
        self.create_file(
            vault,
            "AssistantMD/Authoring/caught_probe.md",
            CAUGHT_PROBE_WORKFLOW,
        )
        self.create_file(
            vault,
            "AssistantMD/Authoring/explicit_failure.md",
            EXPLICIT_FAILURE_WORKFLOW,
        )
        self.create_file(
            vault,
            "AssistantMD/Authoring/session_model_failure.md",
            SESSION_MODEL_FAILURE_WORKFLOW,
        )

        await self.start_system()
        runtime = get_runtime_context()

        model_tool_result = FileRead.get_tool(str(vault)).function(
            operation="unknown",
        )
        self.soft_assert(
            isinstance(model_tool_result, ToolReturn),
            "Model-facing tool failures should remain structured ToolReturn values",
        )
        self.soft_assert_equal(
            model_tool_result.metadata.get("status"),
            "error",
            "Model-facing tool failure should expose structured error status",
        )

        caught_result = await runtime.workflow_governor.execute_workflow(
            global_id=f"{vault.name}/caught_probe",
            source=ExecutionTaskSource.API,
            authority=LOCAL_USER_AUTHORITY,
        )
        explicit_result = await runtime.workflow_governor.execute_workflow(
            global_id=f"{vault.name}/explicit_failure",
            source=ExecutionTaskSource.API,
            authority=LOCAL_USER_AUTHORITY,
        )

        original_preflight = session_ops_module._preflight_session_summary_embeddings
        original_summarize = session_ops_module._summarize_session

        async def successful_preflight() -> None:
            return None

        async def configured_model_failure(**kwargs):
            del kwargs
            raise ValueError("Configured summarization model is unavailable")

        session_ops_module._preflight_session_summary_embeddings = successful_preflight
        session_ops_module._summarize_session = configured_model_failure
        session_failure = None
        try:
            await runtime.workflow_governor.execute_workflow(
                global_id=f"{vault.name}/session_model_failure",
                source=ExecutionTaskSource.API,
                authority=LOCAL_USER_AUTHORITY,
            )
        except Exception as exc:  # noqa: BLE001
            session_failure = exc
        finally:
            session_ops_module._preflight_session_summary_embeddings = (
                original_preflight
            )
            session_ops_module._summarize_session = original_summarize

        caught_run = runtime.workflow_run_store.get_latest_run(
            f"{vault.name}/caught_probe"
        )
        explicit_run = runtime.workflow_run_store.get_latest_run(
            f"{vault.name}/explicit_failure"
        )
        session_run = runtime.workflow_run_store.get_latest_run(
            f"{vault.name}/session_model_failure"
        )

        self.soft_assert_equal(
            caught_result.status,
            "skipped",
            "A script should catch a scoped direct-tool RuntimeError and choose a non-failure outcome",
        )
        self.soft_assert(
            "file_read operation 'unknown' failed" in str(caught_result.reason or ""),
            "Caught tool errors should retain stable tool and operation context",
        )
        self.soft_assert_equal(
            caught_run.status if caught_run else None,
            "skipped",
            "A caught expected tool failure should retain the script-selected durable outcome",
        )
        self.soft_assert_equal(
            explicit_result.status,
            "failed",
            "finish(status='failed') should produce a failed domain result",
        )
        self.soft_assert_equal(
            explicit_result.success,
            False,
            "A failed workflow result must not claim success",
        )
        self.soft_assert_equal(
            explicit_run.status if explicit_run else None,
            "failed",
            "Explicit domain failure should be durable",
        )
        self.soft_assert(
            session_failure is not None,
            "An uncaught mandatory session_ops failure should raise from Monty execution",
        )
        self.soft_assert_equal(
            session_run.status if session_run else None,
            "failed",
            "Uncaught session_ops model failure should be durable",
        )
        self.soft_assert(
            "Configured summarization model is unavailable"
            in str(session_run.reason if session_run else ""),
            "Durable workflow failure should retain the underlying model reason",
        )

        await self.stop_system()
        self.teardown_scenario()
        self.assert_no_failures()


CAUGHT_PROBE_WORKFLOW = """---
run_type: workflow
enabled: false
description: Catch one expected direct-tool failure
---

```python
try:
    await file_read(operation="unknown")
except RuntimeError as exc:
    await finish(status="skipped", reason=str(exc))

await finish(status="failed", reason="expected tool error was not raised")
```
"""


EXPLICIT_FAILURE_WORKFLOW = """---
run_type: workflow
enabled: false
description: Report one intentional domain failure
---

```python
await finish(status="failed", reason="intentional validation failure")
```
"""


SESSION_MODEL_FAILURE_WORKFLOW = """---
run_type: workflow
enabled: false
description: Propagate one mandatory session summarization failure
---

```python
await session_ops(operation="summarize_session", session_id="validation-session", summarization_model="unavailable-model")
```
"""
