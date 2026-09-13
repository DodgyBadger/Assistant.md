"""Shared typed tool binding for workflow authoring surfaces."""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from pydantic_ai import RunContext, Tool
from pydantic_ai.messages import ToolReturn

from core.logger import UnifiedLogger
from core.settings.secrets_store import secret_has_value
from core.settings.store import (
    ToolConfig,
    get_enabled_tool_names,
    get_enabled_tools_config,
)
from core.tools.base import BaseTool, ToolRecoveryPolicy, tool_recovery_metadata
from core.tools.web_security import wrap_web_tool_result
from core.utils.value_parser import DirectiveValueParser
from core.web.config import get_web_tool_strategy_requirements

logger = UnifiedLogger(tag="workflow-tool-binding")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    params: dict[str, str]
    tool_class: type[BaseTool]
    tool_function: Tool
    recovery_policy: ToolRecoveryPolicy
    week_start_day: int = 0


@dataclass(frozen=True)
class ToolBindingResult:
    tool_functions: list[Tool]
    tool_instructions: str
    tool_specs: list[ToolSpec]

    def tool_names(self) -> list[str]:
        return [spec.name for spec in self.tool_specs]


def validate_tool_binding_value(value: Any) -> bool:
    """Validate string/list based tool declarations."""
    try:
        _normalize_tool_value(value, allow_empty=False)
    except Exception:
        return False

    normalized = _normalize_tool_value(value, allow_empty=False)
    if DirectiveValueParser.is_empty(normalized):
        return False

    lowered = DirectiveValueParser.normalize_string(normalized, to_lower=True)
    if lowered in ["true", "false", "yes", "no", "1", "0", "on", "off", "all", "none"]:
        return True

    items = _parse_tools(normalized)
    if not items:
        return False
    available_tools = set(get_enabled_tool_names())
    return all(item[0] in available_tools for item in items)


def resolve_tool_binding(
    value: Any,
    *,
    vault_path: str,
    week_start_day: int = 0,
    approval_tool_names: set[str] | None = None,
) -> ToolBindingResult:
    """Resolve workflow tools from DSL text or SDK literals."""
    normalized_value = _normalize_tool_value(value, allow_empty=False)
    if DirectiveValueParser.is_empty(normalized_value):
        raise ValueError(
            "Tools directive requires explicit value - tools disabled by default for security"
        )

    normalized = DirectiveValueParser.normalize_string(normalized_value, to_lower=True)
    configs = get_enabled_tools_config()
    if normalized in ["true", "yes", "1", "on", "all"]:
        # Connection-gated tools are intentionally absent until configured.
        # An "all" declaration means every currently available tool, not every
        # globally enabled tool that could become available later.
        tool_names = list(configs)
    elif normalized in ["false", "no", "0", "off", "none"]:
        return ToolBindingResult(tool_functions=[], tool_instructions="", tool_specs=[])
    else:
        parsed_tools = _parse_tools(normalized_value)
        tool_names = []
        for name in parsed_tools:
            if name not in tool_names:
                tool_names.append(name)

    disabled_or_unknown = [
        tool_name for tool_name in tool_names if tool_name not in configs
    ]
    if disabled_or_unknown:
        available_tools = ", ".join(configs.keys())
        requested = ", ".join(disabled_or_unknown)
        raise ValueError(
            f"Tool(s) unavailable or disabled: {requested}. Available enabled tools: {available_tools}"
        )

    tool_classes: list[type[BaseTool]] = []
    tool_functions: list[Tool] = []
    tool_specs: list[ToolSpec] = []
    skipped_tools: list[tuple[str, list[str]]] = []
    invalid_tools: list[tuple[str, str]] = []

    for tool_name in tool_names:
        config = configs.get(tool_name)
        if config is None:
            continue

        required_secrets = config.required_secret_keys()
        try:
            _strategy_name, strategy_secrets = get_web_tool_strategy_requirements(
                tool_name
            )
        except Exception as exc:
            reason = str(exc)
            invalid_tools.append((tool_name, reason))
            logger.warning(
                "Tool skipped due to invalid strategy configuration",
                data={
                    "tool": tool_name,
                    "error_type": type(exc).__name__,
                    "error": reason,
                },
            )
            continue
        required_secrets = list(dict.fromkeys([*required_secrets, *strategy_secrets]))
        missing_secrets = [key for key in required_secrets if not secret_has_value(key)]
        if missing_secrets:
            skipped_tools.append((tool_name, missing_secrets))
            logger.warning(
                "Tool skipped due to missing secrets",
                data={"tool": tool_name, "missing_secrets": missing_secrets},
            )
            continue

        try:
            tool_class = _load_tool_class(tool_name)
            tool_classes.append(tool_class)
            tool_function = tool_class.get_tool(vault_path=vault_path)
            wrapped_tool = _wrap_tool_function(
                tool_function,
                tool_name=tool_name,
                recovery_policy=tool_class.get_recovery_policy(),
                requires_approval=(
                    True if tool_name in (approval_tool_names or set()) else None
                ),
            )
            tool_functions.append(wrapped_tool)
            tool_specs.append(
                ToolSpec(
                    name=tool_name,
                    params={},
                    tool_class=tool_class,
                    tool_function=wrapped_tool,
                    recovery_policy=tool_class.get_recovery_policy(),
                    week_start_day=week_start_day,
                )
            )
        except Exception as exc:
            raise ValueError(f"Failed to load tool '{tool_name}': {exc}") from exc

    tool_instructions = ""
    if skipped_tools:
        skipped_messages = [
            f"{name} (missing {', '.join(missing)})" for name, missing in skipped_tools
        ]
        note = "NOTE: The following tools were unavailable and skipped: " + "; ".join(
            skipped_messages
        )
        tool_instructions = (tool_instructions + "\n\n" + note).strip()
    if invalid_tools:
        invalid_messages = [f"{name} ({reason})" for name, reason in invalid_tools]
        note = (
            "NOTE: The following tools had invalid configuration and were skipped: "
            + "; ".join(invalid_messages)
        )
        tool_instructions = (tool_instructions + "\n\n" + note).strip()

    return ToolBindingResult(
        tool_functions=tool_functions,
        tool_instructions=tool_instructions,
        tool_specs=tool_specs,
    )


