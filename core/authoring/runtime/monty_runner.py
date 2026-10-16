"""Minimal Monty runtime wrapper for the Monty-backed authoring surface."""

from __future__ import annotations

import keyword
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic_monty import AsyncMonty

from core.authoring.contracts import (
    AuthoringExecutionContext,
    AuthoringFinishSignal,
    AuthoringHost,
    AuthoringToolCallError,
    ScriptToolResult,
)
from core.authoring.helper_catalog import create_builtin_registry
from core.authoring.helpers.runtime_common import (
    coerce_tool_return_value_text,
    invoke_bound_tool,
    normalize_tool_result,
)
from core.authoring.registry import (
    AuthoringCapabilityRegistry,
    reject_retired_helper_dependencies,
)
from core.authoring.shared.tool_binding import resolve_tool_binding
from core.logger import UnifiedLogger
from core.settings.store import get_enabled_tools_config

logger = UnifiedLogger(tag="authoring-monty")

_STUBS_PATH = Path(__file__).parent.parent / "stubs.pyi"
_INVALID_IDENTIFIER_CHARS = re.compile(r"[^a-zA-Z0-9_]")
_EXCLUDED_DIRECT_TOOL_NAMES = frozenset({"code_execution"})


class AuthoringMontyExecutionError(RuntimeError):
    """Raised when Monty-backed authoring execution fails."""


@dataclass(frozen=True)
class AuthoringMontyExecutionResult:
    """Structured result from one Monty-backed authoring execution."""

    value: Any
    status: str = "completed"
    reason: str = ""
    prints: tuple[str, ...] = ()


@dataclass(frozen=True)
class _PrintCapture:
    lines: list[str] = field(default_factory=list)

    def callback(self, _channel: str, text: str) -> None:
        self.lines.append(text)


async def run_authoring_monty(
    *,
    workflow_id: str,
    code: str,
    host: AuthoringHost,
    inputs: dict[str, Any] | None = None,
    script_name: str = "main.py",
    type_check: bool = True,
    registry: AuthoringCapabilityRegistry | None = None,
) -> AuthoringMontyExecutionResult:
    """Execute one experimental authoring artifact with Monty."""
    reject_retired_helper_dependencies(code)
    runtime_registry = registry or create_builtin_registry()
    context = AuthoringExecutionContext(
        workflow_id=workflow_id,
        host=host,
    )
    external_functions = runtime_registry.build_external_functions(
        context=context,
    )
    direct_tool_functions, direct_tool_stubs = _build_direct_tool_functions(
        context=context,
    )
    external_functions.update(direct_tool_functions)
    reserved_inputs = dict(host.get_monty_inputs())
    effective_inputs = {**reserved_inputs, **(inputs or {})}
    capture = _PrintCapture()

    logger.set_sinks(["validation"]).info(
        "authoring_monty_execution_started",
        data={
            "workflow_id": workflow_id,
            "script_name": script_name,
            "type_check": type_check,
        },
    )

    terminal_status = "completed"
    terminal_reason = ""
    value: Any = None
    try:
        try:
            async with AsyncMonty() as pool:
                async with pool.checkout(
                    script_name=script_name,
                    type_check=type_check,
                    type_check_stubs=(
                        _build_type_check_stubs(direct_tool_stubs)
                        if type_check
                        else None
                    ),
                    dataclass_registry=list(host.get_monty_dataclasses()),
                ) as session:
                    value = await session.feed_run(
                        code,
                        inputs=effective_inputs or None,
                        external_lookup=external_functions,
                        print_callback=capture.callback,
                    )
        except Exception as exc:
            parsed_finish = _extract_finish_signal(exc)
            if parsed_finish is not None:
                terminal_status, terminal_reason = parsed_finish
            else:
                raise
    except Exception as exc:
        logger.set_sinks(["validation"]).error(
            "authoring_monty_execution_failed",
            data={
                "workflow_id": workflow_id,
                "script_name": script_name,
                "error_message": str(exc),
                "error_type": type(exc).__name__,
            },
        )
        raise AuthoringMontyExecutionError(
            f"Monty execution failed for '{workflow_id}': {exc}"
        ) from exc

    logger.set_sinks(["validation"]).info(
        "authoring_monty_execution_completed",
        data={
            "workflow_id": workflow_id,
            "script_name": script_name,
            "status": terminal_status,
            "reason": terminal_reason,
            "printed_line_count": len(capture.lines),
        },
    )
    return AuthoringMontyExecutionResult(
        value=value,
        status=terminal_status,
        reason=terminal_reason,
        prints=tuple(capture.lines),
    )


def _build_type_check_stubs(direct_tool_stubs: str) -> str:
    base = _STUBS_PATH.read_text(encoding="utf-8")
    if not direct_tool_stubs:
        return base
    return f"{base}\n\n# Direct tool callables\n{direct_tool_stubs}"


