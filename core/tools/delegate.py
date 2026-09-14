"""Delegate tool - run a bounded child agent and return its output."""

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic_ai import RunContext
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
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
from core.runtime.task_runner import ExecutionTaskRunOutcome, ExecutionTaskSpec
from core.settings import (
    get_default_model_thinking,
    get_delegate_model_requests_limit,
    get_delegate_repeated_failure_limit,
    get_delegate_timeout_seconds,
    get_delegate_tool_calls_limit,
)
from core.tools.base import BaseTool
from core.tools.failures import (
    FailureClassification,
    classify_exception,
    classify_tool_result_state,
)

logger = UnifiedLogger(tag="delegate-tool")

_FORBIDDEN_CHILD_TOOLS = frozenset({"delegate", "code_execution", "job"})
_SUPPORTED_OPTION_KEYS = frozenset({"thinking"})
_DELEGATE_PARTIAL_OUTPUT_MAX_CHARS = 4_000
_DELEGATE_MAX_HANDOFF_REFERENCES = 20
_DELEGATE_MAX_HANDOFF_REFERENCE_NODES = 1_000


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
    max_tool_calls: int
    repeated_failure_limit: int
    timeout_seconds: float
    mode: Literal["blocking", "managed"]


class DelegateTool(BaseTool):
    """Run a bounded child agent over a prompt with optional tools."""

    @classmethod
    def get_tool(cls, vault_path: str | None = None) -> Tool:
        _vault_path = vault_path or ""

        async def _execute_delegate(
            spec: DelegateLaunchSpec,
            task: ExecutionTaskSnapshot,
        ) -> ToolReturn:
            session_id = spec.session_id
            prompt = spec.prompt
            model_value = spec.model
            safe_tool_names = spec.tool_names
            stripped = spec.stripped_tools
            resolved_thinking = spec.resolved_thinking
            thinking_source = spec.thinking_source
            max_tool_calls = spec.max_tool_calls
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
                    "max_tool_calls": max_tool_calls,
                    "repeated_failure_limit": repeated_failure_limit,
                    "timeout_seconds": timeout_seconds,
                },
            )

            progress = AgentRunProgress()

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
                    max_tool_calls=max_tool_calls,
                    caller_instructions=spec.instructions,
                )

                usage_limits = _delegate_usage_limits(max_tool_calls)
                result = await asyncio.wait_for(
                    _collect_delegate_response(
                        agent=agent,
                        prompt=prompt,
                        usage_limits=usage_limits,
                        allow_retry=not safe_tool_names,
                        session_id=session_id,
                        model=model_value or "default",
                        progress=progress,
                    ),
                    timeout=_delegate_wait_timeout(timeout_seconds),
                )
                output = result.output
                text = coerce_output_data(output)
                audit = _build_child_run_audit(result.messages)
            except asyncio.CancelledError:
                _log_delegate_cancelled(
                    session_id=session_id,
                    model=model_value or "default",
                    tool_names=safe_tool_names,
                    max_tool_calls=max_tool_calls,
                    repeated_failure_limit=repeated_failure_limit,
                    timeout_seconds=timeout_seconds,
                    progress=progress,
                )
                raise
            except UsageLimitExceeded as exc:
                limit_context = _delegate_usage_limit_context(
                    exc, max_tool_calls=max_tool_calls
                )
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
                    model=model_value or "default",
                    tool_names=safe_tool_names,
                    stripped_tools=stripped,
                    thinking=thinking_value_to_label(resolved_thinking),
                    max_tool_calls=max_tool_calls,
                    timeout_seconds=timeout_seconds,
                    repeated_failure_limit=repeated_failure_limit,
                    progress=progress,
                    classification=classification,
                    message=limit_context["message"],
                )
            except TimeoutError as exc:
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
                    model=model_value or "default",
                    tool_names=safe_tool_names,
                    stripped_tools=stripped,
                    thinking=thinking_value_to_label(resolved_thinking),
                    max_tool_calls=max_tool_calls,
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
                classification = classify_exception(exc, phase="delegate_child_run")
                if classification.failure_kind == "unknown":
                    classification = FailureClassification(
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
                return _failed_delegate_return(
                    session_id=session_id,
                    model=model_value or "default",
                    tool_names=safe_tool_names,
                    stripped_tools=stripped,
                    thinking=thinking_value_to_label(resolved_thinking),
                    max_tool_calls=max_tool_calls,
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
                "max_tool_calls": max_tool_calls,
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
                    "max_tool_calls": max_tool_calls,
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
            requested_tools = _parse_tool_names(tools)
            safe_tool_names = tuple(
                name for name in requested_tools if name not in _FORBIDDEN_CHILD_TOOLS
            )
            stripped_tools = tuple(sorted(set(requested_tools) - set(safe_tool_names)))
            requested_thinking, max_tool_calls, timeout_seconds = _parse_options(
                options or {}
            )
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
                max_tool_calls=max_tool_calls,
                repeated_failure_limit=get_delegate_repeated_failure_limit(),
                timeout_seconds=timeout_seconds,
                mode=mode,
            )
            runtime = get_runtime_context()
            completed_result: dict[str, ToolReturn] = {}

            async def _run(task: ExecutionTaskSnapshot) -> ExecutionTaskRunOutcome:
                result = await _execute_delegate(spec, task)
                completed_result["value"] = result
                return _delegate_execution_outcome(result)

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


async def _collect_delegate_response(
    *,
    agent: Any,
    prompt: str,
    usage_limits: UsageLimits | None,
    allow_retry: bool,
    session_id: str,
    model: str,
    progress: AgentRunProgress,
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
    model: str,
    tool_names: tuple[str, ...],
    stripped_tools: tuple[str, ...],
    thinking: str,
    max_tool_calls: int,
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
        "max_tool_calls": max_tool_calls,
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
        "model": model,
        "tool_names": list(tool_names),
        "error_type": classification.error_type,
        "failure_kind": classification.failure_kind,
        "retryable": classification.retryable,
        "error_message": message,
        "max_tool_calls": max_tool_calls,
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


def _log_delegate_cancelled(
    *,
    session_id: str,
    model: str,
    tool_names: tuple[str, ...],
    max_tool_calls: int,
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
            "model": model,
            "tool_names": list(tool_names),
            "max_tool_calls": max_tool_calls,
            "repeated_failure_limit": repeated_failure_limit,
            "timeout_seconds": timeout_seconds,
            "partial_message_count": audit["message_count"],
            "partial_tool_call_count": audit["tool_call_count"],
            "unsettled_tool_call_count": audit["unsettled_tool_call_count"],
            "partial_output_chars": len(_partial_delegate_output(progress.output)),
            **usage,
        },
    )


def _delegate_usage_limit_context(
    exc: UsageLimitExceeded, *, max_tool_calls: int
) -> dict[str, Any]:
    """Return model-visible details for a delegate child usage-limit failure."""
    error_text = str(exc)
    if "request_limit" in error_text:
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

    limit = max_tool_calls
    limit_label = f" of {limit}" if limit > 0 else ""
    return {
        "limit_kind": "tool_calls",
        "limit_setting": "delegate_tool_calls_limit",
        "limit": limit,
        "suggested_action": (
            "Do not retry the same broad delegation. Split the work into smaller child runs, use direct "
            "deterministic tools for simple retrieval, and checkpoint progress with goal_ops before continuing."
        ),
        "message": (
            f"Delegate stopped because the child agent exceeded its tool-call limit{limit_label}. "
            "Do not retry the same broad delegation unchanged. Split the work into smaller delegate calls scoped "
            "by path, query, source group, or hypothesis; use direct deterministic tools for simple retrieval; "
            "have each child return a compact summary or saved artifact path; and checkpoint progress with "
            "goal_ops before continuing."
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
                    "arguments": _compact_value(
                        part.args,
                        max_chars=DELEGATE_AUDIT_MAX_ARGUMENT_CHARS,
                    ),
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


def _parse_options(options: dict[str, Any]) -> tuple[object, int, float]:
    unknown = sorted(set(options) - _SUPPORTED_OPTION_KEYS)
    if unknown:
        raise ValueError(f"Unsupported delegate options: {', '.join(unknown)}")

    if "thinking" not in options:
        requested_thinking: object = _THINKING_UNSET
    else:
        requested_thinking = normalize_thinking_value(
            options["thinking"], source_name="delegate option 'thinking'"
        )

    return (
        requested_thinking,
        get_delegate_tool_calls_limit(),
        get_delegate_timeout_seconds(),
    )


def _delegate_usage_limits(max_tool_calls: int) -> UsageLimits | None:
    model_requests_limit = get_delegate_model_requests_limit()
    return UsageLimits(
        request_limit=model_requests_limit if model_requests_limit > 0 else None,
        tool_calls_limit=max_tool_calls if max_tool_calls > 0 else None,
    )


def _delegate_flight_card(max_tool_calls: int) -> str:
    if max_tool_calls > 0:
        budget_instruction = (
            f"This run has a maximum of {max_tool_calls} total tool calls. "
            "Finish tool use early enough to synthesize the compact handoff."
        )
    else:
        budget_instruction = (
            "The configured tool-call limit is disabled for this run. "
            "Keep tool use bounded to the smallest set needed for the deliverable."
        )
    return f"{DELEGATE_FLIGHT_CARD.strip()}\n- {budget_instruction}"


def _apply_delegate_instruction_layers(
    agent: Any,
    *,
    max_tool_calls: int,
    caller_instructions: str | None,
) -> None:
    """Register delegate layers in the same base-before-specific order as chat."""
    agent.instructions(_delegate_flight_card(max_tool_calls))
    if caller_instructions:
        agent.instructions(caller_instructions)


def _delegate_wait_timeout(timeout_seconds: float) -> float | None:
    if timeout_seconds <= 0:
        return None
    return timeout_seconds