def merge_tool_bindings(results: list[Any]) -> ToolBindingResult:
    """Merge repeated tool declarations across directives/sections."""
    if not results:
        return ToolBindingResult(tool_functions=[], tool_instructions="", tool_specs=[])

    specs_by_name: dict[str, ToolSpec] = {}
    fallback_functions: list[Tool] = []
    notes: list[str] = []

    for result in results:
        binding = _coerce_binding_result(result)
        if binding is None:
            continue
        for line in (binding.tool_instructions or "").splitlines():
            if line.strip().startswith("NOTE:"):
                notes.append(line.strip())
        for spec in binding.tool_specs:
            specs_by_name[spec.name] = spec
        if not binding.tool_specs:
            for fn in binding.tool_functions:
                if fn not in fallback_functions:
                    fallback_functions.append(fn)

    tool_specs = list(specs_by_name.values())
    tool_functions = (
        [spec.tool_function for spec in tool_specs]
        if tool_specs
        else fallback_functions
    )
    tool_instructions = ""

    if notes:
        unique_notes: list[str] = []
        for note in notes:
            if note not in unique_notes:
                unique_notes.append(note)
        note_block = "\n".join(unique_notes)
        tool_instructions = (
            (tool_instructions + "\n\n" + note_block).strip()
            if tool_instructions
            else note_block
        )

    return ToolBindingResult(
        tool_functions=tool_functions,
        tool_instructions=tool_instructions,
        tool_specs=tool_specs,
    )


def _coerce_binding_result(result: Any) -> ToolBindingResult | None:
    if isinstance(result, ToolBindingResult):
        return result
    if isinstance(result, tuple):
        if len(result) >= 3:
            return ToolBindingResult(
                tool_functions=list(result[0] or []),
                tool_instructions=result[1] or "",
                tool_specs=list(result[2] or []),
            )
        if len(result) == 2:
            return ToolBindingResult(
                tool_functions=list(result[0] or []),
                tool_instructions=result[1] or "",
                tool_specs=[],
            )
    return None


