"""Delegate tool - run a supervised child agent and return its output."""

import asyncio
import json
from collections.abc import AsyncIterable, Awaitable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Literal

from pydantic_ai import FunctionToolCallEvent, FunctionToolResultEvent, RunContext
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import (
    AgentStreamEvent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturn,
    ToolReturnPart,
)
from pydantic_ai.tools import Tool
from pydantic_ai.usage import RunUsage, UsageLimits

from core.authoring.helpers.runtime_common import coerce_output_data
from core.authoring.shared.execution_prep import (
    _THINKING_UNSET,
    resolve_effective_thinking,
)
from core.authoring.shared.tool_binding import resolve_tool_binding
from core.constants import (
    DELEGATE_AUDIT_MAX_ARGUMENT_CHARS,
    DELEGATE_AUDIT_MAX_RESULT_CHARS,
    DELEGATE_AUDIT_MAX_TOOL_CALLS,
    DELEGATE_FLIGHT_CARD,
)
from core.identity import (
    LOCAL_USER_AUTHORITY,
    ExecutionAuthority,
    get_current_execution_authority,
)
from core.llm.agents import AgentRunProgress, collect_response, create_agent
from core.llm.capabilities.assistant_tools import build_assistant_tools_capabilities
from core.llm.capabilities.delegate_repeated_failure_guard import (
    build_delegate_repeated_failure_capability,
)
from core.llm.model_factory import build_model_instance
from core.llm.model_selection import ModelExecutionSpec
from core.llm.stream_retry import ModelStreamRetryPolicy
from core.llm.thinking import (
    ThinkingValue,
    normalize_thinking_value,
    thinking_value_to_label,
)
from core.logger import UnifiedLogger
from core.runtime.execution_tasks import (
    ExecutionTaskKind,
    ExecutionTaskSnapshot,
    ExecutionTaskSource,
    ExecutionTaskStatus,
    get_current_execution_task,
)
from core.runtime.state import get_runtime_context
from core.runtime.task_runner import (
    ExecutionConcurrencyPolicy,
    ExecutionConcurrencyWait,
    ExecutionTaskHooks,
    ExecutionTaskRunOutcome,
    ExecutionTaskSpec,
)
from core.settings import (
    get_default_model_thinking,
    get_delegate_model_requests_limit,
    get_delegate_repeated_failure_limit,
    get_delegate_timeout_seconds,
    get_max_concurrent_delegates,
)
from core.tools.base import BaseTool
from core.tools.failures import (
    FailureClassification,
    classify_exception,
    classify_tool_result_state,
)
from core.web.security import sanitize_url_for_log

logger = UnifiedLogger(tag="delegate-tool")

_FORBIDDEN_CHILD_TOOLS = frozenset({"delegate", "code_execution", "job"})
_SUPPORTED_OPTION_KEYS = frozenset({"thinking"})
_DELEGATE_PARTIAL_OUTPUT_MAX_CHARS = 4_000
_DELEGATE_MAX_HANDOFF_REFERENCES = 20
_DELEGATE_MAX_HANDOFF_REFERENCE_NODES = 1_000
_DELEGATE_MODEL_PROGRESS_INTERVAL_SECONDS = 1.0
_DELEGATE_VISIBLE_ARGUMENT_KEYS = frozenset(
    {
        "cache_ref",
        "operation",
        "path",
        "paths",
        "query",
        "queries",
        "ref",
        "url",
        "urls",
    }
)


async def _finish_cancellation_cleanup(cleanup_awaitable: Awaitable[None]) -> None:
    """Finish cancellation publication even if another cancel request arrives."""
    cleanup: asyncio.Future[None] = asyncio.ensure_future(cleanup_awaitable)
    cancelled_during_cleanup = False
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            cancelled_during_cleanup = True
    await cleanup
    if cancelled_during_cleanup:
        raise asyncio.CancelledError


@dataclass(frozen=True)
class DelegateLaunchSpec:
    """Immutable inputs captured before a delegate leaves its parent context."""

    prompt: str
    instructions: str | None
    model: str | None
    tool_names: tuple[str, ...]
    stripped_tools: tuple[str, ...]
    resolved_thinking: ThinkingValue
    thinking_source: str
    vault_path: str
    week_start_day: int
    session_id: str
    authority: ExecutionAuthority
    parent_task_id: str | None
    repeated_failure_limit: int
    timeout_seconds: float
    mode: Literal["blocking", "managed"]


@dataclass
class _ActiveDelegateToolCall:
    tool: str
    call_id: str
    arguments: str
    started_at: str
    started_monotonic: float


