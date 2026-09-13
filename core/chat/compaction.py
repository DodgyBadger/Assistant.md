"""Chat history compaction service."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, cast

from pydantic import TypeAdapter
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    ToolCallPart,
    ToolReturnPart,
)

from core.chat.tool_history import analyze_tool_history
from core.constants import (
    CHAT_HISTORY_COMPACTION_INSTRUCTION,
    CHAT_HISTORY_COMPACTION_PROMPT_VERSION,
)
from core.identity import ExecutionAuthority
from core.logger import UnifiedLogger
from core.runtime.execution_tasks import (
    ExecutionTaskKind,
    ExecutionTaskSource,
    chat_session_scope,
    compaction_task_label,
)
from core.runtime.state import get_runtime_context, has_runtime_context
from core.runtime.task_runner import ExecutionTaskSpec
from core.settings import (
    get_compaction_keep_recent,
    get_compaction_token_threshold,
    get_compaction_type,
)
from core.tools.utils import estimate_token_count

from .chat_store import ChatStore

logger = UnifiedLogger(tag="chat-compaction")

_MODEL_MESSAGE_ADAPTER: TypeAdapter[ModelMessage] = TypeAdapter(ModelMessage)
_SESSION_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}
_SESSION_LOCKS_GUARD = asyncio.Lock()
_SUMMARY_MARKER = "AssistantMD compacted chat history"


@dataclass(frozen=True)
class ChatHistoryCompactionStatus:
    """Status estimate for one chat session."""

    session_id: str
    vault_name: str
    compaction_type: str
    messages_before: int
    estimated_tokens_before: int
    compaction_token_threshold: int
    compaction_keep_recent: int
    recommended: bool
    already_compacted: bool


@dataclass(frozen=True)
class ChatHistoryCompactionResult:
    """Result of rewriting one chat session history."""

    session_id: str
    vault_name: str
    status: str
    messages_before: int
    messages_after: int
    estimated_tokens_before: int
    estimated_tokens_after: int
    kept_recent: int
    summary_message_index: int
    compaction_id: str
    compacted_at: str
    source: str

    def as_api_dict(self) -> dict[str, Any]:
        """Return API/UI-safe result fields."""
        return asdict(self)

    def as_tool_dict(self) -> dict[str, Any]:
        """Return chat-agent-safe result fields."""
        return asdict(self)


@asynccontextmanager
async def chat_session_history_lock(
    *, session_id: str, vault_name: str
) -> AsyncIterator[None]:
    """Serialize canonical history mutation for one chat session."""
    lock = await _get_session_lock(session_id=session_id, vault_name=vault_name)
    async with lock:
        yield


async def get_compaction_status(
    *,
    session_id: str,
    vault_name: str,
    store: ChatStore | None = None,
) -> ChatHistoryCompactionStatus:
    """Return the current compaction status for one chat session."""
    chat_store = store or ChatStore()
    messages = chat_store.get_history(session_id, vault_name) or []
    estimated_tokens = estimate_history_tokens(messages)
    threshold = get_compaction_token_threshold()
    metadata = chat_store.get_session_metadata(session_id, vault_name)
    return ChatHistoryCompactionStatus(
        session_id=session_id,
        vault_name=vault_name,
        compaction_type=get_compaction_type(),
        messages_before=len(messages),
        estimated_tokens_before=estimated_tokens,
        compaction_token_threshold=threshold,
        compaction_keep_recent=get_compaction_keep_recent(),
        recommended=estimated_tokens >= threshold,
        already_compacted=bool(metadata.get("last_compaction")),
    )


async def compact_chat_history(
    *,
    session_id: str,
    vault_name: str,
    vault_path: str | None = None,
    focus: str | None = None,
    source: ExecutionTaskSource = ExecutionTaskSource.API,
    store: ChatStore | None = None,
) -> ChatHistoryCompactionResult:
    """Compact one chat session into a summary plus recent raw messages."""
    chat_store = store or ChatStore()
    source_value = str(source)
    logger.info(
        "chat_compaction_started",
        data={
            "event": "chat_compaction_started",
            "session_id": session_id,
            "vault_name": vault_name,
            "source": source_value,
            "focus_provided": bool((focus or "").strip()),
        },
    )
    try:
        async with chat_session_history_lock(
            session_id=session_id, vault_name=vault_name
        ):
            messages = chat_store.get_history(session_id, vault_name) or []
            integrity = analyze_tool_history(messages)
            if not integrity.ok:
                logger.warning(
                    "chat_compaction_tool_integrity_issue",
                    data={
                        "event": "chat_compaction_tool_integrity_issue",
                        "session_id": session_id,
                        "vault_name": vault_name,
                        "source": source_value,
                        **integrity.to_dict(),
                    },
                )
            if not messages:
                raise ValueError("Cannot compact an empty chat session.")

            keep_recent = get_compaction_keep_recent()
            older_messages, recent_messages = split_history_for_compaction(
                messages,
                keep_recent=keep_recent,
            )
            if not older_messages:
                raise ValueError("Chat session does not have older history to compact.")

            estimated_before = estimate_history_tokens(messages)
            trigger, reason = _compaction_trigger_and_reason(source)
            logger.info(
                "chat_compaction_plan_selected",
                data={
                    "event": "chat_compaction_plan_selected",
                    "session_id": session_id,
                    "vault_name": vault_name,
                    "source": source_value,
                    "trigger": trigger,
                    "reason": reason,
                    "prompt_contract_version": CHAT_HISTORY_COMPACTION_PROMPT_VERSION,
                    "history_mode": "effective",
                    "messages_before": len(messages),
                    "older_messages": len(older_messages),
                    "recent_messages": len(recent_messages),
                    "configured_keep_recent": keep_recent,
                    "estimated_tokens_before": estimated_before,
                    "transcript_export": "manual_only",
                    "tool_history_integrity_status": integrity.status,
                    "tool_history_issue_count": len(integrity.issues),
                    "multi_call_batch_count": integrity.multi_call_batch_count,
                    "multi_return_batch_count": integrity.multi_return_batch_count,
                },
            )

            summary = await _generate_compaction_summary(
                older_messages=older_messages,
                recent_messages=recent_messages,
                focus=focus,
            )
            if not summary:
                raise ValueError("Compaction summary generation returned empty output.")
            summary_message = build_compaction_summary_message(summary)
            replacement = [summary_message, *recent_messages]
            estimated_after = estimate_history_tokens(replacement)
            compacted_at = datetime.now(UTC).isoformat()
            compaction_id = uuid.uuid4().hex
            last_message_sequence_index = chat_store.get_highest_message_sequence_index(
                session_id,
                vault_name,
            )
            metadata_update = {
                "last_compaction": {
                    "compaction_id": compaction_id,
                    "compacted_at": compacted_at,
                    "source": source_value,
                    "trigger": trigger,
                    "reason": reason,
                    "prompt_contract_version": CHAT_HISTORY_COMPACTION_PROMPT_VERSION,
                    "compaction_type": get_compaction_type(),
                    "compaction_token_threshold": get_compaction_token_threshold(),
                    "compaction_keep_recent": keep_recent,
                    "messages_before": len(messages),
                    "messages_after": len(replacement),
                    "estimated_tokens_before": estimated_before,
                    "estimated_tokens_after": estimated_after,
                    "last_message_sequence_index": last_message_sequence_index,
                }
            }
            checkpoint_metadata = {
                **metadata_update["last_compaction"],
                "history_mode": "effective",
                "raw_messages_preserved": True,
            }
            chat_store.add_compaction_checkpoint(
                session_id=session_id,
                vault_name=vault_name,
                checkpoint_id=compaction_id,
                source=source_value,
                message_count_before=len(messages),
                last_message_sequence_index=last_message_sequence_index,
                summary_message=summary_message,
                replacement_history=replacement,
                metadata=checkpoint_metadata,
                metadata_update=metadata_update,
            )
            result = ChatHistoryCompactionResult(
                session_id=session_id,
                vault_name=vault_name,
                status="completed",
                messages_before=len(messages),
                messages_after=len(replacement),
                estimated_tokens_before=estimated_before,
                estimated_tokens_after=estimated_after,
                kept_recent=len(recent_messages),
                summary_message_index=0,
                compaction_id=compaction_id,
                compacted_at=compacted_at,
                source=source_value,
            )
            logger.info(
                "chat_compaction_completed",
                data={
                    "event": "chat_compaction_completed",
                    **result.as_tool_dict(),
                    "trigger": trigger,
                    "reason": reason,
                    "prompt_contract_version": CHAT_HISTORY_COMPACTION_PROMPT_VERSION,
                    "history_mode": "effective",
                    "raw_messages_preserved": True,
                    "checkpoint_id": compaction_id,
                    "last_message_sequence_index": last_message_sequence_index,
                    "token_delta": estimated_before - estimated_after,
                },
            )
            return result
    except Exception as exc:
        logger.warning(
            "chat_compaction_failed",
            data={
                "event": "chat_compaction_failed",
                "session_id": session_id,
                "vault_name": vault_name,
                "source": source_value,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        raise


def estimate_history_tokens(messages: list[ModelMessage]) -> int:
    """Estimate token count from provider-native message JSON."""
    if not messages:
        return 0
    parts = [
        _MODEL_MESSAGE_ADAPTER.dump_json(message).decode("utf-8")
        for message in messages
    ]
    return estimate_token_count("\n".join(parts))


def split_history_for_compaction(
    messages: list[ModelMessage],
    *,
    keep_recent: int,
) -> tuple[list[ModelMessage], list[ModelMessage]]:
    """Split history while preserving tool-call/result pairs in recent history."""
    if keep_recent <= 0 or keep_recent >= len(messages):
        return [], list(messages)
    start = max(0, len(messages) - keep_recent)
    start = _shift_recent_start_for_tool_pairs(messages, start)
    return list(messages[:start]), list(messages[start:])


def build_compaction_summary_message(summary: str) -> ModelRequest:
    """Build the system-maintained summary message stored after compaction."""
    content = f"{_SUMMARY_MARKER}\n\n{summary.strip()}"
    return ModelRequest(parts=[SystemPromptPart(content=content)])


def _compaction_trigger_and_reason(source: ExecutionTaskSource) -> tuple[str, str]:
    """Return stable audit labels for why compaction ran."""
    if source == ExecutionTaskSource.SYSTEM:
        return "auto", "token_threshold"
    if source == ExecutionTaskSource.TOOL:
        return "manual", "agent_tool_requested"
    if source == ExecutionTaskSource.API:
        return "manual", "api_requested"
    return "manual", f"{source.value}_requested"


async def maybe_auto_compact_after_turn(
    *,
    session_id: str,
    vault_name: str,
    vault_path: str,
) -> ChatHistoryCompactionResult | None:
    """Run automatic compaction after a completed chat turn when configured."""
    status = await get_compaction_status(session_id=session_id, vault_name=vault_name)
    if status.compaction_type != "auto" or not status.recommended:
        return None
    runtime = get_runtime_context() if has_runtime_context() else None
    if runtime is None:
        return await compact_chat_history(
            session_id=session_id,
            vault_name=vault_name,
            vault_path=vault_path,
            source=ExecutionTaskSource.SYSTEM,
        )
    session = runtime.chat_store.get_session_by_id(session_id)
    if session is None or session.vault_name != vault_name:
        raise LookupError(f"Chat session not found: {session_id}")
    return cast(
        ChatHistoryCompactionResult,
        await runtime.task_runner.run_inline(
            ExecutionTaskSpec(
                kind=ExecutionTaskKind.HISTORY_COMPACTION,
                scope=chat_session_scope(session_id),
                source=ExecutionTaskSource.SYSTEM,
                label=compaction_task_label(session_id),
                authority=ExecutionAuthority(session.owner_principal_id),
                metadata={
                    "vault": vault_name,
                    "session_id": session_id,
                    "automatic": True,
                },
            ),
            lambda _task: compact_chat_history(
                session_id=session_id,
                vault_name=vault_name,
                vault_path=vault_path,
                source=ExecutionTaskSource.SYSTEM,
            ),
        ),
    )


async def _get_session_lock(*, session_id: str, vault_name: str) -> asyncio.Lock:
    key = (vault_name, session_id)
    async with _SESSION_LOCKS_GUARD:
        lock = _SESSION_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _SESSION_LOCKS[key] = lock
        return lock


def _shift_recent_start_for_tool_pairs(messages: list[ModelMessage], start: int) -> int:
    while start > 0 and _boundary_splits_tool_pair(
        messages[start - 1], messages[start]
    ):
        start -= 1
    while start > 0 and _message_has_tool_return(messages[start]):
        start -= 1
    return start


def _boundary_splits_tool_pair(previous: ModelMessage, current: ModelMessage) -> bool:
    previous_calls = _tool_call_ids(previous)
    current_returns = _tool_return_ids(current)
    return bool(previous_calls & current_returns)


def _tool_call_ids(message: ModelMessage) -> set[str]:
    ids: set[str] = set()
    if not isinstance(message, ModelResponse):
        return ids
    for part in getattr(message, "parts", ()) or ():
        if isinstance(part, ToolCallPart):
            tool_call_id = getattr(part, "tool_call_id", None)
            if tool_call_id:
                ids.add(str(tool_call_id))
    return ids


def _tool_return_ids(message: ModelMessage) -> set[str]:
    ids: set[str] = set()
    if not isinstance(message, ModelRequest):
        return ids
    for part in getattr(message, "parts", ()) or ():
        if isinstance(part, ToolReturnPart):
            tool_call_id = getattr(part, "tool_call_id", None)
            if tool_call_id:
                ids.add(str(tool_call_id))
    return ids


def _message_has_tool_return(message: ModelMessage) -> bool:
    return bool(_tool_return_ids(message))


async def _generate_compaction_summary(
    *,
    older_messages: list[ModelMessage],
    recent_messages: list[ModelMessage],
    focus: str | None,
) -> str:
    from core.llm.agents import collect_response, create_agent

    prompt = _build_summary_prompt(
        older_messages=older_messages,
        recent_messages=recent_messages,
        focus=focus,
    )
    agent = await create_agent()
    result = await collect_response(agent, prompt)
    return str(result.output or "").strip()


def _build_summary_prompt(
    *,
    older_messages: list[ModelMessage],
    recent_messages: list[ModelMessage] | None = None,
    focus: str | None,
) -> str:
    focus_text = (focus or "").strip()
    payload = {
        "prompt_contract_version": CHAT_HISTORY_COMPACTION_PROMPT_VERSION,
        "base_instruction": CHAT_HISTORY_COMPACTION_INSTRUCTION,
        "user_focus": focus_text or None,
        "older_history": [
            _message_to_compaction_source(message) for message in older_messages
        ],
        "retained_recent_history": [
            _message_to_compaction_source(message)
            for message in (recent_messages or [])
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _message_to_compaction_source(message: ModelMessage) -> dict[str, Any]:
    role = "assistant" if isinstance(message, ModelResponse) else "user"
    if _is_compaction_summary_message(message):
        role = "system"
    return {
        "role": role,
        "message_type": type(message).__name__,
        "content": _render_message_text(message),
    }


def _is_compaction_summary_message(message: ModelMessage) -> bool:
    if not isinstance(message, ModelRequest):
        return False
    for part in getattr(message, "parts", ()) or ():
        if isinstance(part, SystemPromptPart):
            content = getattr(part, "content", "")
            if isinstance(content, str) and content.startswith(_SUMMARY_MARKER):
                return True
    return False


def _render_message_text(message: ModelMessage) -> str:
    rendered: list[str] = []
    for part in getattr(message, "parts", ()) or ():
        if isinstance(part, ToolCallPart):
            rendered.append(f"[tool call] {getattr(part, 'tool_name', 'tool')}")
        elif isinstance(part, ToolReturnPart):
            rendered.append(_render_tool_return_for_compaction(part))
        else:
            content = getattr(part, "content", None)
            if isinstance(content, str):
                rendered.append(content)
    return "\n".join(rendered).strip()


def _render_tool_return_for_compaction(part: ToolReturnPart) -> str:
    tool_name = getattr(part, "tool_name", "tool")
    outcome = str(getattr(part, "outcome", "success") or "success").strip().lower()
    content = getattr(part, "content", None)
    if outcome in {"failed", "denied"}:
        return f"[tool result omitted] {tool_name}: outcome={outcome}"
    if _is_empty_tool_return_content(content):
        return f"[tool result omitted] {tool_name}: empty result"
    return f"[tool result] {tool_name}: {content}"


def _is_empty_tool_return_content(content: Any) -> bool:
    if content is None:
        return True
    if isinstance(content, str):
        return not content.strip()
    if isinstance(content, list | tuple | dict | set):
        return len(content) == 0
    return False
