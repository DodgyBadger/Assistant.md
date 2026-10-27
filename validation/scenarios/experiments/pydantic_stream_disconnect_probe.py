"""Probe Pydantic AI stream-disconnect behavior at recovery boundaries.

This experiment is deterministic and provider-free. It documents which native
Pydantic AI hooks and events observe a transport failure before the first model
event, after partial text, and after a completed tool call. Keep it in
experiments until the resilience contract is implemented in a stable scenario.
"""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from pydantic_ai import Agent  # noqa: E402
from pydantic_ai.capabilities import Hooks  # noqa: E402
from pydantic_ai.exceptions import ModelAPIError, UnexpectedModelBehavior  # noqa: E402
from pydantic_ai.models.function import (  # noqa: E402
    AgentInfo,
    DeltaToolCall,
    FunctionModel,
)

from core.tools.failures import classify_exception  # noqa: E402
from validation.support.base_scenario import BaseScenario  # noqa: E402


class PydanticStreamDisconnectProbeScenario(BaseScenario):
    """Record framework-native evidence for three stream failure frontiers."""

    async def test_scenario(self) -> None:
        results = {
            "before_first_event": await _probe_before_first_event(),
            "after_partial_text": await _probe_after_partial_text(),
            "after_completed_tool": await _probe_after_completed_tool(),
            "delegate_collect_response": await _probe_delegate_collect_response(),
            "normalized_exception_classification": _probe_normalized_exception_classification(),
        }

        vault = self.create_vault("PydanticStreamDisconnectProbeVault")
        await self.start_system()
        try:
            results["primary_chat_task"] = await _probe_primary_chat_task(self, vault)
            results["delegate_tool_boundary"] = await _probe_delegate_tool_boundary(
                vault
            )
        finally:
            await self.stop_system()

        before = results["before_first_event"]
        self.soft_assert_equal(
            before["error_type"], "ReadError", "Pre-event failure type is stable"
        )
        self.soft_assert(
            "model_request_error:ReadError" not in before["observed"],
            "Pydantic 2.19 stream consumption should bypass the model-request error hook even before its first event",
        )
        self.soft_assert(
            "run_error:ReadError" in before["observed"],
            "Pre-event disconnect should reach the native run-error hook",
        )

        partial = results["after_partial_text"]
        self.soft_assert_equal(
            partial["error_type"], "ReadError", "Partial-stream failure type is stable"
        )
        self.soft_assert(
            "PartStartEvent" in partial["observed"],
            "Partial-stream disconnect should expose native model stream progress",
        )
        self.soft_assert(
            "model_request_error:ReadError" not in partial["observed"],
            "A failure while consuming an open stream should bypass the model-request error hook",
        )
        self.soft_assert(
            "run_error:ReadError" in partial["observed"],
            "Partial-stream disconnect should reach the native run-error hook",
        )
        self.soft_assert_equal(
            partial["failure_history"],
            [
                {"message": "ModelRequest", "parts": ["UserPromptPart"]},
                {"message": "ModelResponse", "parts": ["TextPart"]},
            ],
            "Pydantic should expose the partial streamed response in failure history",
        )

        after_tool = results["after_completed_tool"]
        self.soft_assert_equal(
            after_tool["effects"], ["once"], "The synthetic tool executes exactly once"
        )
        self.soft_assert(
            "before_tool:effect:call-1" in after_tool["observed"]
            and "after_tool:effect:call-1" in after_tool["observed"],
            "Tool hooks should expose the canonical Pydantic tool_call_id",
        )
        self.soft_assert(
            "FunctionToolResultEvent" in after_tool["observed"],
            "A completed tool should emit Pydantic's native tool result event before disconnect",
        )
        self.soft_assert(
            "model_request_error:ReadError" not in after_tool["observed"]
            and "run_error:ReadError" in after_tool["observed"],
            "A post-tool stream disconnect should be visible at the run-error boundary",
        )
        self.soft_assert_equal(
            after_tool["failure_history"],
            [
                {"message": "ModelRequest", "parts": ["UserPromptPart"]},
                {
                    "message": "ModelResponse",
                    "parts": ["ToolCallPart:effect:call-1"],
                },
                {
                    "message": "ModelRequest",
                    "parts": ["ToolReturnPart:effect:call-1"],
                },
                {"message": "ModelResponse", "parts": ["TextPart"]},
            ],
            "Pydantic should retain the settled tool cycle before partial response history",
        )

        delegate = results["delegate_collect_response"]
        self.soft_assert_equal(
            delegate["error_type"],
            "ReadError",
            "Delegate collection should propagate the native stream exception",
        )
        self.soft_assert(
            "PartStartEvent" in delegate["observed"]
            and "run_error:ReadError" in delegate["observed"],
            "Delegate collection should retain native progress evidence at the run-error hook",
        )
        self.soft_assert(
            "model_request_error:ReadError" not in delegate["observed"],
            "Delegate collection should preserve Pydantic's mid-stream hook boundary",
        )

        primary = results["primary_chat_task"]
        self.soft_assert_equal(
            primary["task_status"],
            "failed",
            "A primary chat stream disconnect should fail its detached execution task",
        )
        self.soft_assert_equal(
            primary["event_types"],
            ["delta", "error"],
            "Primary chat should retain partial text then publish one terminal error",
        )
        self.soft_assert_equal(
            primary["failure_kind"],
            "transient_network",
            "Primary chat terminal event should preserve network classification",
        )
        self.soft_assert_equal(
            primary["persisted_roles"],
            ["user"],
            "Failed primary chat should persist the accepted user turn without partial assistant text",
        )
        self.soft_assert_equal(
            primary["latest_failure_error_type"],
            "ReadError",
            "Primary chat should persist an unfinished-turn failure marker",
        )

        normalized = results["normalized_exception_classification"]
        self.soft_assert_equal(
            normalized["model_api_connection"],
            {"failure_kind": "transient_network", "retryable": True},
            "Pydantic connection errors should be classified as retryable network failures",
        )
        self.soft_assert_equal(
            normalized["incomplete_stream"],
            {"failure_kind": "transient_provider", "retryable": True},
            "An explicitly incomplete provider stream should be retryable",
        )

        delegate_boundary = results["delegate_tool_boundary"]
        self.soft_assert_equal(
            delegate_boundary["read_error"]["status"],
            "failed",
            "DelegateTool should contain a recognized raw read disconnect",
        )
        self.soft_assert_equal(
            delegate_boundary["read_error"]["failure_kind"],
            "transient_network",
            "Contained delegate disconnect should preserve network classification",
        )
        self.soft_assert_equal(
            delegate_boundary["read_error"]["audit"],
            {
                "message_count": 0,
                "request_count": 0,
                "response_count": 0,
                "tool_call_count": 0,
                "tool_error_count": 0,
                "tool_calls_truncated": False,
                "tool_calls": [],
            },
            "Failed delegate collection currently loses partial child history from its audit",
        )
        self.soft_assert_equal(
            delegate_boundary["model_api_error"],
            {
                "status": "failed",
                "failure_kind": "transient_network",
                "retryable": True,
            },
            "DelegateTool should contain a normalized Pydantic connection failure",
        )
        self.soft_assert_equal(
            delegate_boundary["retry_without_tools"],
            {"status": "completed", "stream_attempts": 2},
            "A tool-free delegate should recover from one transient stream failure",
        )
        self.soft_assert_equal(
            delegate_boundary["disabled_retry"],
            {"status": "failed", "stream_attempts": 1},
            "A zero retry budget should disable automatic stream replay",
        )
        self.soft_assert_equal(
            delegate_boundary["no_retry_with_tools"],
            {"status": "failed", "stream_attempts": 1},
            "A tool-enabled delegate should fail closed without effect-aware recovery",
        )

        for case_name in (
            "before_first_event",
            "after_partial_text",
            "after_completed_tool",
            "delegate_collect_response",
            "primary_chat_task",
        ):
            result = results[case_name]
            self.soft_assert_equal(
                result["failure_kind"],
                "transient_network",
                "AssistantMD should classify synthetic read disconnects as transient network failures",
            )
            self.soft_assert_equal(
                result["retryable"],
                True,
                "Synthetic read disconnects should remain retryable in failure metadata",
            )

        (self.artifacts_dir / "pydantic_stream_disconnects.json").write_text(
            json.dumps(results, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        self.teardown_scenario()
        self.assert_no_failures()


def _recording_hooks(
    observed: list[str], failure_history: list[dict[str, Any]] | None = None
) -> Hooks:
    hooks = Hooks()

    @hooks.on.event
    async def record_event(_ctx, event):
        observed.append(type(event).__name__)
        return event

    @hooks.on.model_request_error
    async def record_model_request_error(_ctx, *, request_context, error):
        del request_context
        observed.append(f"model_request_error:{type(error).__name__}")
        raise error

    @hooks.on.run_error
    async def record_run_error(ctx, *, error):
        observed.append(f"run_error:{type(error).__name__}")
        observed.append(f"run_error_message_count:{len(ctx.messages)}")
        raise error

    @hooks.on.node_run_error
    async def record_node_run_error(ctx, *, node, error):
        observed.append(f"node_run_error:{type(node).__name__}:{type(error).__name__}")
        if failure_history is not None:
            failure_history.extend(_message_shapes(ctx.messages))
        raise error

    return hooks


def _message_shapes(messages: list[Any]) -> list[dict[str, Any]]:
    shaped: list[dict[str, Any]] = []
    for message in messages:
        parts: list[str] = []
        for part in message.parts:
            label = type(part).__name__
            tool_name = getattr(part, "tool_name", None)
            tool_call_id = getattr(part, "tool_call_id", None)
            if tool_name is not None or tool_call_id is not None:
                label = f"{label}:{tool_name}:{tool_call_id}"
            parts.append(label)
        shaped.append({"message": type(message).__name__, "parts": parts})
    return shaped


def _read_error(message: str) -> httpx.ReadError:
    return httpx.ReadError(
        message,
        request=httpx.Request("POST", "https://provider.test/v1/responses"),
    )


async def _consume_failure(agent: Agent, observed: list[str]) -> dict[str, Any]:
    try:
        async with agent.run_stream_events("probe stream resilience") as events:
            async for _ in events:
                pass
    except Exception as exc:  # noqa: BLE001 - the probe records the framework boundary
        classification = classify_exception(exc, phase="agent_stream")
        return {
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "failure_kind": classification.failure_kind,
            "retryable": classification.retryable,
            "observed": observed,
        }
    raise AssertionError("Synthetic stream disconnect unexpectedly completed")


async def _probe_before_first_event() -> dict[str, Any]:
    observed: list[str] = []

    async def stream(_messages, _info: AgentInfo) -> AsyncIterator[str]:
        if False:  # pragma: no cover - preserve async-generator type before raising
            yield "unreachable"
        raise _read_error("disconnect-before-first-event")

    agent = Agent(
        FunctionModel(stream_function=stream), capabilities=[_recording_hooks(observed)]
    )
    return await _consume_failure(agent, observed)


async def _probe_after_partial_text() -> dict[str, Any]:
    observed: list[str] = []
    failure_history: list[dict[str, Any]] = []

    async def stream(_messages, _info: AgentInfo) -> AsyncIterator[str]:
        yield "partial child output"
        raise _read_error("disconnect-after-partial-text")

    agent = Agent(
        FunctionModel(stream_function=stream),
        capabilities=[_recording_hooks(observed, failure_history)],
    )
    result = await _consume_failure(agent, observed)
    result["failure_history"] = failure_history
    return result


async def _probe_after_completed_tool() -> dict[str, Any]:
    observed: list[str] = []
    effects: list[str] = []
    failure_history: list[dict[str, Any]] = []

    async def stream(
        messages, _info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        tool_already_returned = any(
            getattr(part, "tool_name", None) == "effect"
            for message in messages
            for part in message.parts
        )
        if not tool_already_returned:
            yield {
                0: DeltaToolCall(
                    name="effect",
                    json_args='{"value":"once"}',
                    tool_call_id="call-1",
                )
            }
            return
        yield "partial output after tool"
        raise _read_error("disconnect-after-completed-tool")

    hooks = _recording_hooks(observed, failure_history)

    @hooks.on.before_tool_execute
    async def record_before_tool(_ctx, *, call, tool_def, args):
        del tool_def
        observed.append(f"before_tool:{call.tool_name}:{call.tool_call_id}")
        return args

    @hooks.on.after_tool_execute
    async def record_after_tool(_ctx, *, call, tool_def, args, result):
        del tool_def, args
        observed.append(f"after_tool:{call.tool_name}:{call.tool_call_id}")
        return result

    agent = Agent(FunctionModel(stream_function=stream), capabilities=[hooks])

    @agent.tool_plain
    async def effect(value: str) -> str:
        effects.append(value)
        return "effect-complete"

    result = await _consume_failure(agent, observed)
    result["effects"] = effects
    result["failure_history"] = failure_history
    return result


async def _probe_delegate_collect_response() -> dict[str, Any]:
    from core.llm.agents import collect_response

    observed: list[str] = []

    async def stream(_messages, _info: AgentInfo) -> AsyncIterator[str]:
        yield "partial delegate output"
        raise _read_error("disconnect-inside-delegate-collection")

    agent = Agent(
        FunctionModel(stream_function=stream),
        capabilities=[_recording_hooks(observed)],
    )
    try:
        await collect_response(agent, "probe delegate collection")
    except Exception as exc:  # noqa: BLE001 - the probe records the helper boundary
        classification = classify_exception(exc, phase="delegate_child_run")
        return {
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "failure_kind": classification.failure_kind,
            "retryable": classification.retryable,
            "observed": observed,
        }
    raise AssertionError("Synthetic delegate stream disconnect unexpectedly completed")


async def _probe_primary_chat_task(
    scenario: BaseScenario,
    vault: Path,
) -> dict[str, Any]:
    from core.chat.chat_store import ChatStore
    from core.chat.executor import PreparedChatExecution
    from core.chat.task_execution import (
        CHAT_TASK_EVENT_BUFFER,
        start_prepared_chat_stream_task,
    )

    session_id = "pydantic_stream_disconnect_primary_chat"
    ChatStore().ensure_session(
        session_id,
        vault.name,
        owner_principal_id="local-user",
    )
    observed: list[str] = []

    async def stream(_messages, _info: AgentInfo) -> AsyncIterator[str]:
        yield "partial primary output"
        raise _read_error("disconnect-inside-primary-chat")

    agent = Agent(
        FunctionModel(stream_function=stream),
        capabilities=[_recording_hooks(observed)],
    )
    started = await start_prepared_chat_stream_task(
        prepared=PreparedChatExecution(
            agent=agent,
            message_history=None,
            prompt_for_history="probe primary chat stream",
            user_prompt="probe primary chat stream",
            attached_image_count=0,
            model="function-probe",
            tools=[],
        ),
        vault_name=vault.name,
        vault_path=str(vault),
        session_id=session_id,
    )
    terminal = await scenario._wait_for_execution_task(
        started.task.task_id
    )  # noqa: SLF001
    events = await CHAT_TASK_EVENT_BUFFER.events_after(started.task.task_id)
    detail_response = scenario.call_api(
        f"/api/chat/sessions/{session_id}?vault_name={vault.name}"
    )
    assert detail_response.status_code == 200
    detail = detail_response.json()
    latest_failure = detail.get("latest_failure") or {}
    error_event = next(event for event in events if event.event == "error")
    return {
        "task_id": started.task.task_id,
        "task_status": terminal.get("status"),
        "event_types": [event.event for event in events],
        "error_type": error_event.data.get("details", {}).get("error_type"),
        "failure_kind": error_event.data.get("details", {}).get("failure_kind"),
        "retryable": error_event.data.get("details", {}).get("retryable"),
        "persisted_roles": [
            message.get("role") for message in detail.get("messages", [])
        ],
        "latest_failure_error_type": latest_failure.get("error_type"),
        "observed": observed,
    }


def _probe_normalized_exception_classification() -> dict[str, dict[str, Any]]:
    cases = {
        "model_api_connection": ModelAPIError(
            model_name="provider-model",
            message="Connection error.",
        ),
        "incomplete_stream": UnexpectedModelBehavior(
            "Streamed response ended without content or tool calls"
        ),
    }
    results: dict[str, dict[str, Any]] = {}
    for name, error in cases.items():
        classification = classify_exception(error, phase="agent_stream")
        results[name] = {
            "failure_kind": classification.failure_kind,
            "retryable": classification.retryable,
        }
    return results


async def _probe_delegate_tool_boundary(vault: Path) -> dict[str, Any]:
    import core.tools.delegate as delegate_module
    from core.authoring.helpers.runtime_common import (
        invoke_bound_tool,
        normalize_tool_result,
    )
    from core.authoring.shared.tool_binding import resolve_tool_binding

    binding = resolve_tool_binding(["delegate"], vault_path=str(vault))
    original_create_agent = delegate_module.create_agent
    original_stream_retries = delegate_module.get_model_stream_retries
    original_retry_delay = delegate_module.get_model_stream_retry_base_delay_seconds
    active_error: dict[str, Exception] = {
        "value": _read_error("delegate-tool-read-disconnect")
    }
    remaining_failures = {"value": -1}
    stream_attempts = {"value": 0}

    async def stream(_messages, _info: AgentInfo) -> AsyncIterator[str]:
        stream_attempts["value"] += 1
        if remaining_failures["value"] != 0:
            yield "partial output hidden by failed delegate result"
            if remaining_failures["value"] > 0:
                remaining_failures["value"] -= 1
            raise active_error["value"]
        yield "recovered delegate output"

    async def create_failing_agent(*_args, **_kwargs):
        return Agent(FunctionModel(stream_function=stream))

    delegate_module.create_agent = create_failing_agent
    delegate_module.get_model_stream_retry_base_delay_seconds = lambda: 0
    try:
        raw_read_result = await invoke_bound_tool(
            binding.tool_functions[0],
            tool_name="delegate",
            arguments={"prompt": "probe raw read disconnect", "model": "test"},
            run_buffers={},
            session_buffers={},
            session_id="delegate_tool_read_disconnect",
            vault_name=vault.name,
        )
        read_result = normalize_tool_result(
            "delegate",
            raw_read_result,
            vault_path=str(vault),
        )

        active_error["value"] = ModelAPIError(
            model_name="provider-model",
            message="Connection error.",
        )
        try:
            raw_model_api_result = await invoke_bound_tool(
                binding.tool_functions[0],
                tool_name="delegate",
                arguments={
                    "prompt": "probe normalized provider disconnect",
                    "model": "test",
                },
                run_buffers={},
                session_buffers={},
                session_id="delegate_tool_model_api_disconnect",
                vault_name=vault.name,
            )
        except Exception as exc:  # noqa: BLE001 - capture current tool boundary
            model_api_result = {
                "raised_error_type": type(exc).__name__,
                "raised_error_message": str(exc),
            }
        else:
            normalized_model_api_result = normalize_tool_result(
                "delegate",
                raw_model_api_result,
                vault_path=str(vault),
            )
            model_api_result = {
                "status": normalized_model_api_result.metadata.get("status"),
                "failure_kind": normalized_model_api_result.metadata.get(
                    "failure_kind"
                ),
                "retryable": normalized_model_api_result.metadata.get("retryable"),
            }

        active_error["value"] = _read_error("delegate-tool-flaky-disconnect")
        remaining_failures["value"] = 1
        stream_attempts["value"] = 0
        raw_retry_result = await invoke_bound_tool(
            binding.tool_functions[0],
            tool_name="delegate",
            arguments={"prompt": "probe retry", "model": "test"},
            run_buffers={},
            session_buffers={},
            session_id="delegate_tool_retry_disconnect",
            vault_name=vault.name,
        )
        retry_result = normalize_tool_result(
            "delegate",
            raw_retry_result,
            vault_path=str(vault),
        )
        retry_stream_attempts = stream_attempts["value"]

        delegate_module.get_model_stream_retries = lambda: 0
        remaining_failures["value"] = 1
        stream_attempts["value"] = 0
        raw_disabled_retry_result = await invoke_bound_tool(
            binding.tool_functions[0],
            tool_name="delegate",
            arguments={"prompt": "probe disabled retry", "model": "test"},
            run_buffers={},
            session_buffers={},
            session_id="delegate_tool_disabled_retry",
            vault_name=vault.name,
        )
        disabled_retry_result = normalize_tool_result(
            "delegate",
            raw_disabled_retry_result,
            vault_path=str(vault),
        )
        disabled_retry_stream_attempts = stream_attempts["value"]
        delegate_module.get_model_stream_retries = original_stream_retries

        active_error["value"] = _read_error("delegate-tool-effect-unsafe-disconnect")
        remaining_failures["value"] = -1
        stream_attempts["value"] = 0
        raw_tool_result = await invoke_bound_tool(
            binding.tool_functions[0],
            tool_name="delegate",
            arguments={
                "prompt": "probe tool-enabled retry gate",
                "model": "test",
                "tools": ["file_read"],
            },
            run_buffers={},
            session_buffers={},
            session_id="delegate_tool_effect_retry_gate",
            vault_name=vault.name,
        )
        tool_result = normalize_tool_result(
            "delegate",
            raw_tool_result,
            vault_path=str(vault),
        )
        tool_stream_attempts = stream_attempts["value"]
    finally:
        delegate_module.create_agent = original_create_agent
        delegate_module.get_model_stream_retries = original_stream_retries
        delegate_module.get_model_stream_retry_base_delay_seconds = original_retry_delay

    return {
        "read_error": {
            "status": read_result.metadata.get("status"),
            "failure_kind": read_result.metadata.get("failure_kind"),
            "retryable": read_result.metadata.get("retryable"),
            "audit": read_result.metadata.get("audit"),
        },
        "model_api_error": model_api_result,
        "retry_without_tools": {
            "status": retry_result.metadata.get("status"),
            "stream_attempts": retry_stream_attempts,
        },
        "disabled_retry": {
            "status": disabled_retry_result.metadata.get("status"),
            "stream_attempts": disabled_retry_stream_attempts,
        },
        "no_retry_with_tools": {
            "status": tool_result.metadata.get("status"),
            "stream_attempts": tool_stream_attempts,
        },
    }