class _DelegateProgressObserver:
    """Project bounded Pydantic stream activity into execution-task state."""

    def __init__(self, task: ExecutionTaskSnapshot) -> None:
        self._task = task
        self._active: dict[str, _ActiveDelegateToolCall] = {}
        self._recent: list[dict[str, Any]] = []
        self._counts = {
            "started": 0,
            "completed": 0,
            "failed": 0,
            "invalid": 0,
            "unsettled": 0,
        }
        self._model_event_count = 0
        self._attention_published = False
        self._attention_logged = False
        self._last_publish_monotonic = 0.0
        self._lock = asyncio.Lock()

    async def handle_events(
        self,
        ctx: RunContext[Any],
        events: AsyncIterable[AgentStreamEvent],
    ) -> None:
        async for event in events:
            async with self._lock:
                self._model_event_count += 1
                is_tool_event = isinstance(
                    event,
                    FunctionToolCallEvent | FunctionToolResultEvent,
                )
                if isinstance(event, FunctionToolCallEvent):
                    self._record_started(event)
                elif isinstance(event, FunctionToolResultEvent):
                    self._record_finished(event)
                now = monotonic()
                if (
                    is_tool_event
                    or now - self._last_publish_monotonic
                    >= _DELEGATE_MODEL_PROGRESS_INTERVAL_SECONDS
                ):
                    await self._publish(usage=ctx.usage)
                    self._last_publish_monotonic = now

    def _record_started(self, event: FunctionToolCallEvent) -> None:
        part = event.part
        try:
            arguments: Any = json.loads(part.args_as_json_str())
        except Exception:  # noqa: BLE001 - defensive upstream event compatibility
            arguments = getattr(part, "args", "")
        call = _ActiveDelegateToolCall(
            tool=str(getattr(part, "tool_name", "tool")),
            call_id=event.tool_call_id,
            arguments=_delegate_argument_hint(arguments),
            started_at=datetime.now(UTC).isoformat(),
            started_monotonic=monotonic(),
        )
        self._active[event.tool_call_id] = call
        self._counts["started"] += 1
        self._counts["unsettled"] = len(self._active)

    def _record_finished(self, event: FunctionToolResultEvent) -> None:
        part = event.part
        call = self._active.pop(event.tool_call_id, None)
        if isinstance(part, RetryPromptPart):
            outcome = "invalid"
            metadata_dict = {
                "status": "failed",
                "failure_kind": "tool_retry_prompt",
            }
            terminal_state = "failed"
        else:
            outcome = str(getattr(part, "outcome", "success") or "success")
            metadata = getattr(part, "metadata", None)
            metadata_dict = metadata if isinstance(metadata, dict) else {}
            terminal_state = classify_tool_result_state(
                outcome=outcome,
                metadata=metadata_dict,
            )
        if terminal_state == "failed":
            self._counts["failed"] += 1
        else:
            self._counts["completed"] += 1
        if outcome == "invalid":
            self._counts["invalid"] += 1
        now = monotonic()
        self._recent.append(
            {
                "tool": (
                    call.tool
                    if call is not None
                    else str(getattr(part, "tool_name", "tool"))
                ),
                "call_id": event.tool_call_id,
                "arguments": call.arguments if call is not None else "",
                "started_at": call.started_at if call is not None else None,
                "finished_at": datetime.now(UTC).isoformat(),
                "duration_seconds": (
                    max(0.0, now - call.started_monotonic) if call is not None else None
                ),
                "outcome": outcome,
                "terminal_state": terminal_state,
            }
        )
        self._recent = self._recent[-20:]
        self._counts["unsettled"] = len(self._active)
        if metadata_dict.get("failure_kind") == "repeated_tool_failure":
            self._attention_published = True

    async def _publish(self, *, usage: RunUsage) -> None:
        health_status = "attention_required" if self._attention_published else "healthy"
        await get_runtime_context().task_coordinator.publish_progress(
            self._task.task_id,
            metadata={
                "active_tools": [
                    {
                        "tool": call.tool,
                        "call_id": call.call_id,
                        "arguments": call.arguments,
                        "started_at": call.started_at,
                    }
                    for call in self._active.values()
                ],
                "recent_activity": list(self._recent),
                "tool_call_counts": dict(self._counts),
                "model_event_count": self._model_event_count,
                "usage": _delegate_usage_metadata(usage),
            },
            health_status=health_status,
        )
        if self._attention_published and not self._attention_logged:
            self._attention_logged = True
            logger.add_sink("validation").warning(
                "delegate_attention_required",
                data={
                    "event": "delegate_attention_required",
                    "task_id": self._task.task_id,
                    "parent_task_id": self._task.parent_task_id,
                    "reason": "repeated_tool_failure",
                    "active_tool_names": [call.tool for call in self._active.values()],
                    "tool_call_counts": dict(self._counts),
                },
            )


