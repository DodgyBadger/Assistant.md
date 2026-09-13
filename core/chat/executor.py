"""
Chat execution logic for dynamic prompt execution.

Handles stateful/stateless chat with user-selected tools and models.
Persists canonical chat history in the structured chat store.
"""

import json
import sqlite3
import traceback
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic_ai import BinaryContent, DeferredToolRequests, DeferredToolResults
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    SystemPromptPart,
    UserContent,
    UserPromptPart,
)
from pydantic_ai.usage import UsageLimits

from core.authoring.context_manager import build_context_manager_history_processor
from core.authoring.shared.tool_binding import resolve_tool_binding
from core.chat.chat_store import ChatStore
from core.chat.compaction import (
    maybe_auto_compact_after_turn,
)
from core.chat.instructions import constant_instruction, primary_chat_instruction_layers
from core.chat.run_recovery import ChatRunRecoveryCoordinator
from core.constants import REGULAR_CHAT_INSTRUCTIONS
from core.identity import ExecutionAuthority, require_current_execution_authority
from core.llm.agents import create_agent
from core.llm.capabilities.factory import build_chat_capabilities
from core.llm.capabilities.mcp_tools import (
    MCPChatCapabilities,
    acquire_mcp_chat_capabilities,
    mcp_unavailable_instruction,
)
from core.llm.model_factory import build_model_instance
from core.llm.model_selection import ModelExecutionSpec, resolve_model_execution_spec
from core.llm.model_utils import (
    get_model_capabilities,
    model_supports_capability,
    resolve_model,
)
from core.llm.thinking import ThinkingValue, thinking_value_to_label
from core.logger import UnifiedLogger
from core.mcp import MCPReadinessSnapshot, MCPUnavailableConnection
from core.runtime.buffers import BufferStore
from core.runtime.state import get_runtime_context, has_runtime_context
from core.settings import (
    get_chat_model_requests_limit,
    get_chat_tool_calls_limit,
    get_chunking_max_image_bytes_per_image,
    get_chunking_max_image_mb_per_image,
    get_default_model_thinking,
)
from core.tools.failures import classify_exception

logger = UnifiedLogger(tag="chat-executor")


PromptInput = str | Sequence[UserContent] | None
ChatMode = str
NORMAL_CHAT_MODE = "normal"
INLINE_EDIT_CHAT_MODE = "inline_edit"


_CHAT_STORE = ChatStore()
_LATEST_TURN_FAILURE_METADATA_KEY = "latest_turn_failure"


@dataclass(frozen=True)
class UploadedImageAttachment:
    """Direct chat image attachment sourced from request payload bytes."""

    display_name: str
    content: BinaryContent


class ChatCapabilityError(ValueError):
    """Raised when a chat request requires model capabilities that are unavailable."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details = details or {}


class ChatContextTemplateError(ValueError):
    """Raised when a selected chat context template cannot be used."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details = details or {}


class ChatReviewPendingError(ValueError):
    """Raised when a session must resolve a deferred review before another turn."""

    def __init__(self, *, session_id: str):
        super().__init__(
            "Resolve the pending inline edit review before sending another message."
        )
        self.details = {"session_id": session_id, "error_type": "DeferredReviewPending"}


class ChatToolCallLimitError(ValueError):
    """Raised when a chat run exceeds the configured tool-call limit."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details = details or {}


class ChatModelRequestLimitError(ValueError):
    """Raised when a chat run exceeds the configured model-request limit."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details = details or {}


@dataclass
class PreparedChatExecution:
    """Preflighted chat execution state safe to reuse across sync and streaming paths."""

    agent: Any
    # Completed prior turns only. The active prompt is passed separately to
    # Pydantic AI and becomes canonical history through result.new_messages().
    message_history: list[ModelMessage] | None
    prompt_for_history: str
    user_prompt: PromptInput
    attached_image_count: int
    model: str
    tools: list[str]
    thinking: ThinkingValue | None = None
    context_template: str | None = None
    workspace_path: str = ""
    chat_mode: ChatMode = NORMAL_CHAT_MODE
    deferred_tool_results: DeferredToolResults | None = None
    recovery: ChatRunRecoveryCoordinator | None = None
    automatic_restart_count: int = 0
    mcp_snapshot: MCPReadinessSnapshot | None = None
    mcp_unavailable: tuple[MCPUnavailableConnection, ...] = ()
    has_mcp_tools: bool = False
    has_advanced_shell: bool = False

    @property
    def has_effective_tools(self) -> bool:
        """Return whether this run exposes built-in or MCP tools."""
        return bool(self.tools) or self.has_mcp_tools or self.has_advanced_shell

    async def close(self) -> None:
        """Release execution-scoped MCP catalog leases."""
        if self.mcp_snapshot is None:
            return
        snapshot = self.mcp_snapshot
        self.mcp_snapshot = None
        await snapshot.close()

    def resume_config(self) -> dict[str, Any]:
        """Return JSON-safe config needed to resume a deferred review."""
        return {
            "model": self.model,
            "tools": list(self.tools),
            "thinking": thinking_value_to_label(self.thinking),
            "context_template": self.context_template,
            "workspace_path": self.workspace_path,
            "chat_mode": normalize_chat_mode(self.chat_mode),
        }