def _build_direct_tool_functions(
    *,
    context: AuthoringExecutionContext,
) -> tuple[dict[str, Any], str]:
    host = context.host
    tool_names = [
        name
        for name in sorted(get_enabled_tools_config())
        if name not in _EXCLUDED_DIRECT_TOOL_NAMES
    ]
    if not tool_names:
        return {}, ""

    binding = resolve_tool_binding(
        tool_names,
        vault_path=host.vault_path or "",
        week_start_day=host.week_start_day,
    )
    external_functions: dict[str, Any] = {}
    stub_lines: list[str] = []
    used_names: set[str] = set()

    for spec in binding.tool_specs:
        function_name = _sanitize_tool_name(spec.name)
        if function_name in used_names:
            logger.warning(
                "Direct tool skipped due to Monty name collision",
                data={"tool": spec.name, "function_name": function_name},
            )
            continue
        used_names.add(function_name)

        async def _direct_tool_function(
            *args: Any,
            _spec: Any = spec,
            **kwargs: Any,
        ) -> Any:
            if args:
                raise ValueError(f"{_spec.name} only supports keyword arguments")
            logger.set_sinks(["validation"]).info(
                "authoring_direct_tool_started",
                data={
                    "workflow_id": context.workflow_id,
                    "tool": _spec.name,
                    "argument_keys": sorted(kwargs),
                },
            )
            try:
                result = await invoke_bound_tool(
                    _spec.tool_function,
                    tool_name=_spec.name,
                    arguments=dict(kwargs),
                    run_buffers=host.run_buffers,
                    session_buffers=host.session_buffers,
                    session_id=getattr(host, "session_key", None),
                    chat_session_id=getattr(host, "chat_session_id", None),
                    vault_name=(
                        str(context.workflow_id).split("/", 1)[0]
                        if "/" in str(context.workflow_id)
                        else None
                    ),
                    authoring_workflow_id=str(context.workflow_id),
                    message_history=getattr(host, "message_history", None),
                    prefer_message_history=bool(
                        getattr(host, "prefer_message_history", False)
                    ),
                )
                tool_result = normalize_tool_result(
                    _spec.name,
                    result,
                    vault_path=host.vault_path or "",
                )
                _raise_for_failed_tool_result(
                    tool_name=_spec.name,
                    arguments=kwargs,
                    tool_result=tool_result,
                )
            except Exception as exc:
                logger.warning(
                    "authoring_direct_tool_failed",
                    data={
                        "event": "authoring_direct_tool_failed",
                        "workflow_id": context.workflow_id,
                        "tool": _spec.name,
                        "argument_keys": sorted(kwargs),
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )
                raise
            logger.set_sinks(["validation"]).info(
                "authoring_direct_tool_completed",
                data={
                    "workflow_id": context.workflow_id,
                    "tool": _spec.name,
                    "return_value_chars": len(
                        coerce_tool_return_value_text(tool_result.return_value)
                    ),
                    "status": tool_result.metadata.get("status"),
                    "item_count": len(tool_result.items),
                    "has_content": tool_result.content is not None,
                },
            )
            return tool_result

        _direct_tool_function.__name__ = function_name
        _direct_tool_function.__doc__ = f"Direct Monty wrapper for tool '{spec.name}'."
        external_functions[function_name] = _direct_tool_function
        stub_lines.append(
            f"async def {function_name}(**kwargs: Any) -> ScriptToolResult: ..."
        )

    return external_functions, "\n".join(stub_lines)


def _raise_for_failed_tool_result(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    tool_result: ScriptToolResult,
) -> None:
    metadata = dict(tool_result.metadata or {})
    status = str(metadata.get("status") or "").strip().lower()
    if status not in {"error", "failed"}:
        return
    reason = coerce_tool_return_value_text(tool_result.return_value).strip()
    raise AuthoringToolCallError(
        tool_name=tool_name,
        operation=str(metadata.get("operation") or arguments.get("operation") or ""),
        reason=reason or str(metadata.get("error_type") or status),
        metadata=metadata,
    )


def _sanitize_tool_name(name: str) -> str:
    sanitized = _INVALID_IDENTIFIER_CHARS.sub("_", name)
    if sanitized and sanitized[0].isdigit():
        sanitized = f"_{sanitized}"
    if keyword.iskeyword(sanitized):
        sanitized = f"{sanitized}_"
    return sanitized or "_"


def _extract_finish_signal(exc: Exception) -> tuple[str, str] | None:
    direct = AuthoringFinishSignal.try_parse(str(exc))
    if direct is not None:
        return direct
    inner_exception = getattr(exc, "exception", None)
    if callable(inner_exception):
        inner = inner_exception()
        if inner is not None:
            return AuthoringFinishSignal.try_parse(str(inner))
    return None