class DelegateTool(BaseTool):
    """Run a supervised child agent over a prompt with optional tools."""

    @classmethod
    def get_tool(cls, vault_path: str | None = None) -> Tool:
        _vault_path = vault_path or ""

        async def _execute_delegate(
            spec: DelegateLaunchSpec,
            task: ExecutionTaskSnapshot,
            progress: AgentRunProgress,
        ) -> ToolReturn:
            session_id = spec.session_id
            prompt = spec.prompt
            model_value = spec.model
            safe_tool_names = spec.tool_names
            stripped = spec.stripped_tools
            resolved_thinking = spec.resolved_thinking
            thinking_source = spec.thinking_source
            repeated_failure_limit = spec.repeated_failure_limit
            timeout_seconds = spec.timeout_seconds

            logger.add_sink("validation").info(
                "delegate_started",
                data={
                    "workflow_id": session_id,
                    "task_id": task.task_id,
                    "parent_task_id": task.parent_task_id,
                    "mode": spec.mode,
                    "model": model_value or "default",
                    "tool_names": list(safe_tool_names),
                    "stripped_tools": list(stripped),
                    "resolved_thinking": thinking_value_to_label(resolved_thinking),
                    "thinking_source": thinking_source,
                    "repeated_failure_limit": repeated_failure_limit,
                    "timeout_seconds": timeout_seconds,
                },
            )

            progress_observer = _DelegateProgressObserver(task)
            deadline = asyncio.timeout(_delegate_wait_timeout(timeout_seconds))

            try:
                resolved_model = None
                if model_value:
                    resolved_model = build_model_instance(
                        model_value, thinking=resolved_thinking
                    )
                    if (
                        isinstance(resolved_model, ModelExecutionSpec)
                        and resolved_model.mode == "skip"
                    ):
                        raise ValueError("delegate does not support skip model mode")

                tool_capabilities: list[Any] = []
                if safe_tool_names:
                    binding = resolve_tool_binding(
                        list(safe_tool_names),
                        vault_path=spec.vault_path,
                        week_start_day=spec.week_start_day,
                    )
                    tool_capabilities = build_assistant_tools_capabilities(
                        tools=binding.tool_functions,
                        instructions="",
                    )
                    repeated_failure_capability = (
                        build_delegate_repeated_failure_capability(
                            limit=repeated_failure_limit,
                            session_id=session_id,
                        )
                    )
                    if repeated_failure_capability is not None:
                        tool_capabilities.append(repeated_failure_capability)
                    logger.set_sinks(["validation"]).info(
                        "delegate_tool_binding_resolved",
                        data={
                            "workflow_id": session_id,
                            "requested": list(safe_tool_names),
                            "bound": binding.tool_names(),
                        },
                    )

                agent = await create_agent(
                    model=resolved_model,
                    capabilities=tool_capabilities,
                    thinking=resolved_thinking,
                )
                _apply_delegate_instruction_layers(
                    agent,
                    caller_instructions=spec.instructions,
                )

                usage_limits = _delegate_usage_limits()
                async with deadline:
                    result = await _collect_delegate_response(
                        agent=agent,
                        prompt=prompt,
                        usage_limits=usage_limits,
                        allow_retry=not safe_tool_names,
                        session_id=session_id,
                        model=model_value or "default",
                        progress=progress,
                        event_stream_handler=progress_observer.handle_events,
                    )
                output = result.output
                text = coerce_output_data(output)
                audit = _build_child_run_audit(result.messages)
            except UsageLimitExceeded as exc:
                limit_context = _delegate_usage_limit_context(exc)
                classification = classify_exception(exc, phase="delegate_child_run")
                classification = FailureClassification(
                    error_type=classification.error_type,
                    failure_kind=classification.failure_kind,
                    retryable=classification.retryable,
                    phase=classification.phase,
                    message=classification.message,
                    suggested_action=limit_context["suggested_action"],
                    http_status=classification.http_status,
                    retry_after=classification.retry_after,
                    metadata={
                        **classification.metadata,
                        "limit_kind": limit_context["limit_kind"],
                        "limit_setting": limit_context["limit_setting"],
                        "limit": limit_context["limit"],
                    },
                )
                return _failed_delegate_return(
                    session_id=session_id,
                    task_id=task.task_id,
                    parent_task_id=task.parent_task_id,
                    mode=spec.mode,
                    model=model_value or "default",
                    tool_names=safe_tool_names,
                    stripped_tools=stripped,
                    thinking=thinking_value_to_label(resolved_thinking),
                    timeout_seconds=timeout_seconds,
                    repeated_failure_limit=repeated_failure_limit,
                    progress=progress,
                    classification=classification,
                    message=limit_context["message"],
                )
            except TimeoutError as exc:
                if not deadline.expired():
                    classification = _delegate_runtime_failure_classification(exc)
                    return _failed_delegate_return(
                        session_id=session_id,
                        task_id=task.task_id,
                        parent_task_id=task.parent_task_id,
                        mode=spec.mode,
                        model=model_value or "default",
                        tool_names=safe_tool_names,
                        stripped_tools=stripped,
                        thinking=thinking_value_to_label(resolved_thinking),
                        timeout_seconds=timeout_seconds,
                        repeated_failure_limit=repeated_failure_limit,
                        progress=progress,
                        classification=classification,
                        message=(
                            "Delegate stopped because the child run raised an internal "
                            f"{type(exc).__name__}. {classification.suggested_action}"
                        ),
                    )
                classification = FailureClassification(
                    error_type=type(exc).__name__,
                    failure_kind="delegate_timeout",
                    retryable=False,
                    phase="delegate_child_run",
                    message=str(exc),
                    suggested_action=(
                        "Do not retry the same broad delegation. Split the work into smaller delegate calls, "
                        "narrow the file/web scope, or save an intermediate artifact."
                    ),
                )
                return _failed_delegate_return(
                    session_id=session_id,
                    task_id=task.task_id,
                    parent_task_id=task.parent_task_id,
                    mode=spec.mode,
                    model=model_value or "default",
                    tool_names=safe_tool_names,
                    stripped_tools=stripped,
                    thinking=thinking_value_to_label(resolved_thinking),
                    timeout_seconds=timeout_seconds,
                    repeated_failure_limit=repeated_failure_limit,
                    progress=progress,
                    classification=classification,
                    message=(
                        f"Delegate stopped because the child agent exceeded its timeout of "
                        f"{timeout_seconds:g} seconds. Do not retry the same broad delegation. Split the work "
                        "into smaller delegate calls, narrow the file/web scope, or ask the child to save an "
                        "intermediate artifact and return only a compact summary/path."
                    ),
                )
            except Exception as exc:
                classification = _delegate_runtime_failure_classification(exc)
                return _failed_delegate_return(
                    session_id=session_id,
                    task_id=task.task_id,
                    parent_task_id=task.parent_task_id,
                    mode=spec.mode,
                    model=model_value or "default",
                    tool_names=safe_tool_names,
                    stripped_tools=stripped,
                    thinking=thinking_value_to_label(resolved_thinking),
                    timeout_seconds=timeout_seconds,
                    repeated_failure_limit=repeated_failure_limit,
                    progress=progress,
                    classification=classification,
                    message=(
                        f"Delegate stopped because the child agent hit a "
                        f"{classification.failure_kind} failure. {classification.suggested_action}"
                    ),
                )

            metadata: dict[str, Any] = {
                "status": "completed",
                "model": model_value or "default",
                "tool_names": list(safe_tool_names),
                "thinking": thinking_value_to_label(resolved_thinking),
                "output_chars": len(text),
                "repeated_failure_limit": repeated_failure_limit,
                "timeout_seconds": timeout_seconds,
                "audit": audit,
                "usage": _delegate_usage_metadata(progress.usage),
            }
            if stripped:
                metadata["stripped_tools"] = list(stripped)

            logger.add_sink("validation").info(
                "delegate_completed",
                data={
                    "workflow_id": session_id,
                    "task_id": task.task_id,
                    "parent_task_id": task.parent_task_id,
                    "mode": spec.mode,
                    "model": model_value or "default",
                    "tool_names": list(safe_tool_names),
                    "output_chars": len(text),
                    "child_tool_call_count": audit["tool_call_count"],
                    "child_tool_error_count": audit["tool_error_count"],
                    "timeout_seconds": timeout_seconds,
                    **_delegate_usage_metadata(progress.usage),
                },
            )

            return ToolReturn(return_value=text, content=None, metadata=metadata)

        async def delegate(
            ctx: RunContext,
            prompt: str,
            instructions: str | None = None,
            model: str | None = None,
            tools: list[str] | None = None,
            options: dict | None = None,
            mode: Literal["blocking", "managed"] = "blocking",
        ) -> ToolReturn:
            """Run a focused child agent over a prompt with optional tools.

            Blocking mode returns the completed child output. Managed mode returns
            a process-local job handle immediately so independent work can continue.

            :param prompt: Primary prompt for the child agent.
            :param instructions: Optional system-style instructions for the child agent.
            :param model: Optional model alias.
            :param tools: Optional list of tool names available to the child agent.
            :param options: Optional controls: thinking.
            :param mode: blocking or managed.
            """
            normalized_prompt = str(prompt or "").strip()
            if not normalized_prompt:
                raise ValueError("delegate requires a non-empty 'prompt'")
            if mode not in {"blocking", "managed"}:
                raise ValueError("delegate mode must be 'blocking' or 'managed'")

            model_value = str(model).strip() if model else None
            requested_tools = tuple(name.lower() for name in _parse_tool_names(tools))
            safe_tool_names = tuple(
                name for name in requested_tools if name not in _FORBIDDEN_CHILD_TOOLS
            )
            stripped_tools = tuple(sorted(set(requested_tools) - set(safe_tool_names)))
            requested_thinking, timeout_seconds = _parse_options(options or {})
            resolved_thinking, thinking_source = resolve_effective_thinking(
                requested_thinking=requested_thinking,
                default_thinking=get_default_model_thinking(),
            )
            parent_task = get_current_execution_task()
            if mode == "managed" and parent_task is None:
                raise ValueError(
                    "managed delegate mode requires an owning execution task"
                )
            authority = get_current_execution_authority() or LOCAL_USER_AUTHORITY
            spec = DelegateLaunchSpec(
                prompt=normalized_prompt,
                instructions=instructions,
                model=model_value,
                tool_names=safe_tool_names,
                stripped_tools=stripped_tools,
                resolved_thinking=resolved_thinking,
                thinking_source=thinking_source,
                vault_path=_vault_path,
                week_start_day=int(getattr(ctx.deps, "week_start_day", 0) or 0),
                session_id=str(getattr(ctx.deps, "session_id", None) or "delegate"),
                authority=authority,
                parent_task_id=parent_task.task_id if parent_task else None,
                repeated_failure_limit=get_delegate_repeated_failure_limit(),
                timeout_seconds=timeout_seconds,
                mode=mode,
            )
            runtime = get_runtime_context()
            completed_result: dict[str, ToolReturn] = {}
            concurrency_limit = get_max_concurrent_delegates()
            cancellation_published = False
            cancellation_lock = asyncio.Lock()

            async def _publish_cancelled(
                task_id: str,
                progress: AgentRunProgress | None = None,
            ) -> None:
                nonlocal cancellation_published
                async with cancellation_lock:
                    if cancellation_published:
                        return
                    cancellation_progress = progress or AgentRunProgress()
                    await runtime.task_coordinator.record_result(
                        task_id,
                        _cancelled_delegate_result(
                            model=spec.model or "default",
                            tool_names=spec.tool_names,
                            stripped_tools=spec.stripped_tools,
                            thinking=thinking_value_to_label(spec.resolved_thinking),
                            repeated_failure_limit=spec.repeated_failure_limit,
                            timeout_seconds=spec.timeout_seconds,
                            progress=cancellation_progress,
                        ),
                    )
                    _log_delegate_cancelled(
                        session_id=spec.session_id,
                        task_id=task_id,
                        parent_task_id=spec.parent_task_id,
                        mode=spec.mode,
                        model=spec.model or "default",
                        tool_names=spec.tool_names,
                        repeated_failure_limit=spec.repeated_failure_limit,
                        timeout_seconds=spec.timeout_seconds,
                        progress=cancellation_progress,
                    )
                    cancellation_published = True

            async def _on_queued(
                task: ExecutionTaskSnapshot,
                wait: ExecutionConcurrencyWait,
            ) -> None:
                logger.add_sink("validation").info(
                    "delegate_concurrency_queued",
                    data={
                        "event": "delegate_concurrency_queued",
                        "task_id": task.task_id,
                        "parent_task_id": task.parent_task_id,
                        "queue_position": wait.queue_position,
                        "limit": concurrency_limit,
                    },
                )

            async def _run(task: ExecutionTaskSnapshot) -> ExecutionTaskRunOutcome:
                progress = AgentRunProgress()

                async def _execute() -> ExecutionTaskRunOutcome:
                    result = await _execute_delegate(spec, task, progress)
                    completed_result["value"] = result
                    return _delegate_execution_outcome(result)

                try:
                    outcome = await runtime.task_runner.run_with_concurrency(
                        task,
                        ExecutionConcurrencyPolicy(
                            key="delegate",
                            limit=concurrency_limit,
                            queued_status="queued_for_delegate_slot",
                            queued_metadata={
                                "queue_reason": "delegate_concurrency_limit"
                            },
                            clear_metadata={
                                "queue_reason": None,
                                "queue_position": None,
                                "active_task_ids": None,
                            },
                            on_queued=_on_queued,
                        ),
                        _execute,
                    )
                except asyncio.CancelledError:
                    await _finish_cancellation_cleanup(
                        _publish_cancelled(task.task_id, progress)
                    )
                    raise
                if not isinstance(outcome, ExecutionTaskRunOutcome):
                    raise RuntimeError("Delegate concurrency lane lost its run outcome")
                return outcome

            child_task = await runtime.task_runner.start_background(
                ExecutionTaskSpec(
                    kind=ExecutionTaskKind.DELEGATE,
                    scope=f"delegate_parent:{spec.parent_task_id or spec.session_id}",
                    source=ExecutionTaskSource.TOOL,
                    label=f"delegate:{spec.session_id}",
                    authority=spec.authority,
                    metadata={
                        "session_id": spec.session_id,
                        "model": spec.model or "default",
                        "mode": spec.mode,
                        "tool_names": list(spec.tool_names),
                    },
                    parent_task_id=spec.parent_task_id,
                ),
                _run,
                hooks=ExecutionTaskHooks(on_cancelled=_publish_cancelled),
                start_immediately=False,
            )
            handle = {
                "job_id": child_task.task_id,
                "kind": child_task.kind,
                "status": child_task.status,
                "mode": "managed",
                "process_local": True,
            }
            if mode == "managed":
                return ToolReturn(return_value=handle, content=None, metadata=handle)

            try:
                terminal = await runtime.task_coordinator.wait_for_tasks(
                    [child_task.task_id],
                    timeout_seconds=None,
                    terminal_or_attention_only=True,
                    wake_on_attention=False,
                )
            except asyncio.CancelledError:
                await runtime.task_coordinator.cancel_task(
                    child_task.task_id,
                    reason="delegate_parent_cancelled",
                )
                try:
                    await asyncio.wait_for(
                        asyncio.shield(
                            runtime.task_coordinator.wait_for_tasks(
                                [child_task.task_id],
                                timeout_seconds=5.0,
                                terminal_or_attention_only=True,
                                wake_on_attention=False,
                            )
                        ),
                        timeout=5.0,
                    )
                except TimeoutError:
                    pass
                raise

            direct_result = completed_result.get("value")
            if direct_result is not None:
                return direct_result
            snapshot = terminal.snapshots[0] if terminal.snapshots else None
            if snapshot is not None and snapshot.result is not None:
                return _delegate_result_to_tool_return(snapshot.result)
            raise RuntimeError(
                f"Delegate execution task ended without a result: {child_task.task_id}"
            )

        return Tool(
            delegate,
            takes_ctx=True,
            name="delegate",
            description=(
                "Run a focused child agent over a prompt with optional tools. "
                "Use managed mode for independent long-running work."
            ),
        )


