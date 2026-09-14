"""
Integration scenario for the delegate tool.

Validates delegate as both an LLM-facing chat tool (via patched executor)
and as a Monty direct tool (via workflow runs). Asserts on stable validation
events at decision boundaries and on final output artifacts.
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai.exceptions import ModelHTTPError, UsageLimitExceeded
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturn,
    ToolReturnPart,
)

from validation.core.base_scenario import BaseScenario, with_local_user_authority


class DelegateToolScenario(BaseScenario):
    """Validate delegate tool behavior via stable validation events and output artifacts."""

    @with_local_user_authority
    async def test_scenario(self):
        vault = self.create_vault("DelegateToolVault")
        self.create_file(vault, "notes/content.md", "DELEGATE_CONTENT")
        self.copy_files("validation/templates/files/test_image.jpg", vault, "images")
        self.create_file(
            vault,
            "notes/with-image.md",
            "Review this embedded image.\n\n![Example](../images/test_image.jpg)\n",
        )

        await self.start_system()

        configured_repeated_failure_limit = 2
        configured_delegate_timeout = 90
        configured_stream_retries = 1
        configured_retry_base_delay = 0.0
        configured_retry_max_delay = 10.0
        repeated_failure_limit_update = self.call_api(
            "/api/system/settings/general/delegate_repeated_failure_limit",
            method="PUT",
            data={"value": str(configured_repeated_failure_limit)},
        )
        assert (
            repeated_failure_limit_update.status_code == 200
        ), "Delegate repeated-failure limit setting updates"
        await _assert_repeated_failure_guard()
        _assert_delegate_flight_card()
        _assert_delegate_usage_limits()
        _assert_shared_tool_result_classification()
        await _assert_retry_prompt_progress()
        delegate_timeout_update = self.call_api(
            "/api/system/settings/general/delegate_timeout_seconds",
            method="PUT",
            data={"value": str(configured_delegate_timeout)},
        )
        assert (
            delegate_timeout_update.status_code == 200
        ), "Delegate timeout setting updates"
        for setting_name, value in (
            ("model_stream_retries", configured_stream_retries),
            ("model_stream_retry_base_delay_seconds", configured_retry_base_delay),
            ("model_stream_retry_max_delay_seconds", configured_retry_max_delay),
        ):
            update = self.call_api(
                f"/api/system/settings/general/{setting_name}",
                method="PUT",
                data={"value": str(value)},
            )
            assert update.status_code == 200, f"{setting_name} setting updates"

        invalid_retries = self.call_api(
            "/api/system/settings/general/model_stream_retries",
            method="PUT",
            data={"value": "6"},
        )
        assert invalid_retries.status_code == 400, "Stream retries reject unsafe bounds"
        invalid_delay_bounds = self.call_api(
            "/api/system/settings/general/model_stream_retry_max_delay_seconds",
            method="PUT",
            data={"value": "-1"},
        )
        assert (
            invalid_delay_bounds.status_code == 400
        ), "Stream retry delays reject negative values"

        from pydantic_ai.models.test import TestModel

        import core.chat.executor as chat_executor

        current_case = {"name": "basic"}

        class _DelegateForcingModel(TestModel):
            def __init__(self):
                super().__init__(call_tools=["delegate"])

            def gen_tool_args(self, tool_def):
                if getattr(tool_def, "name", "") != "delegate":
                    return super().gen_tool_args(tool_def)
                case = current_case["name"]
                if case == "basic":
                    return {"prompt": "Reply with DELEGATE_OK.", "model": "test"}
                if case == "inherit_parent":
                    return {"prompt": "Inherit the parent model."}
                if case == "forbidden_stripping":
                    return {
                        "prompt": "Use your tools.",
                        "model": "test",
                        "tools": ["FILE_READ", "Delegate", "Code_Execution", "JOB"],
                    }
                if case == "child_tools":
                    return {
                        "prompt": "List available notes.",
                        "model": "test",
                        "tools": ["file_read"],
                    }
                if case == "model_request_limit_failure":
                    return {
                        "prompt": "Exceed child model request usage limits.",
                        "model": "test",
                    }
                raise AssertionError(f"Unexpected delegate case: {case}")

        def _patched_prepare_agent_config(
            vault_name, vault_path, tools, model, thinking=None, chat_mode=None
        ):
            del vault_name, vault_path, tools, model, thinking
            from core.authoring.shared.tool_binding import resolve_tool_binding

            binding = resolve_tool_binding(["delegate"], vault_path=str(vault))
            return (
                "You must call delegate before responding.",
                binding.tool_instructions,
                _DelegateForcingModel(),
                binding.tool_functions,
            )

        original_prepare = chat_executor._prepare_agent_config
        chat_executor._prepare_agent_config = _patched_prepare_agent_config
        import core.tools.delegate as delegate_module

        self.soft_assert_equal(
            delegate_module._resolve_delegate_model_value(
                "explicit-model",
                SimpleNamespace(model_alias="parent-model"),
            ),
            ("explicit-model", "explicit"),
            "An explicit delegate model should override its parent model",
        )
        self.soft_assert_equal(
            delegate_module._resolve_delegate_model_value(
                None,
                SimpleNamespace(model_alias=None),
            ),
            (None, "runtime_default"),
            "Non-chat delegates should retain the runtime-default fallback",
        )

        original_create_agent = delegate_module.create_agent

        class _FailingChildAgent:
            def __init__(self, error: Exception):
                self.error = error

            def instructions(self, *_args, **_kwargs):
                return None

            async def run(self, *_args, **_kwargs):
                raise AssertionError(
                    "delegate child agents must use streaming model calls"
                )

            def run_stream(self, *_args, **_kwargs):
                error = self.error

                class _FailingStream:
                    async def __aenter__(self):
                        raise error

                    async def __aexit__(self, exc_type, exc, tb):
                        return False

                return _FailingStream()

        class _StreamingChildRun:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def stream_output(self, *, debounce_by=None):
                yield "streamed delegate output"

            async def get_output(self):
                return "streamed delegate output"

            def all_messages(self):
                return []

        class _StreamingChildAgent:
            def instructions(self, *_args, **_kwargs):
                return None

            async def run(self, *_args, **_kwargs):
                raise AssertionError(
                    "delegate child agents must use streaming model calls"
                )

            def run_stream(self, *_args, **_kwargs):
                return _StreamingChildRun()

        async def _streaming_create_agent(*_args, **_kwargs):
            return _StreamingChildAgent()

        async def _assert_delegate_uses_streaming() -> None:
            from core.authoring.helpers.runtime_common import (
                invoke_bound_tool,
                normalize_tool_result,
            )
            from core.authoring.shared.tool_binding import resolve_tool_binding

            binding = resolve_tool_binding(["delegate"], vault_path=str(vault))
            delegate_module.create_agent = _streaming_create_agent
            try:
                result = await invoke_bound_tool(
                    binding.tool_functions[0],
                    tool_name="delegate",
                    arguments={"prompt": "Stream child delegate.", "model": "test"},
                    run_buffers={},
                    session_buffers={},
                    session_id="delegate_streaming_transport",
                    vault_name=vault.name,
                )
            finally:
                delegate_module.create_agent = original_create_agent
            tool_result = normalize_tool_result(
                "delegate",
                result,
                vault_path=str(vault),
            )
            self.soft_assert_equal(
                tool_result.return_value,
                "streamed delegate output",
                "Delegate should collect streamed child-agent output",
            )

        await _assert_delegate_uses_streaming()

        async def _assert_more_than_32_child_tool_calls() -> None:
            from core.authoring.helpers.runtime_common import (
                invoke_bound_tool,
                normalize_tool_result,
            )
            from core.authoring.shared.tool_binding import resolve_tool_binding

            messages = []
            for index in range(40):
                call_id = f"many-call-{index}"
                messages.extend(
                    [
                        ModelResponse(
                            parts=[
                                ToolCallPart(
                                    tool_name="file_read",
                                    args={"path": f"notes/{index}.md"},
                                    tool_call_id=call_id,
                                )
                            ]
                        ),
                        ModelRequest(
                            parts=[
                                ToolReturnPart(
                                    tool_name="file_read",
                                    content="ok",
                                    tool_call_id=call_id,
                                    metadata={"status": "completed"},
                                )
                            ]
                        ),
                    ]
                )
            observed_limits = []

            class _ManyToolRun(_StreamingChildRun):
                async def stream_output(self, *, debounce_by=None):
                    yield "FORTY_TOOL_CALLS_COMPLETE"

                async def get_output(self):
                    return "FORTY_TOOL_CALLS_COMPLETE"

                def all_messages(self):
                    return messages

            class _ManyToolAgent(_StreamingChildAgent):
                def run_stream(self, *_args, **kwargs):
                    observed_limits.append(kwargs.get("usage_limits"))
                    return _ManyToolRun()

            async def _many_tool_create_agent(*_args, **_kwargs):
                return _ManyToolAgent()

            binding = resolve_tool_binding(["delegate"], vault_path=str(vault))
            delegate_module.create_agent = _many_tool_create_agent
            try:
                raw_result = await invoke_bound_tool(
                    binding.tool_functions[0],
                    tool_name="delegate",
                    arguments={
                        "prompt": "Complete more than 32 useful child tool calls.",
                        "model": "test",
                        "tools": ["file_read"],
                    },
                    run_buffers={},
                    session_buffers={},
                    session_id="delegate_many_tool_calls",
                    vault_name=vault.name,
                )
            finally:
                delegate_module.create_agent = original_create_agent
            result = normalize_tool_result(
                "delegate",
                raw_result,
                vault_path=str(vault),
            )
            self.soft_assert_equal(
                result.return_value,
                "FORTY_TOOL_CALLS_COMPLETE",
                "Delegate should complete after more than 32 child tool calls",
            )
            self.soft_assert_equal(
                observed_limits[0].tool_calls_limit if observed_limits else "missing",
                None,
                "Delegate Pydantic usage limits should not impose a tool-call ceiling",
            )
            self.soft_assert_equal(
                result.metadata.get("audit", {}).get("tool_call_count"),
                40,
                "Delegate audit should continue counting calls without enforcing them",
            )

        await _assert_more_than_32_child_tool_calls()

        async def _assert_managed_delegate_job() -> None:
            from core.authoring.helpers.runtime_common import invoke_bound_tool
            from core.authoring.shared.tool_binding import resolve_tool_binding
            from core.identity import LOCAL_USER_AUTHORITY
            from core.runtime.execution_tasks import (
                ExecutionTaskKind,
                ExecutionTaskSource,
            )
            from core.runtime.state import get_runtime_context
            from core.runtime.task_runner import ExecutionTaskSpec

            runtime = get_runtime_context()
            binding = resolve_tool_binding(["delegate"], vault_path=str(vault))
            delegate_module.create_agent = _streaming_create_agent

            async def _launch(parent_task):
                result = await invoke_bound_tool(
                    binding.tool_functions[0],
                    tool_name="delegate",
                    arguments={
                        "prompt": "Run a managed delegate.",
                        "model": "test",
                        "mode": "managed",
                    },
                    run_buffers={},
                    session_buffers={},
                    session_id="delegate_managed_job",
                    vault_name=vault.name,
                )
                metadata = result.metadata if isinstance(result.metadata, dict) else {}
                await runtime.task_coordinator.wait_for_tasks(
                    [str(metadata.get("job_id") or "")],
                    timeout_seconds=2.0,
                    terminal_or_attention_only=True,
                )
                return parent_task.task_id, result

            parent_task_id, result = await runtime.task_runner.run_inline(
                ExecutionTaskSpec(
                    kind=ExecutionTaskKind.CHAT,
                    scope="delegate:managed-parent",
                    source=ExecutionTaskSource.SYSTEM,
                    label="delegate-managed-parent",
                    authority=LOCAL_USER_AUTHORITY,
                ),
                _launch,
            )

            self.soft_assert_equal(
                isinstance(result, ToolReturn),
                True,
                "Managed delegate should return a structured job handle",
            )
            metadata = result.metadata if isinstance(result.metadata, dict) else {}
            job_id = str(metadata.get("job_id") or "")
            self.soft_assert_equal(
                bool(job_id),
                True,
                "Managed delegate handle should include a public job ID",
            )
            terminal = await runtime.task_coordinator.wait_for_tasks(
                [job_id],
                timeout_seconds=2.0,
                terminal_or_attention_only=True,
            )
            delegate_module.create_agent = original_create_agent
            child = terminal.snapshots[0] if terminal.snapshots else None
            self.soft_assert_equal(
                child.kind if child else None,
                ExecutionTaskKind.DELEGATE.value,
                "Managed delegate should run as a delegate execution task",
            )
            self.soft_assert_equal(
                child.parent_task_id if child else None,
                parent_task_id,
                "Managed delegate should retain execution-task parent ownership",
            )
            self.soft_assert_equal(
                (
                    child.result.get("return_value")
                    if child is not None and child.result
                    else None
                ),
                "streamed delegate output",
                "Managed delegate terminal task should expose its bounded result",
            )

        await _assert_managed_delegate_job()

        async def _invoke_direct_delegate(session_id: str):
            from core.authoring.helpers.runtime_common import invoke_bound_tool
            from core.authoring.shared.tool_binding import resolve_tool_binding

            binding = resolve_tool_binding(["delegate"], vault_path=str(vault))
            return await invoke_bound_tool(
                binding.tool_functions[0],
                tool_name="delegate",
                arguments={"prompt": "Exercise delegate lifecycle.", "model": "test"},
                run_buffers={},
                session_buffers={},
                session_id=session_id,
                vault_name=vault.name,
            )

        async def _assert_initialization_failure_is_structured() -> None:
            from core.authoring.helpers.runtime_common import normalize_tool_result

            async def fail_create_agent(*_args, **_kwargs):
                raise RuntimeError("synthetic initialization failure")

            checkpoint = self.event_checkpoint()
            delegate_module.create_agent = fail_create_agent
            try:
                raw_result = await _invoke_direct_delegate("delegate_init_failure")
            finally:
                delegate_module.create_agent = original_create_agent
            result = normalize_tool_result(
                "delegate",
                raw_result,
                vault_path=str(vault),
            )
            self.soft_assert_equal(
                result.metadata.get("failure_kind"),
                "delegate_internal",
                "Delegate initialization failures should return structured metadata",
            )
            self.assert_event_contains(
                self.events_since(checkpoint),
                name="delegate_failed",
                expected={
                    "workflow_id": "delegate_init_failure",
                    "failure_kind": "delegate_internal",
                    "mode": "blocking",
                },
            )

        await _assert_initialization_failure_is_structured()

        class _WaitingChildRun:
            def __init__(self, started: asyncio.Event, cancelled: asyncio.Event):
                self.started = started
                self.cancelled = cancelled

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def stream_output(self, *, debounce_by=None):
                self.started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.cancelled.set()
                    raise
                yield "unreachable"

            async def get_output(self):
                return "unreachable"

            def all_messages(self):
                return []

        class _WaitingChildAgent:
            def __init__(self, started: asyncio.Event, cancelled: asyncio.Event):
                self.started = started
                self.cancelled = cancelled

            def instructions(self, *_args, **_kwargs):
                return None

            def run_stream(self, *_args, **_kwargs):
                return _WaitingChildRun(self.started, self.cancelled)

        async def _assert_timeout_cleans_up_child() -> None:
            from core.authoring.helpers.runtime_common import normalize_tool_result

            started = asyncio.Event()
            cancelled = asyncio.Event()

            async def waiting_create_agent(*_args, **_kwargs):
                return _WaitingChildAgent(started, cancelled)

            timeout_update = self.call_api(
                "/api/system/settings/general/delegate_timeout_seconds",
                method="PUT",
                data={"value": "1"},
            )
            assert timeout_update.status_code == 200
            delegate_module.create_agent = waiting_create_agent
            try:
                raw_result = await _invoke_direct_delegate("delegate_timeout_cleanup")
            finally:
                delegate_module.create_agent = original_create_agent
                self.call_api(
                    "/api/system/settings/general/delegate_timeout_seconds",
                    method="PUT",
                    data={"value": str(configured_delegate_timeout)},
                )
            result = normalize_tool_result(
                "delegate",
                raw_result,
                vault_path=str(vault),
            )
            self.soft_assert_equal(
                result.metadata.get("failure_kind"),
                "delegate_timeout",
                "Delegate timeout should return structured failure metadata",
            )
            self.soft_assert(started.is_set(), "Timed-out child should have started")
            self.soft_assert(
                cancelled.is_set(),
                "Delegate timeout should cancel and await the active child stream",
            )

        await _assert_timeout_cleans_up_child()

        async def _assert_parent_cancellation_is_logged() -> None:
            started = asyncio.Event()
            cancelled = asyncio.Event()

            async def waiting_create_agent(*_args, **_kwargs):
                return _WaitingChildAgent(started, cancelled)

            timeout_update = self.call_api(
                "/api/system/settings/general/delegate_timeout_seconds",
                method="PUT",
                data={"value": "0"},
            )
            assert timeout_update.status_code == 200
            checkpoint = self.event_checkpoint()
            delegate_module.create_agent = waiting_create_agent
            task = asyncio.create_task(
                _invoke_direct_delegate("delegate_parent_cancelled")
            )
            try:
                await asyncio.wait_for(started.wait(), timeout=1)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                else:
                    raise AssertionError("Parent cancellation must propagate")
            finally:
                delegate_module.create_agent = original_create_agent
                self.call_api(
                    "/api/system/settings/general/delegate_timeout_seconds",
                    method="PUT",
                    data={"value": str(configured_delegate_timeout)},
                )
            self.soft_assert(
                cancelled.is_set(),
                "Parent cancellation should reach the active child stream",
            )
            self.assert_event_contains(
                self.events_since(checkpoint),
                name="delegate_cancelled",
                expected={
                    "workflow_id": "delegate_parent_cancelled",
                    "mode": "blocking",
                },
            )

        await _assert_parent_cancellation_is_logged()

        async def _assert_queued_managed_cancellation_is_recorded() -> None:
            from core.authoring.helpers.runtime_common import invoke_bound_tool
            from core.authoring.shared.tool_binding import resolve_tool_binding
            from core.identity import LOCAL_USER_AUTHORITY
            from core.runtime.execution_tasks import (
                ExecutionTaskKind,
                ExecutionTaskSource,
            )
            from core.runtime.state import get_runtime_context
            from core.runtime.task_runner import ExecutionTaskSpec

            started = asyncio.Event()
            cancelled = asyncio.Event()

            async def waiting_create_agent(*_args, **_kwargs):
                return _WaitingChildAgent(started, cancelled)

            concurrency_update = self.call_api(
                "/api/system/settings/general/max_concurrent_delegates",
                method="PUT",
                data={"value": "1"},
            )
            assert concurrency_update.status_code == 200
            checkpoint = self.event_checkpoint()
            delegate_module.create_agent = waiting_create_agent
            runtime = get_runtime_context()
            binding = resolve_tool_binding(["delegate"], vault_path=str(vault))

            async def _launch_and_cancel(_parent_task):
                async def _launch(session_id: str):
                    return await invoke_bound_tool(
                        binding.tool_functions[0],
                        tool_name="delegate",
                        arguments={
                            "prompt": "Wait until cancelled.",
                            "model": "test",
                            "mode": "managed",
                        },
                        run_buffers={},
                        session_buffers={},
                        session_id=session_id,
                        vault_name=vault.name,
                    )

                first = await _launch("delegate_managed_holder")
                await asyncio.wait_for(started.wait(), timeout=1.0)
                second = await _launch("delegate_managed_queued_cancel")
                first_id = str(first.metadata.get("job_id"))
                second_id = str(second.metadata.get("job_id"))
                for _ in range(100):
                    queued = await runtime.task_coordinator.get_task(second_id)
                    if queued and queued.metadata.get("queue_reason"):
                        break
                    await asyncio.sleep(0.01)
                else:
                    raise AssertionError("Managed delegate did not enter the queue")
                await runtime.task_coordinator.cancel_task(
                    second_id,
                    reason="validation_queued_delegate_cancel",
                )
                second_terminal = await runtime.task_coordinator.wait_for_tasks(
                    [second_id],
                    timeout_seconds=1.0,
                    terminal_or_attention_only=True,
                    wake_on_attention=False,
                )
                await runtime.task_coordinator.cancel_task(
                    first_id,
                    reason="validation_holder_cleanup",
                )
                await runtime.task_coordinator.wait_for_tasks(
                    [first_id],
                    timeout_seconds=1.0,
                    terminal_or_attention_only=True,
                    wake_on_attention=False,
                )
                return second_id, second_terminal.snapshots[0]

            try:
                second_id, second_terminal = await runtime.task_runner.run_inline(
                    ExecutionTaskSpec(
                        kind=ExecutionTaskKind.CHAT,
                        scope="delegate:queued-cancel-parent",
                        source=ExecutionTaskSource.SYSTEM,
                        label="delegate-queued-cancel-parent",
                        authority=LOCAL_USER_AUTHORITY,
                    ),
                    _launch_and_cancel,
                )
            finally:
                delegate_module.create_agent = original_create_agent
                self.call_api(
                    "/api/system/settings/general/max_concurrent_delegates",
                    method="PUT",
                    data={"value": "3"},
                )
            self.soft_assert_equal(
                second_terminal.result.get("metadata", {}).get("status"),
                "cancelled",
                "Queued managed cancellation should retain a normalized result",
            )
            self.assert_event_contains(
                self.events_since(checkpoint),
                name="delegate_cancelled",
                expected={
                    "task_id": second_id,
                    "workflow_id": "delegate_managed_queued_cancel",
                    "mode": "managed",
                },
            )

        await _assert_queued_managed_cancellation_is_recorded()

        async def _assert_pre_attachment_cancellation_is_recorded() -> None:
            from core.authoring.helpers.runtime_common import invoke_bound_tool
            from core.authoring.shared.tool_binding import resolve_tool_binding
            from core.identity import LOCAL_USER_AUTHORITY
            from core.runtime.execution_tasks import (
                ExecutionTaskKind,
                ExecutionTaskSource,
            )
            from core.runtime.state import get_runtime_context
            from core.runtime.task_runner import ExecutionTaskSpec

            runtime = get_runtime_context()
            binding = resolve_tool_binding(["delegate"], vault_path=str(vault))
            captured_spawns = []
            original_spawn = runtime.background_spawner.spawn

            async def _launch_and_cancel(_parent_task):
                runtime.background_spawner.spawn = captured_spawns.append
                try:
                    launched = await invoke_bound_tool(
                        binding.tool_functions[0],
                        tool_name="delegate",
                        arguments={
                            "prompt": "Cancel before attachment.",
                            "model": "test",
                            "mode": "managed",
                        },
                        run_buffers={},
                        session_buffers={},
                        session_id="delegate_pre_attachment_cancel",
                        vault_name=vault.name,
                    )
                finally:
                    runtime.background_spawner.spawn = original_spawn
                job_id = str(launched.metadata.get("job_id"))
                await runtime.task_coordinator.cancel_task(
                    job_id,
                    reason="validation_pre_attachment_cancel",
                )
                before_attachment = await runtime.task_coordinator.get_task(job_id)
                self.soft_assert_equal(
                    before_attachment.is_terminal if before_attachment else None,
                    False,
                    "Cancellation should wait for pending background attachment",
                )
                original_spawn(captured_spawns.pop())
                terminal = await runtime.task_coordinator.wait_for_tasks(
                    [job_id],
                    timeout_seconds=1.0,
                    terminal_or_attention_only=True,
                    wake_on_attention=False,
                )
                return terminal.snapshots[0]

            terminal = await runtime.task_runner.run_inline(
                ExecutionTaskSpec(
                    kind=ExecutionTaskKind.CHAT,
                    scope="delegate:pre-attachment-parent",
                    source=ExecutionTaskSource.SYSTEM,
                    label="delegate-pre-attachment-parent",
                    authority=LOCAL_USER_AUTHORITY,
                ),
                _launch_and_cancel,
            )
            self.soft_assert_equal(
                terminal.result.get("metadata", {}).get("status"),
                "cancelled",
                "Pre-attachment cancellation should retain a normalized result",
            )

        await _assert_pre_attachment_cancellation_is_recorded()

        async def _patched_create_agent(*args, **kwargs):
            if current_case["name"] == "model_request_limit_failure":
                return _FailingChildAgent(
                    UsageLimitExceeded(
                        "The next request would exceed the request_limit of 75"
                    )
                )
            return await original_create_agent(*args, **kwargs)

        delegate_module.create_agent = _patched_create_agent
        try:
            # --- Basic: delegate fires and completes ---
            checkpoint = self.event_checkpoint()
            basic = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "Run a basic delegate call.",
                    "session_id": "delegate_basic",
                    "tools": ["delegate"],
                    "model": "test",
                },
            )
            assert (
                basic["start_response"].status_code == 200
            ), "Basic delegate chat task should start"
            assert (
                basic["terminal_event"].get("event") == "done"
            ), "Basic delegate call should succeed"
            basic_events = self.events_since(checkpoint)

            self.assert_event_contains(
                basic_events,
                name="delegate_started",
                expected={
                    "workflow_id": "delegate_basic",
                    "model": "test",
                    "model_source": "explicit",
                    "timeout_seconds": configured_delegate_timeout,
                },
            )
            self.assert_event_contains(
                basic_events,
                name="delegate_completed",
                expected={
                    "workflow_id": "delegate_basic",
                    "model": "test",
                    "timeout_seconds": configured_delegate_timeout,
                },
            )
            self.soft_assert(
                "failed:" not in basic["text"].lower(),
                "Basic delegate call should not produce a Monty failure response",
            )

            current_case["name"] = "inherit_parent"
            checkpoint = self.event_checkpoint()
            inherited = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "Test delegate model inheritance.",
                    "session_id": "delegate_inherit_parent_model",
                    "tools": ["delegate"],
                    "model": "test",
                },
            )
            assert inherited["terminal_event"].get("event") == "done"
            self.assert_event_contains(
                self.events_since(checkpoint),
                name="delegate_started",
                expected={
                    "workflow_id": "delegate_inherit_parent_model",
                    "model": "test",
                    "model_source": "parent",
                },
            )

            # --- Forbidden tool stripping is case-insensitive ---
            current_case["name"] = "forbidden_stripping"
            checkpoint = self.event_checkpoint()
            stripping = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "Test forbidden tool stripping.",
                    "session_id": "delegate_forbidden_stripping",
                    "tools": ["delegate"],
                    "model": "test",
                },
            )
            assert (
                stripping["start_response"].status_code == 200
            ), "Forbidden tool stripping chat task should start"
            assert (
                stripping["terminal_event"].get("event") == "done"
            ), "Forbidden tool stripping call should succeed"
            stripping_events = self.events_since(checkpoint)

            self.assert_event_contains(
                stripping_events,
                name="delegate_started",
                expected={
                    "workflow_id": "delegate_forbidden_stripping",
                    "tool_names": ["file_read"],
                    "stripped_tools": ["code_execution", "delegate", "job"],
                },
            )
            self.assert_event_contains(
                stripping_events,
                name="delegate_completed",
                expected={"workflow_id": "delegate_forbidden_stripping"},
            )

            # --- Child tool binding: delegate_tool_binding_resolved fires ---
            current_case["name"] = "child_tools"
            checkpoint = self.event_checkpoint()
            child_tools = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "Test delegate with child tools.",
                    "session_id": "delegate_child_tools",
                    "tools": ["delegate"],
                    "model": "test",
                },
            )
            assert (
                child_tools["start_response"].status_code == 200
            ), "Delegate with child tools chat task should start"
            assert (
                child_tools["terminal_event"].get("event") == "done"
            ), "Delegate with child tools should succeed"
            child_events = self.events_since(checkpoint)

            self.assert_event_contains(
                child_events,
                name="delegate_tool_binding_resolved",
                expected={
                    "workflow_id": "delegate_child_tools",
                    "requested": ["file_read"],
                },
            )
            self.assert_event_contains(
                child_events,
                name="delegate_completed",
                expected={"workflow_id": "delegate_child_tools"},
            )
            from core.runtime.state import get_runtime_context

            runtime = get_runtime_context()
            delegate_tasks = await runtime.task_coordinator.list_tasks(kind="delegate")
            child_job = next(
                (
                    task
                    for task in reversed(delegate_tasks)
                    if task.metadata.get("session_id") == "delegate_child_tools"
                ),
                None,
            )
            recent_activity = (
                child_job.metadata.get("recent_activity", []) if child_job else []
            )
            self.soft_assert_equal(
                [item.get("tool") for item in recent_activity],
                ["file_read"],
                "Delegate jobs should retain bounded Pydantic tool activity",
            )
            self.soft_assert_equal(
                (
                    child_job.metadata.get("tool_call_counts", {}).get("completed", 0)
                    + child_job.metadata.get("tool_call_counts", {}).get("failed", 0)
                    if child_job
                    else None
                ),
                1,
                "Delegate jobs should expose settled child tool-call counts",
            )

            current_case["name"] = "model_request_limit_failure"
            model_request_limit_failure = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "Test delegate model-request limit handling.",
                    "session_id": "delegate_model_request_limit_failure",
                    "tools": ["delegate"],
                    "model": "test",
                },
            )
            assert (
                model_request_limit_failure["start_response"].status_code == 200
            ), "Delegate model-request limit failure chat task should start"
            assert (
                model_request_limit_failure["terminal_event"].get("event") == "done"
            ), "Delegate model-request limit failure should not abort chat"
            request_limit_context = delegate_module._delegate_usage_limit_context(
                UsageLimitExceeded(
                    "The next request would exceed the request_limit of 75"
                )
            )
            self.soft_assert_equal(
                request_limit_context["limit_kind"],
                "model_requests",
                "Delegate request-limit context should classify model-request limits",
            )
            self.soft_assert_equal(
                request_limit_context["limit_setting"],
                "delegate_model_requests_limit",
                "Delegate request-limit context should identify the controlling setting",
            )
            self.soft_assert(
                "model-request limit" in model_request_limit_failure["text"],
                "Delegate request-limit failure should use model-request wording",
            )
            self.soft_assert(
                "goal_ops" in model_request_limit_failure["text"],
                "Delegate request-limit failure should instruct parent to checkpoint before continuing",
            )

            from core.authoring.helpers.runtime_common import (
                invoke_bound_tool,
                normalize_tool_result,
            )
            from core.authoring.shared.tool_binding import resolve_tool_binding

            checkpoint = self.event_checkpoint()
            timeout_binding = resolve_tool_binding(["delegate"], vault_path=str(vault))

            async def _timeout_create_agent(*_args, **_kwargs):
                return _FailingChildAgent(TimeoutError())

            delegate_module.create_agent = _timeout_create_agent
            try:
                timeout_result = await invoke_bound_tool(
                    timeout_binding.tool_functions[0],
                    tool_name="delegate",
                    arguments={"prompt": "Exceed child timeout.", "model": "test"},
                    run_buffers={},
                    session_buffers={},
                    session_id="delegate_timeout_failure",
                    vault_name=vault.name,
                )
            finally:
                delegate_module.create_agent = _patched_create_agent
            timeout_tool_result = normalize_tool_result(
                "delegate",
                timeout_result,
                vault_path=str(vault),
            )
            self.soft_assert_equal(
                timeout_tool_result.metadata.get("status"),
                "failed",
                "Delegate timeout should return a failed tool result",
            )
            self.soft_assert_equal(
                timeout_tool_result.metadata.get("failure_kind"),
                "delegate_internal",
                "An inner TimeoutError should not impersonate the delegate deadline",
            )
            self.soft_assert_equal(
                timeout_tool_result.metadata.get("retryable"),
                False,
                "Delegate timeout metadata should tell the caller not to retry the same broad delegate",
            )
            timeout_events = self.events_since(checkpoint)
            self.assert_event_contains(
                timeout_events,
                name="delegate_started",
                expected={"workflow_id": "delegate_timeout_failure"},
            )
            self.soft_assert(
                "timeout" in timeout_tool_result.return_value.lower(),
                "Delegate timeout should return actionable text",
            )

            async def _billing_failure_create_agent(*_args, **_kwargs):
                return _FailingChildAgent(
                    ModelHTTPError(
                        status_code=400,
                        model_name="gpt-5-mini",
                        body={
                            "error": {
                                "message": "Your credit balance is too low for this request.",
                            },
                        },
                    )
                )

            delegate_module.create_agent = _billing_failure_create_agent
            try:
                billing_result = await invoke_bound_tool(
                    timeout_binding.tool_functions[0],
                    tool_name="delegate",
                    arguments={
                        "prompt": "Trigger child provider billing failure.",
                        "model": "test",
                    },
                    run_buffers={},
                    session_buffers={},
                    session_id="delegate_billing_failure",
                    vault_name=vault.name,
                )
            finally:
                delegate_module.create_agent = _patched_create_agent
            billing_tool_result = normalize_tool_result(
                "delegate",
                billing_result,
                vault_path=str(vault),
            )
            self.soft_assert_equal(
                billing_tool_result.metadata.get("status"),
                "failed",
                "Delegate provider failure should return a failed tool result",
            )
            self.soft_assert_equal(
                billing_tool_result.metadata.get("failure_kind"),
                "billing",
                "Delegate provider billing failure should be classified",
            )
            self.soft_assert_equal(
                billing_tool_result.metadata.get("retryable"),
                False,
                "Delegate provider billing failure should be non-retryable without user action",
            )
            self.soft_assert_equal(
                billing_tool_result.metadata.get("http_status"),
                400,
                "Delegate provider failure metadata should include HTTP status",
            )

        finally:
            chat_executor._prepare_agent_config = original_prepare
            delegate_module.create_agent = original_create_agent

        # --- Monty direct tool: delegate with tools ---
        self.create_file(
            vault,
            "AssistantMD/Authoring/delegate_with_tools.md",
            DELEGATE_WITH_TOOLS_WORKFLOW,
        )
        checkpoint = self.event_checkpoint()
        with_tools_result = await self.run_workflow(vault, "delegate_with_tools")
        self.soft_assert_equal(
            with_tools_result.status,
            "completed",
            "Delegate with tools workflow should complete",
        )
        with_tools_events = self.events_since(checkpoint)

        self.assert_event_contains(
            with_tools_events,
            name="authoring_direct_tool_started",
            expected={
                "workflow_id": "DelegateToolVault/delegate_with_tools",
                "tool": "delegate",
            },
        )
        self.assert_event_contains(
            with_tools_events,
            name="delegate_tool_binding_resolved",
            expected={
                "workflow_id": "DelegateToolVault/delegate_with_tools",
                "requested": ["file_read"],
            },
        )
        self.assert_event_contains(
            with_tools_events,
            name="delegate_completed",
            expected={"workflow_id": "DelegateToolVault/delegate_with_tools"},
        )
        self.assert_event_contains(
            with_tools_events,
            name="authoring_direct_tool_completed",
            expected={
                "workflow_id": "DelegateToolVault/delegate_with_tools",
                "tool": "delegate",
            },
        )

        with_tools_output = vault / "outputs" / "delegate-with-tools-result.md"
        self.soft_assert(
            with_tools_output.exists(),
            "Delegate with tools workflow should write output file",
        )
        if with_tools_output.exists():
            self.soft_assert(
                bool(with_tools_output.read_text(encoding="utf-8").strip()),
                "Delegate with tools output should be non-empty",
            )

        # --- Direct tool result exposes child-run audit metadata for debugging ---
        from core.authoring.helpers.runtime_common import (
            invoke_bound_tool,
            normalize_tool_result,
        )
        from core.authoring.shared.tool_binding import resolve_tool_binding

        audit_binding = resolve_tool_binding(["delegate"], vault_path=str(vault))
        audit_raw_result = await invoke_bound_tool(
            audit_binding.tool_functions[0],
            tool_name="delegate",
            arguments={
                "prompt": "Read notes/content.md and return its text.",
                "tools": ["file_read"],
                "model": "test",
            },
            run_buffers={},
            session_buffers={},
            session_id="delegate_audit_metadata",
            vault_name=vault.name,
        )
        audit_result = normalize_tool_result(
            "delegate",
            audit_raw_result,
            vault_path=str(vault),
        )
        audit = audit_result.metadata.get("audit")
        self.soft_assert(
            isinstance(audit, dict),
            "Delegate direct tool metadata should include child-run audit details",
        )
        if isinstance(audit, dict):
            self.soft_assert(
                audit.get("tool_call_count", 0) >= 1,
                "Delegate audit should count child tool calls",
            )
            tool_calls = audit.get("tool_calls")
            self.soft_assert(
                isinstance(tool_calls, list) and bool(tool_calls),
                "Delegate audit should include compact child tool call entries",
            )
            if isinstance(tool_calls, list) and tool_calls:
                self.soft_assert_equal(
                    tool_calls[0].get("tool"),
                    "file_read",
                    "Delegate audit should record child tool names",
                )
                self.soft_assert(
                    "result" in tool_calls[0],
                    "Delegate audit should include compact child tool return values",
                )

        # --- Monty direct tool: delegate a markdown-with-image source path ---
        self.create_file(
            vault,
            "AssistantMD/Authoring/delegate_markdown_image.md",
            DELEGATE_MARKDOWN_IMAGE_WORKFLOW,
        )
        checkpoint = self.event_checkpoint()
        markdown_image_result = await self.run_workflow(
            vault, "delegate_markdown_image"
        )
        self.soft_assert_equal(
            markdown_image_result.status,
            "completed",
            "Delegate markdown-with-image workflow should complete",
        )
        markdown_image_events = self.events_since(checkpoint)
        self.assert_event_contains(
            markdown_image_events,
            name="delegate_tool_binding_resolved",
            expected={
                "workflow_id": "DelegateToolVault/delegate_markdown_image",
                "requested": ["file_read"],
            },
        )
        markdown_image_output = vault / "outputs" / "delegate-markdown-image-result.md"
        self.soft_assert(
            markdown_image_output.exists(),
            "Delegate markdown-with-image workflow should write output file",
        )

        await self.stop_system()
        self.teardown_scenario()
        self.assert_no_failures()


async def _assert_repeated_failure_guard() -> None:
    from core.llm.capabilities.delegate_repeated_failure_guard import (
        DelegateRepeatedFailureGuard,
    )

    executed: list[str] = []

    async def fail(args):
        executed.append(str(args))
        return ToolReturn(return_value="failed", metadata={"status": "failed"})

    async def succeed(args):
        executed.append(str(args))
        return ToolReturn(return_value="ok", metadata={"status": "completed"})

    guard = DelegateRepeatedFailureGuard(limit=2, session_id="guard-contract")
    await guard.execute(tool_name="probe", args={"a": 1, "b": 2}, handler=fail)
    await guard.execute(tool_name="probe", args={"b": 2, "a": 1}, handler=fail)
    blocked = await guard.execute(
        tool_name="probe",
        args={"a": 1, "b": 2},
        handler=fail,
    )
    assert len(executed) == 2, "Third identical failed execution should be blocked"
    assert blocked.metadata["failure_kind"] == "repeated_tool_failure"

    await guard.execute(tool_name="other", args={}, handler=succeed)
    await guard.execute(tool_name="probe", args={"a": 1, "b": 2}, handler=fail)
    assert (
        len(executed) == 4
    ), "A successful different call should reset the failure streak"

    disabled = DelegateRepeatedFailureGuard(limit=0, session_id="guard-disabled")
    for _ in range(3):
        await disabled.execute(tool_name="probe", args={}, handler=fail)
    assert len(executed) == 7, "Zero should disable the repeated-failure guard"

    repeated_successes = 0

    async def repeated_success(args):
        nonlocal repeated_successes
        repeated_successes += 1
        return ToolReturn(return_value="ok", metadata={"status": "completed"})

    success_guard = DelegateRepeatedFailureGuard(
        limit=1,
        session_id="guard-success-repeat",
    )
    for _ in range(3):
        await success_guard.execute(
            tool_name="status",
            args={"id": "same"},
            handler=repeated_success,
        )
    assert repeated_successes == 3, "Repeated successful calls must remain allowed"

    exception_guard = DelegateRepeatedFailureGuard(
        limit=1,
        session_id="guard-exception-reset",
    )
    await exception_guard.execute(
        tool_name="probe",
        args={"same": True},
        handler=fail,
    )

    async def raise_failure(args):
        raise RuntimeError("unstructured execution failure")

    try:
        await exception_guard.execute(
            tool_name="other",
            args={},
            handler=raise_failure,
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("Tool execution exceptions must propagate")
    executions_before_retry = len(executed)
    await exception_guard.execute(
        tool_name="probe",
        args={"same": True},
        handler=fail,
    )
    assert (
        len(executed) == executions_before_retry + 1
    ), "An intervening exception must reset the structured-failure streak"

    parallel_started = 0
    parallel_release = asyncio.Event()

    async def parallel_failure(args):
        nonlocal parallel_started
        parallel_started += 1
        if parallel_started == 2:
            parallel_release.set()
        await parallel_release.wait()
        return ToolReturn(return_value="failed", metadata={"status": "failed"})

    parallel_guard = DelegateRepeatedFailureGuard(
        limit=1,
        session_id="guard-parallel",
    )
    await asyncio.gather(
        parallel_guard.execute(
            tool_name="probe",
            args={"same": True},
            handler=parallel_failure,
        ),
        parallel_guard.execute(
            tool_name="probe",
            args={"same": True},
            handler=parallel_failure,
        ),
    )
    assert parallel_started == 2, "The guard must not serialize admitted parallel calls"


def _assert_delegate_flight_card() -> None:
    from core.tools.delegate import _apply_delegate_instruction_layers

    class _InstructionRecorder:
        def __init__(self):
            self.layers = []

        def instructions(self, instruction):
            self.layers.append(instruction)

    recorder = _InstructionRecorder()
    task_instructions = "TASK-SPECIFIC-DELEGATE-INSTRUCTIONS"
    _apply_delegate_instruction_layers(
        recorder,
        caller_instructions=task_instructions,
    )
    assert len(recorder.layers) == 2
    assert "DELEGATE FLIGHT CARD" in recorder.layers[0]
    assert recorder.layers[1] == task_instructions


def _assert_delegate_usage_limits() -> None:
    from core.tools.delegate import _delegate_usage_limits

    limits = _delegate_usage_limits()
    assert limits.tool_calls_limit is None
    assert limits.request_limit is None or limits.request_limit > 0


def _assert_shared_tool_result_classification() -> None:
    from core.tools.delegate import (
        _build_child_run_audit,
        _child_run_references,
        _compact_value,
        _delegate_argument_hint,
    )
    from core.tools.failures import classify_tool_result_state

    assert classify_tool_result_state(metadata={"status": "completed"}) == "completed"
    assert classify_tool_result_state(metadata={"status": "failed"}) == "failed"
    assert classify_tool_result_state(outcome="denied") == "failed"
    assert classify_tool_result_state(outcome="interrupted") == "interrupted"
    assert (
        classify_tool_result_state(
            outcome="success",
            metadata={"message": "completed without error"},
        )
        == "completed"
    )
    benign_audit = _build_child_run_audit(
        [
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="status",
                        args={},
                        tool_call_id="benign-result",
                    )
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="status",
                        content="Completed without error.",
                        tool_call_id="benign-result",
                    )
                ]
            ),
        ]
    )
    assert benign_audit["tool_error_count"] == 0

    cyclic_result: dict[str, object] = {"artifact_ref": "artifact://kept"}
    cyclic_result["cycle"] = cyclic_result
    cyclic_messages = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="probe",
                    content=cyclic_result,
                    tool_call_id="cyclic-result",
                )
            ]
        )
    ]
    assert _child_run_references(cyclic_messages) == ["artifact://kept"]
    assert _compact_value(cyclic_result, max_chars=200).startswith("{")
    argument_hint = _delegate_argument_hint(
        {
            "operation": "write",
            "path": "reports/result.md",
            "content": "sensitive generated payload",
        }
    )
    assert "reports/result.md" in argument_hint
    assert "sensitive generated payload" not in argument_hint
    assert "content" in argument_hint
    signed_url_hint = _delegate_argument_hint(
        {
            "url": "https://user:password@example.com/report?token=secret#private",
            "urls": ["https://example.com/second?signature=secret"],
        }
    )
    assert signed_url_hint.count("secret") == 0
    assert "password" not in signed_url_hint
    assert "https://example.com/report" in signed_url_hint
    unresolved_audit = _build_child_run_audit(
        [
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="file_write",
                        args={"path": "possibly-written.md"},
                        tool_call_id="unsettled-call",
                    )
                ]
            )
        ]
    )
    assert unresolved_audit["settled_tool_call_count"] == 0
    assert unresolved_audit["unsettled_tool_call_count"] == 1


async def _assert_retry_prompt_progress() -> None:
    from pydantic_ai import FunctionToolCallEvent, FunctionToolResultEvent
    from pydantic_ai.messages import RetryPromptPart
    from pydantic_ai.usage import RunUsage

    from core.identity import LOCAL_USER_AUTHORITY
    from core.runtime.execution_tasks import ExecutionTaskKind, ExecutionTaskSource
    from core.runtime.state import get_runtime_context
    from core.tools.delegate import _DelegateProgressObserver

    runtime = get_runtime_context()
    task = await runtime.task_coordinator.create_queued_task(
        kind=ExecutionTaskKind.DELEGATE,
        scope="delegate:retry-progress",
        source=ExecutionTaskSource.SYSTEM,
        label="delegate-retry-progress",
        authority=LOCAL_USER_AUTHORITY,
    )
    observer = _DelegateProgressObserver(task)
    observer._record_started(  # noqa: SLF001
        FunctionToolCallEvent(
            ToolCallPart(
                tool_name="file_read",
                args={"path": "missing.md"},
                tool_call_id="retry-call",
            )
        )
    )
    observer._record_finished(  # noqa: SLF001
        FunctionToolResultEvent(
            RetryPromptPart(
                content="Path is required.",
                tool_name="file_read",
                tool_call_id="retry-call",
            )
        )
    )
    await observer._publish(usage=RunUsage())  # noqa: SLF001
    snapshot = await runtime.task_coordinator.get_task(task.task_id)
    counts = snapshot.metadata.get("tool_call_counts", {}) if snapshot else {}
    assert counts.get("invalid") == 1
    assert counts.get("failed") == 1
    assert counts.get("completed") == 0
    await runtime.task_coordinator.mark_completed(task.task_id)


DELEGATE_WITH_TOOLS_WORKFLOW = """---
run_type: workflow
enabled: false
description: Validate delegate with child tool access
---

## Run

```python
result = await delegate(
    prompt="Read notes/content.md and return its text.",
    tools=["file_read"],
    model="test",
)
await file_write(
    operation="write",
    path="outputs/delegate-with-tools-result.md",
    content=result.return_value,
)
await finish(status="completed", reason="delegate-with-tools-ok")
```
"""


DELEGATE_MARKDOWN_IMAGE_WORKFLOW = """---
run_type: workflow
enabled: false
description: Validate delegate with markdown containing an embedded image
---

## Run

```python
result = await delegate(
    prompt="Read notes/with-image.md and describe what source was provided.",
    tools=["file_read"],
    model="test",
)
await file_write(
    operation="write",
    path="outputs/delegate-markdown-image-result.md",
    content=result.return_value,
)
await finish(status="completed", reason="delegate-markdown-image-ok")
```
"""