def _normalize_tool_value(value: Any, *, allow_empty: bool) -> str:
    if isinstance(value, ToolBindingResult):
        return ", ".join(value.tool_names())
    if isinstance(value, bool):
        return "all" if value else "none"
    if value is None:
        if allow_empty:
            return ""
        raise ValueError("Tools value cannot be empty")
    if isinstance(value, list | tuple):
        parts: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("tools= entries must be strings")
            normalized = item.strip()
            if normalized:
                parts.append(normalized)
        joined = ", ".join(parts)
        if joined or allow_empty:
            return joined
        raise ValueError("Tools value cannot be empty")
    if not isinstance(value, str):
        raise ValueError("tools= must be a string, boolean, or list of strings")
    normalized = value.strip()
    if normalized or allow_empty:
        return normalized
    raise ValueError("Tools value cannot be empty")


def _get_tool_configs() -> dict[str, ToolConfig]:
    return cast(dict[str, ToolConfig], get_enabled_tools_config())


def _load_tool_class(tool_name: str) -> type[BaseTool]:
    configs = _get_tool_configs()
    if tool_name not in configs:
        available_tools = ", ".join(configs.keys())
        raise ValueError(
            f"Unknown tool '{tool_name}'. Available tools: {available_tools}"
        )

    config = configs[tool_name]
    try:
        module = importlib.import_module(config.module)
    except ImportError as exc:
        raise ValueError(
            f"Could not import module '{config.module}' for tool '{tool_name}': {exc}"
        ) from exc

    for _name, obj in inspect.getmembers(module, inspect.isclass):
        if obj != BaseTool and issubclass(obj, BaseTool):
            return obj
    raise ValueError(
        f"No BaseTool subclass found in module '{config.module}' for tool '{tool_name}'"
    )


def _tokenize_tools(value: str) -> list[str]:
    tokens: list[str] = []
    if DirectiveValueParser.is_empty(value):
        return tokens
    buf: list[str] = []
    depth = 0
    for ch in value:
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth > 0:
                depth -= 1
        if depth == 0 and (ch == "," or ch.isspace()):
            token = "".join(buf).strip()
            if token:
                tokens.append(token)
            buf = []
            continue
        buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        tokens.append(tail)
    return tokens


def _parse_tools(value: str) -> list[str]:
    tokens = _tokenize_tools(value)
    parsed: list[str] = []
    for token in tokens:
        base = token.strip()
        if not base:
            continue
        if "(" in base or ")" in base:
            raise ValueError(
                "Tool parameters are no longer supported in tools declarations"
            )
        parsed.append(base.lower())
    return parsed