def _delegate_execution_outcome(result: ToolReturn) -> ExecutionTaskRunOutcome:
    metadata = result.metadata if isinstance(result.metadata, dict) else {}
    status = str(metadata.get("status") or "completed")
    failure_kind = str(metadata.get("failure_kind") or "") or None
    if status != "failed":
        terminal_status = ExecutionTaskStatus.COMPLETED
    elif failure_kind == "delegate_timeout":
        terminal_status = ExecutionTaskStatus.TIMED_OUT
    else:
        terminal_status = ExecutionTaskStatus.FAILED
    references = metadata.get("handoff_references")
    payload: dict[str, Any] = {
        "return_value": result.return_value,
        "content": result.content,
        "metadata": metadata,
    }
    if isinstance(references, list):
        payload["artifact_references"] = references
    return ExecutionTaskRunOutcome(
        value=payload,
        status=terminal_status,
        reason=failure_kind,
    )


def _delegate_result_to_tool_return(result: dict[str, Any]) -> ToolReturn:
    return ToolReturn(
        return_value=result.get("return_value", "Delegate result was truncated."),
        content=result.get("content"),
        metadata=result.get("metadata"),
    )


def _cancelled_delegate_result(
    *,
    model: str,
    tool_names: tuple[str, ...],
    stripped_tools: tuple[str, ...],
    thinking: str,
    repeated_failure_limit: int,
    timeout_seconds: float,
    progress: AgentRunProgress,
) -> dict[str, Any]:
    audit = _build_child_run_audit(progress.messages)
    references = _child_run_references(progress.messages)
    metadata: dict[str, Any] = {
        "status": "cancelled",
        "model": model,
        "tool_names": list(tool_names),
        "thinking": thinking,
        "repeated_failure_limit": repeated_failure_limit,
        "timeout_seconds": timeout_seconds,
        "audit": audit,
        "usage": _delegate_usage_metadata(progress.usage),
        "partial_output": _partial_delegate_output(progress.output),
        "handoff_references": references,
    }
    if stripped_tools:
        metadata["stripped_tools"] = list(stripped_tools)
    return {
        "return_value": "Delegate cancelled.",
        "content": None,
        "metadata": metadata,
        "artifact_references": references,
    }


