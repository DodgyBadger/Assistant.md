"""Utilities for working with pydantic_ai ModelMessage histories."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import TypeAdapter
from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    NativeToolReturnPart,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_core import to_jsonable_python

_MODEL_MESSAGE_ADAPTER: TypeAdapter[ModelMessage] = TypeAdapter(ModelMessage)

MessageSourceKind = Literal[
    "user",
    "assistant",
    "system",
    "tool_result",
    "retrieval_result",
    "tool_call",
    "mixed",
    "unknown",
]


@dataclass(frozen=True)
class MessageProjection:
    """Rebuildable text and provenance derived from one canonical message."""

    role: str
    content_text: str
    source_kind: MessageSourceKind
    tool_names: tuple[str, ...]


def _model_request_has_user_prompt(message: ModelRequest) -> bool:
    parts = getattr(message, "parts", None) or ()
    for part in parts:
        if isinstance(part, UserPromptPart):
            return True
    return False


def run_slice(msgs: list[ModelMessage], runs_to_take: int) -> list[ModelMessage]:
    run_ids: list[str] = []
    for m in msgs:
        rid = getattr(m, "run_id", None)
        if rid:
            if not run_ids or run_ids[-1] != rid:
                run_ids.append(rid)
    if run_ids:
        if runs_to_take == 0:
            return []
        take_runs = runs_to_take if runs_to_take > 0 else len(run_ids)
        selected_run_ids = set(run_ids[-take_runs:])
        start_idx = 0
        for idx, m in enumerate(msgs):
            if getattr(m, "run_id", None) in selected_run_ids:
                start_idx = idx
                break
        return msgs[start_idx:]
    # Fallback: slice from last user message to end (user→assistant→tools)
    last_user_idx = None
    for idx in range(len(msgs) - 1, -1, -1):
        m = msgs[idx]
        role = getattr(m, "role", None)
        if role and role.lower() == "user":
            last_user_idx = idx
            break
        if isinstance(m, ModelRequest) and _model_request_has_user_prompt(m):
            last_user_idx = idx
            break
    if last_user_idx is not None:
        return msgs[last_user_idx:]
    return msgs


def project_message(msg: ModelMessage) -> MessageProjection:
    """Project provider-native history into deterministic searchable text."""
    # Normalize role names across message types
    if isinstance(msg, ModelRequest):
        role = "user"
    elif isinstance(msg, ModelResponse):
        role = "assistant"
    else:
        role = getattr(msg, "role", None) or msg.__class__.__name__.lower()

    source_kinds: set[MessageSourceKind] = set()
    tool_names: list[str] = []
    parts = getattr(msg, "parts", None)
    if parts:
        has_system_part = False
        rendered_parts: list[str] = []
        for part in parts:
            if isinstance(part, UserPromptPart | TextPart):
                part_content = getattr(part, "content", None)
                if isinstance(part_content, str):
                    rendered_parts.append(part_content)
                    source_kinds.add("user" if role == "user" else "assistant")
            elif isinstance(part, SystemPromptPart):
                has_system_part = True
                part_content = getattr(part, "content", None)
                if isinstance(part_content, str):
                    rendered_parts.append(part_content)
                    source_kinds.add("system")
            elif isinstance(part, ToolReturnPart | NativeToolReturnPart):
                tool_name = (
                    getattr(part, "tool_name", None)
                    or getattr(part, "tool_call_id", None)
                    or "tool"
                )
                if tool_name not in tool_names:
                    tool_names.append(str(tool_name))
                part_content = getattr(part, "content", None)
                rendered_content = _render_tool_return_content(part_content)
                if rendered_content:
                    rendered_parts.append(f"[{tool_name}] {rendered_content}")
                source_kinds.add(
                    "retrieval_result" if tool_name == "session_ops" else "tool_result"
                )
            elif isinstance(part, ToolCallPart):
                tool_name = (
                    getattr(part, "tool_name", None)
                    or getattr(part, "tool_call_id", None)
                    or "tool"
                )
                if tool_name not in tool_names:
                    tool_names.append(str(tool_name))
                rendered_parts.append(f"[{tool_name}] (tool call)")
                source_kinds.add("tool_call")
        if rendered_parts:
            if has_system_part and role == "user":
                role = "system"
            return MessageProjection(
                role=role,
                content_text="\n".join(rendered_parts),
                source_kind=_source_kind(source_kinds),
                tool_names=tuple(tool_names),
            )

    # Try direct content if no parts were rendered
    content = getattr(msg, "content", None)
    if isinstance(content, str) and content:
        fallback_kind: MessageSourceKind = "unknown"
        if role == "user":
            fallback_kind = "user"
        elif role == "assistant":
            fallback_kind = "assistant"
        elif role == "system":
            fallback_kind = "system"
        return MessageProjection(role, content, fallback_kind, ())

    return MessageProjection(role, "", _source_kind(source_kinds), tuple(tool_names))


def extract_role_and_text(msg: ModelMessage) -> tuple[str, str]:
    """Return the compatibility role/text pair from the shared projection."""
    projection = project_message(msg)
    return projection.role, projection.content_text


def project_message_json(message_json: str) -> MessageProjection:
    """Project one serialized canonical message without changing its payload."""
    return project_message(_MODEL_MESSAGE_ADAPTER.validate_json(message_json))


def _source_kind(source_kinds: set[MessageSourceKind]) -> MessageSourceKind:
    if not source_kinds:
        return "unknown"
    if len(source_kinds) == 1:
        return next(iter(source_kinds))
    return "mixed"


def _render_tool_return_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    projected = _jsonable_without_binary_payloads(content)
    return json.dumps(projected, ensure_ascii=False, sort_keys=True, indent=2)


def _jsonable_without_binary_payloads(value: Any) -> Any:
    if isinstance(value, BinaryContent):
        return {
            "identifier": value.identifier,
            "kind": value.kind,
            "media_type": value.media_type,
        }
    if isinstance(value, bytes | bytearray):
        return {"kind": "binary", "size_bytes": len(value)}
    if isinstance(value, Mapping):
        return {
            str(key): _jsonable_without_binary_payloads(item)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_jsonable_without_binary_payloads(item) for item in value]
    projected = to_jsonable_python(value, serialize_unknown=True)
    if isinstance(projected, dict) and projected.get("kind") == "binary":
        return {key: item for key, item in projected.items() if key != "data"}
    return projected