def _wrap_tool_function(
    tool: Tool,
    *,
    tool_name: str,
    recovery_policy: ToolRecoveryPolicy = ToolRecoveryPolicy.UNKNOWN,
    requires_approval: bool | None = None,
) -> Tool:
    original_func = cast(Callable[..., Any], tool.function)
    original_takes_ctx = getattr(tool, "takes_ctx", False)

    async def _call_async(ctx: RunContext[Any], **kwargs: Any) -> ToolReturn:
        if not _has_meaningful_tool_args(kwargs):
            return _to_tool_return(
                tool_name,
                f"Tool '{tool_name}' requires named parameters.",
            )
        binding_error = _tool_argument_binding_error(
            original_func, original_takes_ctx=original_takes_ctx, ctx=ctx, kwargs=kwargs
        )
        if binding_error is not None:
            return _to_tool_return(
                tool_name,
                _format_tool_type_error(tool_name, binding_error),
                status="error",
                error_type="invalid_parameters",
            )
        if original_takes_ctx:
            result = await original_func(ctx, **kwargs)
        else:
            result = await original_func(**kwargs)
        return _to_tool_return(tool_name, wrap_web_tool_result(tool_name, result))

    def _call_sync(ctx: RunContext[Any], **kwargs: Any) -> ToolReturn:
        if not _has_meaningful_tool_args(kwargs):
            return _to_tool_return(
                tool_name,
                f"Tool '{tool_name}' requires named parameters.",
            )
        binding_error = _tool_argument_binding_error(
            original_func, original_takes_ctx=original_takes_ctx, ctx=ctx, kwargs=kwargs
        )
        if binding_error is not None:
            return _to_tool_return(
                tool_name,
                _format_tool_type_error(tool_name, binding_error),
                status="error",
                error_type="invalid_parameters",
            )
        if original_takes_ctx:
            result = original_func(ctx, **kwargs)
        else:
            result = original_func(**kwargs)
        return _to_tool_return(tool_name, wrap_web_tool_result(tool_name, result))

    wrapper = _call_async if inspect.iscoroutinefunction(original_func) else _call_sync
    untyped_wrapper: Any = wrapper
    try:
        sig = inspect.signature(original_func)
        params_list = list(sig.parameters.values())
        if not original_takes_ctx:
            ctx_param = inspect.Parameter(
                "ctx",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=RunContext,
            )
            params_list = [ctx_param] + params_list
        untyped_wrapper.__signature__ = sig.replace(parameters=params_list)
    except (ValueError, TypeError):
        pass

    wrapper.__name__ = getattr(original_func, "__name__", tool_name)
    wrapper.__doc__ = getattr(original_func, "__doc__", None)
    annotations = dict(getattr(original_func, "__annotations__", {}) or {})
    if not original_takes_ctx:
        annotations["ctx"] = RunContext
    wrapper.__annotations__ = annotations

    return type(tool)(
        cast(Any, wrapper),
        takes_ctx=True,
        name=getattr(tool, "name", None) or tool_name,
        description=getattr(tool, "description", None),
        requires_approval=(
            bool(requires_approval)
            if requires_approval is not None
            else getattr(tool, "requires_approval", False)
        ),
        metadata={
            **dict(getattr(tool, "metadata", None) or {}),
            "assistantmd": {
                **dict((getattr(tool, "metadata", None) or {}).get("assistantmd", {})),
                **tool_recovery_metadata(recovery_policy),
            },
        },
    )


def _tool_argument_binding_error(
    function: Callable[..., Any],
    *,
    original_takes_ctx: bool,
    ctx: RunContext,
    kwargs: dict[str, Any],
) -> TypeError | None:
    """Return call-shape errors without masking TypeError raised inside a tool."""
    try:
        signature = inspect.signature(function)
        if original_takes_ctx:
            signature.bind(ctx, **kwargs)
        else:
            signature.bind(**kwargs)
    except TypeError as exc:
        return exc
    except (ValueError, RuntimeError):
        return None
    return None


def _has_meaningful_tool_args(kwargs: dict[str, Any]) -> bool:
    """Return True when the tool call includes at least one non-empty user argument."""
    if not kwargs:
        return False
    for value in kwargs.values():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, list | tuple | dict | set) and not value:
            continue
        return True
    return False


def _format_tool_type_error(tool_name: str, exc: Exception) -> str:
    return (
        f"Invalid parameters for tool '{tool_name}': {exc}. Use named parameters only."
    )


def _to_tool_return(
    tool_name: str,
    result: Any,
    *,
    status: str = "completed",
    error_type: str | None = None,
) -> ToolReturn:
    """Normalize bound tool calls to a Pydantic ToolReturn envelope."""
    if isinstance(result, ToolReturn):
        existing_metadata = (
            dict(result.metadata) if isinstance(result.metadata, dict) else {}
        )
        existing_metadata.setdefault("status", status)
        existing_metadata.setdefault("tool_name", tool_name)
        existing_metadata.setdefault(
            "return_type", _return_value_type(result.return_value)
        )
        if error_type:
            existing_metadata.setdefault("error_type", error_type)
        return ToolReturn(
            return_value=result.return_value,
            content=result.content,
            metadata=existing_metadata,
        )

    metadata: dict[str, Any] = {
        "status": status,
        "tool_name": tool_name,
        "return_type": _return_value_type(result),
    }
    if error_type:
        metadata["error_type"] = error_type
    return ToolReturn(return_value=result, content=None, metadata=metadata)


def _return_value_type(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, str):
        return "text"
    if isinstance(value, dict | list | tuple):
        return "json"
    return type(value).__name__