async def _collect_delegate_response(
    *,
    agent: Any,
    prompt: str,
    usage_limits: UsageLimits | None,
    allow_retry: bool,
    session_id: str,
    model: str,
    progress: AgentRunProgress,
    event_stream_handler: Any | None = None,
) -> Any:
    """Collect a child run, retrying only when replay cannot duplicate tools."""
    retry_policy = ModelStreamRetryPolicy.from_settings()
    for attempt in range(1, retry_policy.max_attempts + 1):
        try:
            return await collect_response(
                agent,
                prompt,
                usage_limits=usage_limits,
                usage=progress.usage,
                progress=progress,
                event_stream_handler=event_stream_handler,
            )
        except Exception as exc:
            classification = classify_exception(exc, phase="delegate_child_run")
            can_retry = (
                allow_retry
                and classification.retryable
                and retry_policy.can_retry_after(attempt)
            )
            if not can_retry:
                raise
            delay_seconds = retry_policy.delay_after(attempt)
            logger.add_sink("validation").warning(
                "delegate_retry_scheduled",
                data={
                    "event": "delegate_retry_scheduled",
                    "workflow_id": session_id,
                    "model": model,
                    "attempt": attempt,
                    "next_attempt": attempt + 1,
                    "max_attempts": retry_policy.max_attempts,
                    "delay_seconds": delay_seconds,
                    "failure_kind": classification.failure_kind,
                    "error_type": classification.error_type,
                    "replay_scope": "no_child_tools",
                },
            )
            await asyncio.sleep(delay_seconds)
    raise AssertionError("Delegate retry loop exhausted without returning or raising")