def normalize_chat_mode(value: str | None) -> ChatMode:
    """Normalize chat mode values accepted by the API and persisted resume config."""
    normalized = str(value or NORMAL_CHAT_MODE).strip().lower()
    return (
        INLINE_EDIT_CHAT_MODE
        if normalized == INLINE_EDIT_CHAT_MODE
        else NORMAL_CHAT_MODE
    )


def approval_tools_for_chat_mode(chat_mode: ChatMode) -> set[str]:
    """Return tool names that require deferred review in the given chat mode."""
    if normalize_chat_mode(chat_mode) == INLINE_EDIT_CHAT_MODE:
        return {"file_write"}
    return set()


def persist_chat_session_mode(
    *, vault_name: str, session_id: str, chat_mode: ChatMode
) -> None:
    """Persist the canonical mode selected for a chat session."""
    _CHAT_STORE.set_session_chat_mode(
        session_id=session_id,
        vault_name=vault_name,
        chat_mode=normalize_chat_mode(chat_mode),
    )


def _accepted_user_request(prepared: PreparedChatExecution) -> ModelRequest:
    """Build the accepted user turn persisted before model execution."""
    return ModelRequest(parts=[UserPromptPart(content=prepared.prompt_for_history)])


def _user_prompt_text(message: ModelMessage) -> str | None:
    """Return the first user prompt text from a persisted request message."""
    if not isinstance(message, ModelRequest):
        return None
    text_parts: list[str] = []
    for part in getattr(message, "parts", ()) or ():
        if isinstance(part, UserPromptPart) and isinstance(part.content, str):
            text_parts.append(part.content)
    if not text_parts:
        return None
    return "\n\n".join(text_parts).strip()


def _retry_metadata_from_marker(marker: dict[str, Any]) -> dict[str, Any]:
    """Preserve bounded manual retry audit fields across marker rewrites."""
    keys = (
        "manual_retry_count",
        "last_manual_retry_task_id",
        "last_manual_retry_started_at",
    )
    return {key: marker[key] for key in keys if key in marker}