def _failed_delegate_return(
    *,
    session_id: str,
    task_id: str,
    parent_task_id: str | None,
    mode: str,
    model: str,
    tool_names: tuple[str, ...],
    stripped_tools: tuple[str, ...],
    thinking: str,
    timeout_seconds: float,
    repeated_failure_limit: int,
    progress: AgentRunProgress,
    classification: FailureClassification,
    message: str,
) -> ToolReturn:
    audit = _build_child_run_audit(progress.messages)
    references = _child_run_references(progress.messages)
    partial_output = _partial_delegate_output(progress.output)
    handoff_message = _delegate_failure_handoff_message(
        message,
        partial_output,
        unsettled_tool_call_count=audit["unsettled_tool_call_count"],
    )
    usage = _delegate_usage_metadata(progress.usage)
    metadata: dict[str, Any] = {
        "status": "failed",
        "model": model,
        "tool_names": list(tool_names),
        "thinking": thinking,
        "output_chars": len(handoff_message),
        "repeated_failure_limit": repeated_failure_limit,
        "timeout_seconds": timeout_seconds,
        "audit": audit,
        "usage": usage,
        "partial_output": partial_output,
        "handoff_references": references,
    }
    metadata.update(classification.to_metadata())
    if stripped_tools:
        metadata["stripped_tools"] = list(stripped_tools)

    log_data = {
        "workflow_id": session_id,
        "task_id": task_id,
        "parent_task_id": parent_task_id,
        "mode": mode,
        "model": model,
        "tool_names": list(tool_names),
        "error_type": classification.error_type,
        "failure_kind": classification.failure_kind,
        "retryable": classification.retryable,
        "error_message": message,
        "repeated_failure_limit": repeated_failure_limit,
        "timeout_seconds": timeout_seconds,
        "suggested_action": classification.suggested_action,
        "partial_message_count": audit["message_count"],
        "partial_tool_call_count": audit["tool_call_count"],
        "unsettled_tool_call_count": audit["unsettled_tool_call_count"],
        "partial_output_chars": len(partial_output),
        "handoff_reference_count": len(references),
        **usage,
    }
    for key in ("limit_kind", "limit_setting", "limit"):
        if key in metadata:
            log_data[key] = metadata[key]

    logger.add_sink("validation").error(
        "delegate_failed",
        data=log_data,
    )
    return ToolReturn(return_value=handoff_message, content=None, metadata=metadata)


def _delegate_runtime_failure_classification(
    exc: Exception,
) -> FailureClassification:
    classification = classify_exception(exc, phase="delegate_child_run")
    if classification.failure_kind != "unknown":
        return classification
    return FailureClassification(
        error_type=type(exc).__name__,
        failure_kind="delegate_internal",
        retryable=False,
        phase="delegate_child_run",
        message=str(exc),
        suggested_action=(
            "Inspect the delegate failure log and correct the model, tool binding, "
            "or child-run configuration before retrying."
        ),
    )


def _log_delegate_cancelled(
    *,
    session_id: str,
    task_id: str,
    parent_task_id: str | None,
    mode: str,
    model: str,
    tool_names: tuple[str, ...],
    repeated_failure_limit: int,
    timeout_seconds: float,
    progress: AgentRunProgress,
) -> None:
    audit = _build_child_run_audit(progress.messages)
    usage = _delegate_usage_metadata(progress.usage)
    logger.add_sink("validation").info(
        "delegate_cancelled",
        data={
            "workflow_id": session_id,
            "task_id": task_id,
            "parent_task_id": parent_task_id,
            "mode": mode,
            "model": model,
            "tool_names": list(tool_names),
            "repeated_failure_limit": repeated_failure_limit,
            "timeout_seconds": timeout_seconds,
            "partial_message_count": audit["message_count"],
            "partial_tool_call_count": audit["tool_call_count"],
            "unsettled_tool_call_count": audit["unsettled_tool_call_count"],
            "partial_output_chars": len(_partial_delegate_output(progress.output)),
            **usage,
        },
    )


def _delegate_usage_limit_context(exc: UsageLimitExceeded) -> dict[str, Any]:
    """Return model-visible details for a delegate child usage-limit failure."""
    del exc
    limit = get_delegate_model_requests_limit()
    limit_label = f" of {limit}" if limit > 0 else ""
    return {
        "limit_kind": "model_requests",
        "limit_setting": "delegate_model_requests_limit",
        "limit": limit,
        "suggested_action": (
            "Do not retry the same broad delegation. Split the work into smaller child runs, "
            "ask each child to return a compact summary or saved artifact path, and checkpoint "
            "progress with goal_ops before continuing."
        ),
        "message": (
            f"Delegate stopped because the child agent reached its model-request limit{limit_label}. "
            "Do not retry the same broad delegation unchanged. Split the work into smaller delegate calls "
            "scoped by path, query, source group, or hypothesis; have each child return a compact summary "
            "or saved artifact path; and checkpoint progress with goal_ops before continuing."
        ),
    }


def _build_child_run_audit(messages: Sequence[ModelMessage]) -> dict[str, Any]:
    tool_calls_by_id: dict[str, dict[str, Any]] = {}
    all_tool_call_ids: set[str] = set()
    settled_tool_call_ids: set[str] = set()
    total_tool_call_count = 0
    tool_calls: list[dict[str, Any]] = []
    response_count = 0
    request_count = 0

    for message in messages:
        if isinstance(message, ModelRequest):
            request_count += 1
        elif isinstance(message, ModelResponse):
            response_count += 1

        for part in getattr(message, "parts", ()) or ():
            if isinstance(part, ToolCallPart):
                total_tool_call_count += 1
                all_tool_call_ids.add(part.tool_call_id)
                call: dict[str, Any] = {
                    "tool": part.tool_name,
                    "call_id": part.tool_call_id,
                    "settled": False,
                    "arguments": _delegate_argument_hint(part.args),
                }
                if len(tool_calls) < DELEGATE_AUDIT_MAX_TOOL_CALLS:
                    tool_calls.append(call)
                    tool_calls_by_id[part.tool_call_id] = call
            elif isinstance(part, ToolReturnPart):
                if part.tool_call_id in all_tool_call_ids:
                    settled_tool_call_ids.add(part.tool_call_id)
                returned_call = tool_calls_by_id.get(part.tool_call_id)
                if returned_call is None:
                    continue
                returned_call["outcome"] = part.outcome
                returned_call["settled"] = True
                returned_call["terminal_state"] = classify_tool_result_state(
                    outcome=part.outcome,
                    metadata=part.metadata,
                )
                returned_call["result"] = _compact_value(
                    part.content,
                    max_chars=DELEGATE_AUDIT_MAX_RESULT_CHARS,
                )
                if isinstance(part.metadata, dict):
                    returned_call["metadata"] = _compact_mapping(part.metadata)
                    returned_call["structured_state"] = bool(
                        part.metadata.get("status") or part.metadata.get("state")
                    )

    tool_error_count = sum(
        1
        for call in tool_calls
        if call.get("terminal_state") == "failed"
        or (
            not call.get("structured_state")
            and call.get("outcome") in {None, "success"}
            and _looks_like_tool_error(str(call.get("result") or ""))
        )
    )
    settled_tool_call_count = len(settled_tool_call_ids)
    return {
        "message_count": len(messages),
        "request_count": request_count,
        "response_count": response_count,
        "tool_call_count": total_tool_call_count,
        "settled_tool_call_count": settled_tool_call_count,
        "unsettled_tool_call_count": total_tool_call_count - settled_tool_call_count,
        "tool_error_count": tool_error_count,
        "tool_calls_truncated": total_tool_call_count > len(tool_calls),
        "tool_calls": tool_calls,
    }