def _messages_after_accepted_user_request(
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    """Drop the active user request that AssistantMD already persisted."""
    filtered: list[ModelMessage] = []
    skipped_accepted_user = False
    for message in messages:
        if not skipped_accepted_user and _is_user_prompt_request(message):
            skipped_accepted_user = True
            continue
        filtered.append(message)
    return filtered


def _is_user_prompt_request(message: ModelMessage) -> bool:
    if not isinstance(message, ModelRequest):
        return False
    return any(
        isinstance(part, UserPromptPart) for part in getattr(message, "parts", ())
    )


def _serialize_exception(exc: BaseException) -> dict[str, Any]:
    """Return stable exception details for activity-log diagnostics."""
    return {
        "error_type": type(exc).__name__,
        "error": str(exc),
        "traceback": "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        ).strip(),
    }


def _build_failure_recovery_marker(
    *,
    exc: Exception,
    phase: str,
    streaming: bool,
    model: str | None,
    tools: Sequence[str] | None,
    chat_mode: ChatMode = NORMAL_CHAT_MODE,
    sequence_index: int,
) -> dict[str, Any]:
    """Build session metadata that lets the next turn recover from a failed run."""
    classification = classify_exception(exc, phase=phase)
    metadata = classification.to_metadata()
    return {
        "status": "failed",
        "phase": phase,
        "streaming": streaming,
        "error_type": type(exc).__name__,
        "error": str(exc),
        "failure_kind": classification.failure_kind,
        "retryable": classification.retryable,
        "http_status": classification.http_status,
        "retry_after": classification.retry_after,
        "model": model,
        "tools": list(tools or []),
        "chat_mode": normalize_chat_mode(chat_mode),
        "accepted_user_sequence_index": sequence_index,
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "suggested_action": (
            "Treat the previous user request as accepted but unfinished. "
            f"{metadata['suggested_action']}"
        ),
    }


def _record_latest_turn_failure(
    *,
    session_id: str,
    vault_name: str,
    exc: Exception,
    phase: str,
    streaming: bool,
    model: str | None,
    tools: Sequence[str] | None,
    chat_mode: ChatMode = NORMAL_CHAT_MODE,
) -> None:
    """Persist an internal failure marker for a user turn with no assistant outcome."""
    existing = _CHAT_STORE.get_session_metadata(session_id, vault_name).get(
        _LATEST_TURN_FAILURE_METADATA_KEY
    )
    marker = _build_failure_recovery_marker(
        exc=exc,
        phase=phase,
        streaming=streaming,
        model=model,
        tools=tools,
        chat_mode=chat_mode,
        sequence_index=_CHAT_STORE.get_highest_message_sequence_index(
            session_id, vault_name
        ),
    )
    if (
        isinstance(existing, dict)
        and existing.get("accepted_user_sequence_index")
        == marker["accepted_user_sequence_index"]
    ):
        marker.update(_retry_metadata_from_marker(existing))
    _CHAT_STORE.update_session_metadata(
        session_id=session_id,
        vault_name=vault_name,
        metadata_update={_LATEST_TURN_FAILURE_METADATA_KEY: marker},
        advance_history_revision=True,
    )
    logger.warning(
        "chat_turn_failure_marker_recorded",
        data={
            "event": "chat_turn_failure_marker_recorded",
            "vault_name": vault_name,
            "session_id": session_id,
            **marker,
        },
    )


def _clear_latest_turn_failure(
    *,
    session_id: str,
    vault_name: str,
    connection: sqlite3.Connection | None = None,
) -> None:
    """Clear any internal failure marker after a successful assistant outcome."""
    metadata = _CHAT_STORE.get_session_metadata(
        session_id,
        vault_name,
        connection=connection,
    )
    if _LATEST_TURN_FAILURE_METADATA_KEY not in metadata:
        return
    _CHAT_STORE.update_session_metadata(
        session_id=session_id,
        vault_name=vault_name,
        remove_keys=(_LATEST_TURN_FAILURE_METADATA_KEY,),
        advance_history_revision=True,
        connection=connection,
    )


def _failure_recovery_message(marker: dict[str, Any]) -> ModelRequest | None:
    """Render a metadata failure marker as ephemeral model context."""
    if marker.get("status") != "failed":
        return None
    phase = str(marker.get("phase") or "unknown")
    error_type = str(marker.get("error_type") or "Error")
    error = str(marker.get("error") or "").strip()
    sequence_index = marker.get("accepted_user_sequence_index")
    text = (
        "Internal recovery note: the previous user request was accepted into chat history "
        "but the assistant response failed before it was persisted. "
        f"Failure phase: {phase}. Error type: {error_type}."
    )
    failure_kind = str(marker.get("failure_kind") or "").strip()
    if failure_kind:
        text += f" Failure kind: {failure_kind}."
    if "retryable" in marker:
        text += f" Retryable: {bool(marker.get('retryable'))}."
    if error:
        text += f" Error: {error}."
    if sequence_index is not None:
        text += f" Accepted user message sequence index: {sequence_index}."
    suggested_action = str(marker.get("suggested_action") or "").strip()
    if suggested_action:
        text += f" Suggested action: {suggested_action}"
    text += (
        " For broad or long-running work, resume from durable state rather than assuming partial "
        "failed-run tool history was persisted: inspect goal_ops goals/checkpoints, vault activity, "
        "changed files, saved artifacts, and session history; then continue in a smaller checkpointed batch."
    )
    return ModelRequest(parts=[SystemPromptPart(content=text)])


def _with_failure_recovery_context(
    messages: list[ModelMessage] | None,
    *,
    session_id: str,
    vault_name: str,
) -> list[ModelMessage] | None:
    """Append ephemeral recovery context for an unfinished prior turn."""
    metadata = _CHAT_STORE.get_session_metadata(session_id, vault_name)
    marker = metadata.get(_LATEST_TURN_FAILURE_METADATA_KEY)
    if not isinstance(marker, dict):
        return messages
    recovery_message = _failure_recovery_message(marker)
    if recovery_message is None:
        return messages
    return [*(messages or []), recovery_message]


def _chat_usage_limits() -> UsageLimits | None:
    tool_calls_limit = get_chat_tool_calls_limit()
    model_requests_limit = get_chat_model_requests_limit()
    return UsageLimits(
        request_limit=model_requests_limit if model_requests_limit > 0 else None,
        tool_calls_limit=tool_calls_limit if tool_calls_limit > 0 else None,
    )


def _build_tool_call_limit_error(exc: UsageLimitExceeded) -> ChatToolCallLimitError:
    limit = get_chat_tool_calls_limit()
    limit_label = f" of {limit}" if limit > 0 else ""
    return ChatToolCallLimitError(
        (
            f"Chat stopped because it exceeded the configured tool-call limit"
            f"{limit_label}. "
            "Increase chat_tool_calls_limit or set it to 0 to disable this guard."
        ),
        details={
            "setting": "chat_tool_calls_limit",
            "limit": limit,
            "error_type": type(exc).__name__,
            "error": str(exc),
        },
    )


def _build_model_request_limit_error(
    exc: UsageLimitExceeded,
) -> ChatModelRequestLimitError:
    limit = get_chat_model_requests_limit()
    limit_label = f" of {limit}" if limit > 0 else ""
    return ChatModelRequestLimitError(
        (
            f"Chat stopped because it reached the configured model-request limit"
            f"{limit_label} for this run. "
            "The session is intact. Ask the agent to continue in a new message; it should resume "
            "from durable state such as goal_ops checkpoints, vault activity, changed files, saved "
            "artifacts, and session history. Increase chat_model_requests_limit only when the larger "
            "scope is intentional."
        ),
        details={
            "setting": "chat_model_requests_limit",
            "limit_kind": "request_limit",
            "limit": limit,
            "error_type": type(exc).__name__,
            "error": str(exc),
        },
    )


def _build_chat_usage_limit_error(
    exc: UsageLimitExceeded,
) -> ChatToolCallLimitError | ChatModelRequestLimitError:
    if "request_limit" in str(exc):
        return _build_model_request_limit_error(exc)
    return _build_tool_call_limit_error(exc)


def _usage_limit_label(
    error: ChatToolCallLimitError | ChatModelRequestLimitError,
) -> str:
    return (
        "model-request limit"
        if isinstance(error, ChatModelRequestLimitError)
        else "tool-call limit"
    )


def _usage_limit_display_label(
    error: ChatToolCallLimitError | ChatModelRequestLimitError,
) -> str:
    return (
        "Model-request limit"
        if isinstance(error, ChatModelRequestLimitError)
        else "Tool-call limit"
    )


def _chat_event_for_message(message: str) -> str | None:
    """Map existing chat lifecycle messages to stable activity event names."""
    if message in {"Chat execution started", "Streaming chat execution started"}:
        return "chat_turn_started"
    if message in {"Chat execution completed", "Streaming chat execution completed"}:
        return "chat_turn_completed"
    if message in {"Chat execution cancelled", "Streaming chat execution cancelled"}:
        return "chat_turn_cancelled"
    if "failed" in message.lower() or "limit exceeded" in message.lower():
        return "chat_turn_failed"
    return None


def _chat_status_for_event(event: str | None) -> str | None:
    """Return a normalized status label for chat activity events."""
    if event == "chat_turn_started":
        return "started"
    if event == "chat_turn_completed":
        return "completed"
    if event == "chat_turn_cancelled":
        return "cancelled"
    if event == "chat_turn_failed":
        return "failed"
    return None


def _summarize_tool_activity(
    tool_activity: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build compact tool-call counts for activity logs."""
    by_tool: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for item in tool_activity.values():
        tool_name = str(item.get("tool_name") or "tool")
        status = str(item.get("status") or "unknown")
        by_tool[tool_name] = by_tool.get(tool_name, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1
    return {
        "tool_call_count": len(tool_activity),
        "tool_call_status_counts": by_status,
        "tool_call_tool_counts": by_tool,
    }


def _log_chat_lifecycle(
    message: str,
    *,
    vault_name: str,
    session_id: str,
    model: str | None = None,
    tools: list[str] | None = None,
    streaming: bool,
    phase: str,
    prompt_length: int | None = None,
    attached_image_count: int | None = None,
    context_template: str | None = None,
    workspace_path: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Emit structured lifecycle logs for chat session execution."""
    event = _chat_event_for_message(message)
    payload: dict[str, Any] = {
        "vault_name": vault_name,
        "session_id": session_id,
        "streaming": streaming,
        "phase": phase,
    }
    if event:
        payload["event"] = event
        payload["status"] = _chat_status_for_event(event)
    if model is not None:
        payload["model"] = model
    if tools is not None:
        payload["tools"] = list(tools)
        payload["tools_count"] = len(tools)
    if context_template is not None:
        payload["context_template"] = context_template
    if workspace_path is not None:
        payload["workspace_path"] = workspace_path
    if prompt_length is not None:
        payload["prompt_length"] = prompt_length
    if attached_image_count is not None:
        payload["attached_image_count"] = attached_image_count
    rss_bytes = _get_process_rss_bytes()
    if rss_bytes is not None:
        payload["memory_rss_bytes"] = rss_bytes
    if extra:
        payload.update(extra)
    logger.info(message, data=payload)


def _log_chat_failure(
    message: str,
    *,
    vault_name: str,
    session_id: str,
    model: str | None = None,
    tools: list[str] | None = None,
    streaming: bool,
    phase: str,
    prompt_length: int | None = None,
    attached_image_count: int | None = None,
    context_template: str | None = None,
    workspace_path: str | None = None,
    extra: dict[str, Any] | None = None,
    exc: BaseException,
) -> None:
    """Emit structured failure logs for chat session execution."""
    payload = _serialize_exception(exc)
    if isinstance(exc, Exception):
        payload.update(classify_exception(exc, phase=phase).to_metadata())
    else:
        payload.update(
            {
                "failure_kind": "cancelled",
                "retryable": False,
                "phase": phase,
            }
        )
    if extra:
        payload.update(extra)
    rss_bytes = _get_process_rss_bytes()
    if rss_bytes is not None:
        payload["memory_rss_bytes"] = rss_bytes
    event = _chat_event_for_message(message) or "chat_turn_failed"
    logger.error(
        message,
        data={
            "event": event,
            "status": _chat_status_for_event(event),
            "vault_name": vault_name,
            "session_id": session_id,
            "streaming": streaming,
            "phase": phase,
            "model": model,
            "tools": list(tools or []),
            "tools_count": len(tools or []),
            "context_template": context_template,
            "workspace_path": workspace_path,
            "prompt_length": prompt_length,
            "attached_image_count": attached_image_count,
            **payload,
        },
    )


async def _try_auto_compact_after_turn(
    *,
    session_id: str,
    vault_name: str,
    vault_path: str,
) -> None:
    """Run post-turn automatic compaction without failing the completed chat."""
    try:
        await maybe_auto_compact_after_turn(
            session_id=session_id,
            vault_name=vault_name,
            vault_path=vault_path,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Automatic chat history compaction failed after completed chat turn",
            data={
                "vault_name": vault_name,
                "session_id": session_id,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )


def _get_process_rss_bytes() -> int | None:
    """Return current process RSS in bytes when available."""
    try:
        with open("/proc/self/status", encoding="utf-8") as handle:
            for line in handle:
                if not line.startswith("VmRSS:"):
                    continue
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    return int(parts[1]) * 1024
    except OSError:
        return None
    return None


def _normalize_tool_detail(value: Any) -> Any:
    """
    Convert streamed tool details into JSON-safe data without preview truncation.
    """
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return ""
        try:
            return json.loads(stripped)
        except (TypeError, ValueError):
            return stripped
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        return str(value)


def _build_model_capability_details(
    model_alias: str,
    requested_capability: str,
    *,
    image_paths: list[str] | None = None,
    image_uploads: list[UploadedImageAttachment] | None = None,
) -> dict[str, Any]:
    """Collect stable context describing a model capability mismatch."""
    execution = resolve_model_execution_spec(model_alias)
    base_alias = execution.base_alias or model_alias.strip()
    provider = None
    model_string = None
    declared_capabilities = ["text"] if base_alias == "test" else []

    if base_alias and base_alias != "test":
        try:
            provider, model_string = resolve_model(base_alias)
        except ValueError:
            provider = None
            model_string = None
        try:
            declared_capabilities = sorted(get_model_capabilities(base_alias))
        except ValueError:
            declared_capabilities = []

    return {
        "model_alias": model_alias,
        "model_base_alias": base_alias,
        "provider": provider,
        "model_string": model_string,
        "requested_capability": requested_capability,
        "declared_capabilities": declared_capabilities,
        "image_path_count": sum(
            1 for path in (image_paths or []) if (path or "").strip()
        ),
        "image_upload_count": len(image_uploads or []),
    }


@dataclass
class ChatRunDeps:
    """Per-run dependencies/caches for chat agents."""

    authority: ExecutionAuthority
    context_manager_cache: dict[str, Any] = field(default_factory=dict)
    context_manager_now: datetime | None = None
    buffer_store: BufferStore = field(default_factory=BufferStore)
    buffer_store_registry: dict[str, BufferStore] = field(default_factory=dict)
    session_id: str = ""
    vault_name: str = ""
    message_history: list[ModelMessage] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)


def _resolve_context_manager_now() -> datetime | None:
    if not has_runtime_context():
        return None
    try:
        runtime = get_runtime_context()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to get runtime context for context_manager_now",
            data={"error": str(exc)},
        )
        return None
    features = runtime.config.features or {}
    raw_value = features.get("context_manager_now")
    if raw_value is None:
        return None
    if isinstance(raw_value, datetime):
        return raw_value
    if isinstance(raw_value, str):
        try:
            return datetime.fromisoformat(raw_value)
        except ValueError:
            return None
    return None


def _check_image_size(display_name: str, size_bytes: int) -> None:
    """Raise ValueError if the image exceeds the configured per-image byte limit."""
    max_image_bytes = get_chunking_max_image_bytes_per_image()
    if max_image_bytes > 0 and size_bytes > max_image_bytes:
        max_image_mb = get_chunking_max_image_mb_per_image()
        raise ValueError(
            f"Image '{display_name}' is too large to attach ({size_bytes} bytes). "
            f"Maximum per image is chunking_max_image_mb_per_image={max_image_mb} MB."
        )


def _resolve_image_prompt(
    *,
    prompt_text: str,
    history_prompt_text: str | None = None,
    image_paths: list[str] | None,
    image_uploads: list[UploadedImageAttachment] | None,
    vault_path: str,
) -> tuple[PromptInput, str, int]:
    """Build prompt payload and history-safe text from optional image attachments."""
    history_text = (
        history_prompt_text if history_prompt_text is not None else prompt_text
    )
    if not image_paths and not image_uploads:
        return prompt_text, history_text, 0

    vault_root = Path(vault_path).resolve()
    prompt_content: list[UserContent] = [prompt_text]
    history_lines: list[str] = []
    seen_paths: set[str] = set()

    for raw_path in image_paths or []:
        candidate = (raw_path or "").strip()
        if not candidate:
            continue

        image_path = Path(candidate)
        if not image_path.is_absolute():
            image_path = vault_root / image_path
        resolved_path = image_path.resolve()

        if resolved_path != vault_root and vault_root not in resolved_path.parents:
            raise ValueError(
                f"Image path '{candidate}' is outside the vault and cannot be attached."
            )
        if not resolved_path.is_file():
            raise ValueError(f"Image file not found: {candidate}")

        resolved_key = str(resolved_path)
        if resolved_key in seen_paths:
            continue
        seen_paths.add(resolved_key)

        file_content = BinaryContent.from_path(resolved_path)
        if not file_content.is_image:
            raise ValueError(
                f"File is not an image and cannot be attached: {candidate}"
            )
        _check_image_size(candidate, len(file_content.data))

        prompt_content.append(file_content)
        display_path = resolved_path.relative_to(vault_root).as_posix()
        history_lines.append(f"- {display_path}")

    for upload in image_uploads or []:
        file_content = upload.content
        display_name = (upload.display_name or "").strip() or "uploaded-image"
        if not file_content.is_image:
            raise ValueError(
                f"Uploaded file '{display_name}' is not an image and cannot be attached."
            )
        _check_image_size(display_name, len(file_content.data))
        prompt_content.append(file_content)
        history_lines.append(f"- [upload] {display_name}")

    if len(prompt_content) == 1:
        return prompt_text, history_text, 0

    prompt_for_history = "\n".join(
        [
            history_text,
            "",
            "[Attached images]",
            *history_lines,
        ]
    )
    return prompt_content, prompt_for_history, len(prompt_content) - 1


def _validate_image_capability(
    model_alias: str,
    image_paths: list[str] | None,
    image_uploads: list[UploadedImageAttachment] | None,
) -> None:
    """Fail fast if image attachments are requested for a non-vision model."""
    has_uploads = bool(image_uploads)
    if not image_paths and not has_uploads:
        return
    has_paths = any((path or "").strip() for path in (image_paths or []))
    if not has_paths and not has_uploads:
        return
    if model_supports_capability(model_alias, "vision"):
        return
    details = _build_model_capability_details(
        model_alias,
        "vision",
        image_paths=image_paths,
        image_uploads=image_uploads,
    )
    logger.warning("Chat capability mismatch", data=details)
    raise ChatCapabilityError(
        (
            f"Model '{model_alias}' does not declare 'vision' capability in settings.yaml. "
            "Use a vision-capable model or remove image attachments."
        ),
        details=details,
    )


async def _prepare_chat_execution(
    vault_name: str,
    vault_path: str,
    prompt: str,
    image_paths: list[str] | None,
    image_uploads: list[UploadedImageAttachment] | None,
    session_id: str,
    tools: list[str],
    model: str,
    thinking: ThinkingValue | None = None,
    context_template: str | None = None,
    chat_mode: ChatMode = NORMAL_CHAT_MODE,
    message_history_override: list[ModelMessage] | None = None,
    display_prompt: str | None = None,
) -> PreparedChatExecution:
    """Perform chat preflight before either sync or streaming execution begins."""
    _validate_image_capability(model, image_paths, image_uploads)
    workspace_path = _CHAT_STORE.get_session_workspace_path(session_id, vault_name)
    base_instructions, tool_instructions, model_instance, tool_functions = (
        _prepare_agent_config(
            vault_name,
            vault_path,
            tools,
            model,
            thinking,
            chat_mode=chat_mode,
        )
    )

    user_prompt, prompt_for_history, attached_image_count = _resolve_image_prompt(
        prompt_text=prompt,
        history_prompt_text=display_prompt,
        image_paths=image_paths,
        image_uploads=image_uploads,
        vault_path=vault_path,
    )
    mcp_chat = await _acquire_chat_mcp_capabilities()
    try:
        advanced_shell_tool = await _acquire_primary_chat_advanced_shell_tool()
        if advanced_shell_tool is not None:
            tool_functions.append(advanced_shell_tool)
        capabilities = build_chat_capabilities(
            vault_name=vault_name,
            vault_path=vault_path,
            session_id=session_id,
            model_alias=model,
            context_template=context_template,
            now=_resolve_context_manager_now(),
            workspace_path=workspace_path,
            event_sink=_CHAT_STORE,
            tools=tool_functions,
            tool_instructions="",
            history_processor_factory=build_context_manager_history_processor,
        )
        if mcp_chat is not None:
            capabilities.extend(mcp_chat.capabilities)
        recovery = ChatRunRecoveryCoordinator.from_tools(tool_functions)
        capabilities.append(recovery.capability(session_id=session_id))
        agent = await create_agent(
            model=model_instance,
            output_type=[str, DeferredToolRequests],
            capabilities=capabilities,
        )
        for instruction in primary_chat_instruction_layers(
            base_instructions=base_instructions,
            tool_instructions=tool_instructions,
            has_advanced_shell=advanced_shell_tool is not None,
        ):
            agent.instructions(constant_instruction(instruction))
        if mcp_chat is not None and (
            unavailable_note := mcp_unavailable_instruction(mcp_chat.unavailable)
        ):
            agent.instructions(lambda: unavailable_note)

        base_message_history = (
            message_history_override
            if message_history_override is not None
            else _CHAT_STORE.get_history(session_id, vault_name)
        )
        message_history = _with_failure_recovery_context(
            base_message_history,
            session_id=session_id,
            vault_name=vault_name,
        )
        return PreparedChatExecution(
            agent=agent,
            message_history=message_history,
            prompt_for_history=prompt_for_history,
            user_prompt=user_prompt,
            attached_image_count=attached_image_count,
            model=model,
            tools=list(tools),
            thinking=thinking,
            context_template=context_template,
            workspace_path=workspace_path,
            chat_mode=normalize_chat_mode(chat_mode),
            recovery=recovery,
            mcp_snapshot=mcp_chat.snapshot if mcp_chat is not None else None,
            mcp_unavailable=mcp_chat.unavailable if mcp_chat is not None else (),
            has_mcp_tools=mcp_chat.has_tools if mcp_chat is not None else False,
            has_advanced_shell=advanced_shell_tool is not None,
        )
    except BaseException:
        if mcp_chat is not None:
            await mcp_chat.snapshot.close()
        raise


async def _prepare_deferred_review_resume_execution(
    *,
    vault_name: str,
    vault_path: str,
    session_id: str,
    tools: list[str],
    model: str,
    message_history: list[ModelMessage],
    deferred_tool_results: DeferredToolResults,
    thinking: ThinkingValue | None = None,
    context_template: str | None = None,
    chat_mode: ChatMode = NORMAL_CHAT_MODE,
) -> PreparedChatExecution:
    """Prepare a chat execution that resumes deferred tool review results."""
    workspace_path = _CHAT_STORE.get_session_workspace_path(session_id, vault_name)
    base_instructions, tool_instructions, model_instance, tool_functions = (
        _prepare_agent_config(
            vault_name,
            vault_path,
            tools,
            model,
            thinking,
            chat_mode=chat_mode,
        )
    )

    mcp_chat = await _acquire_chat_mcp_capabilities()
    try:
        advanced_shell_tool = await _acquire_primary_chat_advanced_shell_tool()
        if advanced_shell_tool is not None:
            tool_functions.append(advanced_shell_tool)
        capabilities = build_chat_capabilities(
            vault_name=vault_name,
            vault_path=vault_path,
            session_id=session_id,
            model_alias=model,
            context_template=context_template,
            now=_resolve_context_manager_now(),
            workspace_path=workspace_path,
            event_sink=_CHAT_STORE,
            tools=tool_functions,
            tool_instructions="",
            history_processor_factory=build_context_manager_history_processor,
        )
        if mcp_chat is not None:
            capabilities.extend(mcp_chat.capabilities)
        recovery = ChatRunRecoveryCoordinator.from_tools(tool_functions)
        capabilities.append(recovery.capability(session_id=session_id))
        agent = await create_agent(
            model=model_instance,
            output_type=[str, DeferredToolRequests],
            capabilities=capabilities,
        )
        for instruction in primary_chat_instruction_layers(
            base_instructions=base_instructions,
            tool_instructions=tool_instructions,
            has_advanced_shell=advanced_shell_tool is not None,
        ):
            agent.instructions(constant_instruction(instruction))
        if mcp_chat is not None and (
            unavailable_note := mcp_unavailable_instruction(mcp_chat.unavailable)
        ):
            agent.instructions(lambda: unavailable_note)

        return PreparedChatExecution(
            agent=agent,
            message_history=list(message_history),
            prompt_for_history="",
            user_prompt=None,
            attached_image_count=0,
            model=model,
            tools=list(tools),
            thinking=thinking,
            context_template=context_template,
            workspace_path=workspace_path,
            chat_mode=normalize_chat_mode(chat_mode),
            deferred_tool_results=deferred_tool_results,
            recovery=recovery,
            mcp_snapshot=mcp_chat.snapshot if mcp_chat is not None else None,
            mcp_unavailable=mcp_chat.unavailable if mcp_chat is not None else (),
            has_mcp_tools=mcp_chat.has_tools if mcp_chat is not None else False,
            has_advanced_shell=advanced_shell_tool is not None,
        )
    except BaseException:
        if mcp_chat is not None:
            await mcp_chat.snapshot.close()
        raise


async def _acquire_chat_mcp_capabilities() -> MCPChatCapabilities | None:
    """Acquire current-principal MCP capabilities only for primary chat."""
    if not has_runtime_context():
        return None
    manager = get_runtime_context().mcp_manager
    if manager is None:
        return None
    return await acquire_mcp_chat_capabilities(
        manager=manager,
        authority=require_current_execution_authority(),
    )


async def _acquire_primary_chat_advanced_shell_tool() -> Any | None:
    """Resolve the deployment shell only within an owned primary chat run."""
    if not has_runtime_context():
        return None
    service = get_runtime_context().advanced_shell
    if service is None:
        return None
    return await service.resolve_for_primary_chat(require_current_execution_authority())


def _prepare_agent_config(
    vault_name: str,
    vault_path: str,
    tools: list[str],
    model: str,
    thinking: ThinkingValue | None = None,
    chat_mode: ChatMode = NORMAL_CHAT_MODE,
) -> tuple:
    """
    Prepare agent configuration (shared between streaming and non-streaming).

    Returns:
        Tuple of (base_instructions, tool_instructions, model_instance, tool_functions)
    """
    base_instructions = REGULAR_CHAT_INSTRUCTIONS

    # Process tools directive to get tool functions
    tool_functions = []
    tool_instructions = ""

    if tools:  # Only process if tools list is not empty
        tools_value = ", ".join(tools)  # Convert list to comma-separated string
        binding = resolve_tool_binding(
            tools_value,
            vault_path=vault_path,
            approval_tool_names=approval_tools_for_chat_mode(chat_mode),
        )
        tool_functions = binding.tool_functions
        tool_instructions = binding.tool_instructions

    resolved_thinking = (
        thinking if thinking is not None else get_default_model_thinking()
    )
    model_instance = build_model_instance(model, thinking=resolved_thinking)
    if isinstance(model_instance, ModelExecutionSpec) and model_instance.mode == "skip":
        raise ValueError(
            "Chat execution does not support skip mode model alias 'none'. "
            "Select a concrete model."
        )

    return base_instructions, tool_instructions, model_instance, tool_functions