def _compact_mapping(value: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in (
        "status",
        "operation",
        "path",
        "media_type",
        "media_mode",
        "size_bytes",
        "error_type",
        "failure_kind",
        "artifact_ref",
        "cache_ref",
        "ref",
    ):
        if key in value:
            compact[key] = _compact_value(value[key], max_chars=200)
    return compact


def _partial_delegate_output(output: Any) -> str:
    if output is None:
        return ""
    return _compact_value(
        coerce_output_data(output),
        max_chars=_DELEGATE_PARTIAL_OUTPUT_MAX_CHARS,
    )


def _delegate_failure_handoff_message(
    message: str,
    partial_output: str,
    *,
    unsettled_tool_call_count: int,
) -> str:
    sections = [message]
    if unsettled_tool_call_count:
        sections.append(
            f"Caution: {unsettled_tool_call_count} child tool call(s) had no settled return. "
            "Do not replay a possible mutation blindly; inspect durable state first."
        )
    if partial_output:
        sections.append(f"Partial child handoff:\n{partial_output}")
    return "\n\n".join(sections)


def _delegate_usage_metadata(usage: RunUsage) -> dict[str, int]:
    return {
        "request_count": usage.requests,
        "tool_call_count": usage.tool_calls,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
    }


def _child_run_references(messages: Sequence[ModelMessage]) -> list[str]:
    references: list[str] = []
    for message in messages:
        for part in getattr(message, "parts", ()) or ():
            if not isinstance(part, ToolReturnPart):
                continue
            _collect_handoff_references(part.metadata, references)
            _collect_handoff_references(part.content, references)
            if len(references) >= _DELEGATE_MAX_HANDOFF_REFERENCES:
                return references
    return references


def _collect_handoff_references(value: Any, references: list[str]) -> None:
    pending: list[tuple[str | None, Any]] = [(None, value)]
    visited_container_ids: set[int] = set()
    visited_nodes = 0
    while (
        pending
        and len(references) < _DELEGATE_MAX_HANDOFF_REFERENCES
        and visited_nodes < _DELEGATE_MAX_HANDOFF_REFERENCE_NODES
    ):
        key, item = pending.pop()
        visited_nodes += 1
        if key in {"artifact_ref", "cache_ref", "ref"} and isinstance(item, str):
            if item and item not in references:
                references.append(item)
            continue
        if isinstance(item, dict):
            container_id = id(item)
            if container_id in visited_container_ids:
                continue
            visited_container_ids.add(container_id)
            pending.extend(
                (str(child_key).strip().lower(), child)
                for child_key, child in reversed(tuple(item.items()))
            )
        elif isinstance(item, list | tuple):
            container_id = id(item)
            if container_id in visited_container_ids:
                continue
            visited_container_ids.add(container_id)
            pending.extend((None, child) for child in reversed(item))
        elif isinstance(item, str) and item.lstrip().startswith(("{", "[")):
            try:
                pending.append((None, json.loads(item)))
            except (TypeError, ValueError):
                continue


def _compact_value(value: Any, *, max_chars: int) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            text = str(value)
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...[truncated {len(text) - max_chars} chars]"


def _delegate_argument_hint(value: Any) -> str:
    """Expose useful routing hints without retaining arbitrary tool payloads."""
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return "<unstructured arguments>"
    if not isinstance(parsed, dict):
        return f"<arguments type={type(parsed).__name__}>"

    hint: dict[str, Any] = {}
    for key, item in parsed.items():
        normalized_key = str(key).strip().lower()
        if normalized_key not in _DELEGATE_VISIBLE_ARGUMENT_KEYS:
            continue
        hint[str(key)] = (
            _sanitize_url_argument_hint(item)
            if normalized_key in {"url", "urls"}
            else item
        )
    hidden_keys = sorted(
        str(key)
        for key in parsed
        if str(key).strip().lower() not in _DELEGATE_VISIBLE_ARGUMENT_KEYS
    )
    if hidden_keys:
        hint["other_keys"] = hidden_keys
    return _compact_value(hint, max_chars=DELEGATE_AUDIT_MAX_ARGUMENT_CHARS)


def _sanitize_url_argument_hint(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_url_for_log(value)
    if isinstance(value, dict):
        return {
            str(key): _sanitize_url_argument_hint(item) for key, item in value.items()
        }
    if isinstance(value, list | tuple | set):
        return [_sanitize_url_argument_hint(item) for item in value]
    return f"<{type(value).__name__}>"


def _looks_like_tool_error(text: str) -> bool:
    lowered = text.strip().lower()
    if lowered.startswith(("error:", "error ", "failed:", "failure:")):
        return True
    return any(
        marker in lowered
        for marker in (
            '"error":',
            "cannot ",
            "not found",
            "unsupported",
            "permission denied",
            "exceeded",
            "timeout",
        )
    )


def _parse_tool_names(tools: Any) -> tuple[str, ...]:
    if tools is None:
        return ()
    if isinstance(tools, list | tuple):
        result: list[str] = []
        for item in tools:
            if not isinstance(item, str):
                raise ValueError("delegate tools entries must be strings")
            name = item.strip()
            if name:
                result.append(name)
        return tuple(result)
    raise ValueError("delegate tools must be a list or tuple of strings when provided")


def _parse_options(options: dict[str, Any]) -> tuple[object, float]:
    unknown = sorted(set(options) - _SUPPORTED_OPTION_KEYS)
    if unknown:
        raise ValueError(f"Unsupported delegate options: {', '.join(unknown)}")

    if "thinking" not in options:
        requested_thinking: object = _THINKING_UNSET
    else:
        requested_thinking = normalize_thinking_value(
            options["thinking"], source_name="delegate option 'thinking'"
        )

    return requested_thinking, get_delegate_timeout_seconds()


def _delegate_usage_limits() -> UsageLimits:
    model_requests_limit = get_delegate_model_requests_limit()
    return UsageLimits(
        request_limit=model_requests_limit if model_requests_limit > 0 else None,
        tool_calls_limit=None,
    )


def _apply_delegate_instruction_layers(
    agent: Any,
    *,
    caller_instructions: str | None,
) -> None:
    """Register delegate layers in the same base-before-specific order as chat."""
    agent.instructions(DELEGATE_FLIGHT_CARD.strip())
    if caller_instructions:
        agent.instructions(caller_instructions)


def _delegate_wait_timeout(timeout_seconds: float) -> float | None:
    if timeout_seconds <= 0:
        return None
    return timeout_seconds
