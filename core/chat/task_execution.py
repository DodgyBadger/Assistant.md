"""Task-owned chat streaming execution helpers."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from pydantic_ai import (
    AgentRunResultEvent,
    DeferredToolRequests,
    DeferredToolResults,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
)
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelMessage, TextPart, ToolReturnPart
from pydantic_ai.usage import RunUsage, UsageLimits

from core.authoring.context_manager import ContextTemplateExecutionError
from core.chat import executor as chat_executor
from core.chat.chat_store import ChatStore
from core.chat.compaction import chat_session_history_lock
from core.chat.deferred_reviews import (
    DeferredReviewError,
    StoredDeferredReview,
    capture_deferred_review_context,
    create_deferred_review,
    has_pending_deferred_review,
    log_deferred_review_created,
    mark_deferred_review_terminal,
    summarize_deferred_review,
)
from core.chat.run_recovery import ChatRecoveryStrategy
from core.chat.task_events import ChatTaskEventBuffer, ChatTaskEventCursorExpired
from core.identity import ExecutionAuthority
from core.llm.capabilities.chat_context import build_context_template_error_details
from core.llm.capabilities.chat_tool_output_cache import tool_result_as_text
from core.llm.stream_retry import ModelStreamRetryPolicy
from core.runtime.buffers import get_session_buffer_store
from core.runtime.execution_tasks import (
    ExecutionTaskKind,
    ExecutionTaskSnapshot,
    ExecutionTaskSource,
    chat_session_scope,
    chat_task_label,
)
from core.runtime.state import get_runtime_context
from core.runtime.task_runner import (
    ExecutionGatePolicy,
    ExecutionTaskHooks,
    ExecutionTaskSpec,
)
from core.tools.failures import classify_exception, classify_tool_result_state
from core.tools.utils import estimate_token_count
from core.vault_state.rollback import rollback_task_file_mutations

_CHAT_STORE = ChatStore()


@dataclass(frozen=True)
class ChatStreamTaskStart:
    """Result returned after starting a background streaming chat task."""

    task: ExecutionTaskSnapshot
    session_id: str


class ChatRollbackRestartRequired(RuntimeError):
    """Signal that a failed chat must roll back before whole-turn replay."""

    def __init__(self, *, cause: Exception, attempt: int, max_attempts: int) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.attempt = attempt
        self.max_attempts = max_attempts


CHAT_TASK_EVENT_BUFFER = ChatTaskEventBuffer()


def _session_authority(session_id: str, vault_name: str) -> ExecutionAuthority:
    """Derive interactive execution authority from immutable session ownership."""
    session = _CHAT_STORE.get_session(session_id, vault_name)
    if session is None:
        raise ValueError(f"Chat session not found: {session_id}")
    return ExecutionAuthority(principal_id=session.owner_principal_id)


async def start_prepared_chat_stream_task(
    *,
    prepared: chat_executor.PreparedChatExecution,
    vault_name: str,
    vault_path: str,
    session_id: str,
    event_buffer: ChatTaskEventBuffer | None = None,
    persist_user_request: bool = True,
) -> ChatStreamTaskStart:
    """Start a prepared streaming chat run in a background execution task."""
    runtime = get_runtime_context()
    buffer = event_buffer or CHAT_TASK_EVENT_BUFFER
    try:
        task = await runtime.task_runner.start_background(
            ExecutionTaskSpec(
                kind=ExecutionTaskKind.CHAT,
                scope=chat_session_scope(session_id),
                source=ExecutionTaskSource.API,
                label=chat_task_label(session_id),
                authority=_session_authority(session_id, vault_name),
                metadata={
                    "vault": vault_name,
                    "session_id": session_id,
                    "streaming": True,
                    "model": prepared.model,
                    "tools": list(prepared.tools),
                    "retry": not persist_user_request,
                },
            ),
            lambda task: _run_prepared_chat_stream_task(
                task=task,
                prepared=prepared,
                vault_name=vault_name,
                vault_path=vault_path,
                session_id=session_id,
                event_buffer=buffer,
                persist_user_request=persist_user_request,
            ),
            hooks=ExecutionTaskHooks(
                on_cancelled=lambda task_id: _append_cancelled_if_open(buffer, task_id),
                on_failed=lambda task_id, exc: _handle_failed_chat_task(
                    task_id=task_id,
                    exc=exc,
                    prepared=prepared,
                    vault_name=vault_name,
                    vault_path=vault_path,
                    session_id=session_id,
                    event_buffer=buffer,
                ),
            ),
        )
    except BaseException:
        await prepared.close()
        raise
    return ChatStreamTaskStart(task=task, session_id=session_id)


async def _handle_failed_chat_task(
    *,
    task_id: str,
    exc: BaseException,
    prepared: chat_executor.PreparedChatExecution,
    vault_name: str,
    vault_path: str,
    session_id: str,
    event_buffer: ChatTaskEventBuffer,
) -> None:
    """Start whole-turn replay only after task-terminal rollback succeeds."""
    if not isinstance(exc, ChatRollbackRestartRequired):
        return
    chat_executor.logger.info(
        "Chat rollback-restart recovery hook invoked",
        data={
            "event": "chat_failed_task_recovery_hook",
            "status": "started",
            "task_id": task_id,
            "session_id": session_id,
            "vault_name": vault_name,
            "error_type": type(exc).__name__,
        },
    )
    try:
        rollback = rollback_task_file_mutations(
            task_id=task_id,
            terminal_status="failed",
            reason="chat_recovery_restart",
        )
        rollback_succeeded = rollback.rollback_status == "completed" or (
            rollback.skipped
            and rollback.reason in {"already_rolled_back", "no_mutations"}
        )
        if not rollback_succeeded:
            raise RuntimeError(rollback.reason or "rollback_incomplete")

        replacement_prepared = replace(
            prepared,
            automatic_restart_count=prepared.automatic_restart_count + 1,
        )
        replacement = await start_prepared_chat_stream_task(
            prepared=replacement_prepared,
            vault_name=vault_name,
            vault_path=vault_path,
            session_id=session_id,
            event_buffer=event_buffer,
            persist_user_request=False,
        )
    except Exception as rollback_exc:
        chat_executor.logger.error(
            "Primary chat rollback recovery abandoned",
            data={
                "event": "chat_recovery_abandoned",
                "status": "failed",
                "task_id": task_id,
                "session_id": session_id,
                "vault_name": vault_name,
                "reason": "rollback_or_restart_unavailable",
                "error_type": type(rollback_exc).__name__,
                "error": str(rollback_exc),
            },
        )
        if not await event_buffer.is_terminal(task_id):
            await event_buffer.append(
                task_id,
                "error",
                _error_event_data(
                    "\n\nError: Automatic recovery could not safely restart after "
                    "rolling back vault changes.",
                    {
                        "strategy": "manual_required",
                        "reason": "rollback_or_restart_unavailable",
                    },
                ),
            )
        return

    redirect_data = {
        "event": "chat_retry_redirect",
        "source_task_id": task_id,
        "replacement_task_id": replacement.task.task_id,
        "session_id": session_id,
        "strategy": "terminal_rollback_restart",
        "rollback_status": rollback.rollback_status or rollback.reason,
        "attempt": exc.attempt,
        "max_attempts": exc.max_attempts,
        "reset_response": True,
    }
    try:
        await event_buffer.append(
            task_id,
            "chat_retry_redirect",
            redirect_data,
        )
        chat_executor.logger.info(
            "Primary chat redirected to rollback-restart replacement",
            data={
                **redirect_data,
                "event": "chat_recovery_redirected",
                "status": "completed",
                "vault_name": vault_name,
            },
        )
    except RuntimeError as redirect_exc:
        chat_executor.logger.error(
            "Primary chat replacement started but redirect publication failed",
            data={
                "event": "chat_recovery_redirect_failed",
                "status": "failed",
                "task_id": task_id,
                "replacement_task_id": replacement.task.task_id,
                "session_id": session_id,
                "vault_name": vault_name,
                "error_type": type(redirect_exc).__name__,
                "error": str(redirect_exc),
            },
        )


async def start_chat_turn_retry_task(
    *,
    vault_name: str,
    vault_path: str,
    session_id: str,
    event_buffer: ChatTaskEventBuffer | None = None,
) -> ChatStreamTaskStart:
    """Retry the latest retryable unfinished chat turn without duplicating the user request."""
    metadata = _CHAT_STORE.get_session_metadata(session_id, vault_name)
    marker = metadata.get(chat_executor._LATEST_TURN_FAILURE_METADATA_KEY)
    if not isinstance(marker, dict) or marker.get("status") != "failed":
        raise ValueError("Chat session has no unfinished turn to retry.")
    if marker.get("retryable") is not True:
        raise ValueError("The latest unfinished chat turn is not marked retryable.")

    accepted_sequence_index = marker.get("accepted_user_sequence_index")
    if not isinstance(accepted_sequence_index, int | str):
        raise ValueError(
            "The unfinished turn marker does not identify an accepted user message."
        )
    try:
        accepted_sequence_index = int(accepted_sequence_index)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "The unfinished turn marker does not identify an accepted user message."
        ) from exc

    stored_messages = _CHAT_STORE.get_stored_messages(
        session_id,
        vault_name,
        mode="effective",
    )
    accepted_message = next(
        (
            message
            for message in stored_messages
            if message.sequence_index == accepted_sequence_index
        ),
        None,
    )
    if accepted_message is None:
        raise ValueError(
            "The accepted user message for the unfinished turn was not found."
        )
    prompt = chat_executor._user_prompt_text(accepted_message.message)
    if not prompt:
        raise ValueError(
            "The accepted user message for the unfinished turn is not retryable."
        )

    model = str(marker.get("model") or "").strip()
    if not model:
        raise ValueError(
            "The unfinished turn marker does not include a model to retry."
        )
    tools = [str(tool) for tool in marker.get("tools") or ()]
    chat_mode = chat_executor.normalize_chat_mode(marker.get("chat_mode"))
    message_history = [
        message.message
        for message in stored_messages
        if message.sequence_index < accepted_sequence_index
    ]

    retry_count = max(int(marker.get("manual_retry_count") or 0), 0) + 1
    marker = {
        **marker,
        "manual_retry_count": retry_count,
        "last_manual_retry_started_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    _CHAT_STORE.update_session_metadata(
        session_id=session_id,
        vault_name=vault_name,
        metadata_update={chat_executor._LATEST_TURN_FAILURE_METADATA_KEY: marker},
        advance_history_revision=True,
    )

    prepared = await chat_executor._prepare_chat_execution(
        vault_name=vault_name,
        vault_path=vault_path,
        prompt=prompt,
        image_paths=[],
        image_uploads=[],
        session_id=session_id,
        tools=tools,
        model=model,
        thinking=None,
        context_template=None,
        chat_mode=chat_mode,
        message_history_override=message_history,
    )
    started = await start_prepared_chat_stream_task(
        prepared=prepared,
        vault_name=vault_name,
        vault_path=vault_path,
        session_id=session_id,
        event_buffer=event_buffer,
        persist_user_request=False,
    )
    marker = {
        **marker,
        "last_manual_retry_task_id": started.task.task_id,
    }
    _CHAT_STORE.update_session_metadata(
        session_id=session_id,
        vault_name=vault_name,
        metadata_update={chat_executor._LATEST_TURN_FAILURE_METADATA_KEY: marker},
        advance_history_revision=False,
    )
    chat_executor.logger.info(
        "Manual chat retry task started",
        data={
            "event": "chat_manual_retry_started",
            "vault_name": vault_name,
            "session_id": session_id,
            "task_id": started.task.task_id,
            "accepted_user_sequence_index": accepted_sequence_index,
            "manual_retry_count": retry_count,
            "model": model,
            "tools": tools,
        },
    )
    return started


async def start_deferred_review_resume_task(
    *,
    vault_name: str,
    vault_path: str,
    session_id: str,
    review: StoredDeferredReview,
    deferred_tool_results: DeferredToolResults,
    tools: list[str],
    model: str,
    thinking: chat_executor.ThinkingValue | None = None,
    context_template: str | None = None,
    chat_mode: chat_executor.ChatMode = chat_executor.NORMAL_CHAT_MODE,
    event_buffer: ChatTaskEventBuffer | None = None,
) -> ChatStreamTaskStart:
    """Queue a resumed chat task behind other work in the same session."""
    runtime = get_runtime_context()
    buffer = event_buffer or CHAT_TASK_EVENT_BUFFER

    async def _mark_terminal(status: str, error: BaseException | None = None) -> None:
        try:
            mark_deferred_review_terminal(
                vault_name=vault_name,
                session_id=session_id,
                artifact_ref=review.artifact_ref,
                status=status,
                error=(
                    {"error_type": type(error).__name__, "message": str(error)}
                    if error is not None
                    else None
                ),
            )
        except DeferredReviewError:
            chat_executor.logger.warning(
                "Deferred review terminal state could not be recorded",
                data={"artifact_ref": review.artifact_ref, "status": status},
            )

    async def _run(tracked_task: ExecutionTaskSnapshot) -> None:
        async def _run_in_session_gate() -> None:
            await runtime.task_coordinator.mark_started(tracked_task.task_id)
            prepared = await chat_executor._prepare_deferred_review_resume_execution(
                vault_name=vault_name,
                vault_path=vault_path,
                session_id=session_id,
                tools=tools,
                model=model,
                message_history=review.resume_messages,
                deferred_tool_results=deferred_tool_results,
                thinking=thinking,
                context_template=context_template,
                chat_mode=chat_mode,
            )
            await _run_prepared_chat_stream_task(
                task=tracked_task,
                prepared=prepared,
                vault_name=vault_name,
                vault_path=vault_path,
                session_id=session_id,
                event_buffer=buffer,
                persist_user_request=False,
            )
            await _mark_terminal("completed")

        await runtime.task_runner.run_with_gate(
            tracked_task,
            ExecutionGatePolicy(
                key=chat_session_scope(session_id),
                queued_status="queued",
                clear_metadata={"queue_position": 0, "waiting_for_task_id": None},
            ),
            _run_in_session_gate,
        )

    task = await runtime.task_runner.start_background(
        ExecutionTaskSpec(
            kind=ExecutionTaskKind.CHAT,
            scope=chat_session_scope(session_id),
            source=ExecutionTaskSource.API,
            label=chat_task_label(session_id),
            authority=_session_authority(session_id, vault_name),
            metadata={
                "vault": vault_name,
                "session_id": session_id,
                "streaming": True,
                "model": model,
                "tools": list(tools),
                "deferred_review_artifact_ref": review.artifact_ref,
                "queued_by_session": True,
            },
        ),
        _run,
        hooks=ExecutionTaskHooks(
            on_cancelled=lambda task_id: _mark_review_cancelled(
                buffer, task_id, _mark_terminal
            ),
            on_failed=lambda _task_id, exc: _mark_terminal("failed", exc),
        ),
        start_immediately=False,
    )
    return ChatStreamTaskStart(task=task, session_id=session_id)


async def _mark_review_cancelled(
    event_buffer: ChatTaskEventBuffer,
    task_id: str,
    mark_terminal: Callable[[str], Awaitable[None]],
) -> None:
    await _append_cancelled_if_open(event_buffer, task_id)
    await mark_terminal("cancelled")


async def start_chat_stream_task(
    *,
    vault_name: str,
    vault_path: str,
    prompt: str,
    image_paths: list[str] | None,
    image_uploads: list[chat_executor.UploadedImageAttachment] | None,
    session_id: str,
    tools: list[str],
    model: str,
    display_prompt: str | None = None,
    thinking: chat_executor.ThinkingValue | None = None,
    context_template: str | None = None,
    chat_mode: chat_executor.ChatMode = chat_executor.NORMAL_CHAT_MODE,
    event_buffer: ChatTaskEventBuffer | None = None,
) -> ChatStreamTaskStart:
    """Preflight and start a streaming chat run in a background execution task."""
    chat_executor.persist_chat_session_mode(
        vault_name=vault_name,
        session_id=session_id,
        chat_mode=chat_mode,
    )
    prepared = await chat_executor._prepare_chat_execution(
        vault_name=vault_name,
        vault_path=vault_path,
        prompt=prompt,
        image_paths=image_paths,
        image_uploads=image_uploads,
        session_id=session_id,
        tools=tools,
        model=model,
        thinking=thinking,
        context_template=context_template,
        chat_mode=chat_mode,
        display_prompt=display_prompt,
    )
    return await start_prepared_chat_stream_task(
        prepared=prepared,
        vault_name=vault_name,
        vault_path=vault_path,
        session_id=session_id,
        event_buffer=event_buffer,
    )


async def start_queued_chat_stream_task(
    *,
    vault_name: str,
    vault_path: str,
    prompt: str,
    image_paths: list[str] | None,
    image_uploads: list[chat_executor.UploadedImageAttachment] | None,
    session_id: str,
    tools: list[str],
    model: str,
    display_prompt: str | None = None,
    thinking: chat_executor.ThinkingValue | None = None,
    context_template: str | None = None,
    chat_mode: chat_executor.ChatMode = chat_executor.NORMAL_CHAT_MODE,
    event_buffer: ChatTaskEventBuffer | None = None,
) -> ChatStreamTaskStart:
    """Start a streaming chat task that waits behind earlier tasks in its session."""
    runtime = get_runtime_context()
    buffer = event_buffer or CHAT_TASK_EVENT_BUFFER
    prepared_for_failure: chat_executor.PreparedChatExecution | None = None

    async def _run(tracked_task: ExecutionTaskSnapshot) -> None:
        async def _run_in_session_gate() -> None:
            nonlocal prepared_for_failure
            try:
                if has_pending_deferred_review(
                    vault_name=vault_name, session_id=session_id
                ):
                    raise chat_executor.ChatReviewPendingError(session_id=session_id)
                chat_executor.persist_chat_session_mode(
                    vault_name=vault_name,
                    session_id=session_id,
                    chat_mode=chat_mode,
                )
                prepared = await chat_executor._prepare_chat_execution(
                    vault_name=vault_name,
                    vault_path=vault_path,
                    prompt=prompt,
                    image_paths=image_paths,
                    image_uploads=image_uploads,
                    session_id=session_id,
                    tools=tools,
                    model=model,
                    thinking=thinking,
                    context_template=context_template,
                    chat_mode=chat_mode,
                    display_prompt=display_prompt,
                )
                prepared_for_failure = prepared
            except asyncio.CancelledError:
                raise
            except (
                Exception
            ) as exc:  # noqa: BLE001 - preflight failure is reported to subscribers
                await _publish_deferred_preflight_failure(
                    task_id=tracked_task.task_id,
                    exc=exc,
                    vault_name=vault_name,
                    session_id=session_id,
                    prompt=prompt,
                    tools=tools,
                    model=model,
                    context_template=context_template,
                    chat_mode=chat_mode,
                    event_buffer=buffer,
                )
                return

            await runtime.task_coordinator.mark_started(tracked_task.task_id)
            await _run_prepared_chat_stream_task(
                task=tracked_task,
                prepared=prepared,
                vault_name=vault_name,
                vault_path=vault_path,
                session_id=session_id,
                event_buffer=buffer,
            )

        await runtime.task_runner.run_with_gate(
            tracked_task,
            ExecutionGatePolicy(
                key=chat_session_scope(session_id),
                queued_status="queued",
                queued_metadata={},
                clear_metadata={
                    "queue_position": 0,
                    "waiting_for_task_id": None,
                },
            ),
            _run_in_session_gate,
        )

    async def _on_failed(task_id: str, exc: BaseException) -> None:
        if prepared_for_failure is None:
            return
        await _handle_failed_chat_task(
            task_id=task_id,
            exc=exc,
            prepared=prepared_for_failure,
            vault_name=vault_name,
            vault_path=vault_path,
            session_id=session_id,
            event_buffer=buffer,
        )

    task = await runtime.task_runner.start_background(
        ExecutionTaskSpec(
            kind=ExecutionTaskKind.CHAT,
            scope=chat_session_scope(session_id),
            source=ExecutionTaskSource.API,
            label=chat_task_label(session_id),
            authority=_session_authority(session_id, vault_name),
            metadata={
                "vault": vault_name,
                "session_id": session_id,
                "streaming": True,
                "model": model,
                "tools": list(tools),
                "queued_by_session": True,
            },
        ),
        _run,
        hooks=ExecutionTaskHooks(
            on_cancelled=lambda task_id: _append_cancelled_if_open(buffer, task_id),
            on_failed=_on_failed,
        ),
        start_immediately=False,
    )
    return ChatStreamTaskStart(task=task, session_id=session_id)


async def _append_cancelled_if_open(
    event_buffer: ChatTaskEventBuffer,
    task_id: str,
) -> None:
    """Publish a cancellation terminal event unless the stream already closed."""
    if await event_buffer.is_terminal(task_id):
        return
    try:
        await event_buffer.append(
            task_id,
            "cancelled",
            {
                "event": "cancelled",
                "choices": [
                    {
                        "delta": {},
                        "index": 0,
                        "finish_reason": "cancelled",
                    }
                ],
            },
        )
    except RuntimeError:
        return


@asynccontextmanager
async def _provided_execution_task(
    task: ExecutionTaskSnapshot,
) -> AsyncIterator[ExecutionTaskSnapshot]:
    yield task


async def _publish_deferred_preflight_failure(
    *,
    task_id: str,
    exc: Exception,
    vault_name: str,
    session_id: str,
    prompt: str,
    tools: list[str],
    model: str,
    context_template: str | None,
    chat_mode: chat_executor.ChatMode,
    event_buffer: ChatTaskEventBuffer,
) -> None:
    """Mark and publish a preflight failure from a deferred chat task."""
    runtime = get_runtime_context()
    workspace_path = _CHAT_STORE.get_session_workspace_path(session_id, vault_name)
    chat_executor._log_chat_failure(
        "Queued streaming chat preflight failed",
        vault_name=vault_name,
        session_id=session_id,
        model=model,
        tools=tools,
        streaming=True,
        phase="preflight",
        prompt_length=len(prompt),
        context_template=context_template,
        workspace_path=workspace_path,
        extra={"chat_mode": chat_executor.normalize_chat_mode(chat_mode)},
        exc=exc,
    )
    payload = _preflight_error_event_data(exc)
    await event_buffer.append(task_id, "error", payload)
    await runtime.task_coordinator.mark_failed(
        task_id, reason=f"{type(exc).__name__}: {exc}"
    )


def _preflight_error_event_data(exc: Exception) -> dict[str, Any]:
    """Build a user-facing terminal event for deferred chat preflight failures."""
    if isinstance(exc, chat_executor.ChatCapabilityError):
        return _error_event_data(f"\n\nError: {str(exc)}", exc.details)
    if isinstance(exc, chat_executor.ChatContextTemplateError):
        return _error_event_data(f"\n\nTemplate error: {str(exc)}", exc.details)
    if isinstance(exc, chat_executor.ChatReviewPendingError):
        return _error_event_data(f"\n\nReview pending: {str(exc)}", exc.details)
    if isinstance(
        exc,
        chat_executor.ChatToolCallLimitError | chat_executor.ChatModelRequestLimitError,
    ):
        return _error_event_data(
            f"\n\n{chat_executor._usage_limit_display_label(exc)} reached: {str(exc)}",
            exc.details,
        )

    classification = classify_exception(exc, phase="preflight")
    if _is_user_correctable_preflight_error(exc):
        return _error_event_data(
            f"\n\nError: {str(exc)}",
            classification.to_metadata(),
        )
    return _error_event_data(
        "\n\nError: An unexpected error occurred",
        classification.to_metadata(),
    )


def _is_user_correctable_preflight_error(exc: Exception) -> bool:
    """Return whether a preflight exception message is safe and actionable."""
    if isinstance(exc, chat_executor.ChatReviewPendingError):
        return True
    if not isinstance(exc, ValueError):
        return False
    message = str(exc)
    return (
        message.startswith("Image path ")
        or message.startswith("Image file not found: ")
        or message.startswith("File is not an image and cannot be attached: ")
        or message.startswith("Uploaded file ")
        or message.startswith("Image ")
        or message.startswith("Chat execution does not support skip mode model alias ")
    )


async def _run_prepared_chat_stream_task(
    *,
    task_id: str | None = None,
    task: ExecutionTaskSnapshot | None = None,
    prepared: chat_executor.PreparedChatExecution,
    vault_name: str,
    vault_path: str,
    session_id: str,
    event_buffer: ChatTaskEventBuffer,
    persist_user_request: bool = True,
) -> None:
    """Run one prepared chat and always release its external tool leases."""
    try:
        await _run_prepared_chat_stream_task_inner(
            task_id=task_id,
            task=task,
            prepared=prepared,
            vault_name=vault_name,
            vault_path=vault_path,
            session_id=session_id,
            event_buffer=event_buffer,
            persist_user_request=persist_user_request,
        )
    finally:
        await prepared.close()


async def _run_prepared_chat_stream_task_inner(
    *,
    task_id: str | None = None,
    task: ExecutionTaskSnapshot | None = None,
    prepared: chat_executor.PreparedChatExecution,
    vault_name: str,
    vault_path: str,
    session_id: str,
    event_buffer: ChatTaskEventBuffer,
    persist_user_request: bool = True,
) -> None:
    """Run a prepared streaming chat task and publish buffered task events."""
    runtime = get_runtime_context()
    if task is None and task_id is None:
        raise ValueError("Either task or task_id is required")
    task_context = (
        _provided_execution_task(task)
        if task is not None
        else runtime.task_coordinator.track_existing_task(str(task_id))
    )
    should_mark_started = task is None
    full_response = ""
    final_result = None
    messages_for_canonical_commit: list[ModelMessage] | None = None
    deferred_review = None
    tool_activity: dict[str, dict[str, Any]] = {}
    session_buffer_store = get_session_buffer_store(session_id)
    async with task_context as task:
        run_deps = chat_executor.ChatRunDeps(
            context_manager_now=chat_executor._resolve_context_manager_now(),
            buffer_store=session_buffer_store,
            buffer_store_registry={"session": session_buffer_store},
            session_id=session_id,
            vault_name=vault_name,
            message_history=list(prepared.message_history or []),
            tools=list(prepared.tools or []),
            authority=ExecutionAuthority(principal_id=task.principal_id),
        )
        if should_mark_started:
            await runtime.task_coordinator.mark_started(task.task_id)
        for unavailable in prepared.mcp_unavailable:
            await event_buffer.append(
                task.task_id,
                "mcp_connection_unavailable",
                {
                    "event": "mcp_connection_unavailable",
                    "task_id": task.task_id,
                    "connection_name": unavailable.display_name,
                    "status": unavailable.status,
                    "message": unavailable.message,
                },
            )
        if persist_user_request:
            async with chat_session_history_lock(
                session_id=session_id, vault_name=vault_name
            ):
                _CHAT_STORE.add_messages(
                    session_id,
                    vault_name,
                    [chat_executor._accepted_user_request(prepared)],
                )

        chat_executor._log_chat_lifecycle(
            "Streaming chat execution started",
            vault_name=vault_name,
            session_id=session_id,
            model=prepared.model,
            tools=prepared.tools,
            streaming=True,
            phase="agent_stream",
            prompt_length=len(prepared.prompt_for_history),
            attached_image_count=prepared.attached_image_count,
            context_template=prepared.context_template,
            workspace_path=prepared.workspace_path,
            extra={
                "history_message_count": len(prepared.message_history or []),
                "prompt_for_history_tokens": estimate_token_count(
                    prepared.prompt_for_history
                ),
                "task_id": task.task_id,
                "retry": not persist_user_request,
            },
        )

        try:
            usage = RunUsage()
            usage_limits = chat_executor._chat_usage_limits()
            retry_policy = ModelStreamRetryPolicy.from_settings()
            attempt_prompt = prepared.user_prompt
            attempt_history = prepared.message_history
            for attempt in range(1, retry_policy.max_attempts + 1):
                try:
                    final_result, full_response = await _collect_chat_stream_attempt(
                        prepared=prepared,
                        user_prompt=attempt_prompt,
                        message_history=attempt_history,
                        run_deps=run_deps,
                        usage=usage,
                        usage_limits=usage_limits,
                        task_id=task.task_id,
                        event_buffer=event_buffer,
                        tool_activity=tool_activity,
                        vault_name=vault_name,
                        session_id=session_id,
                    )
                    if (
                        attempt_history is not None
                        and attempt_history is not prepared.message_history
                    ):
                        history_prefix_length = len(prepared.message_history or [])
                        messages_for_canonical_commit = [
                            *attempt_history[history_prefix_length:],
                            *final_result.new_messages(),
                        ]
                    break
                except Exception as exc:
                    classification = classify_exception(exc, phase="agent_stream")
                    checkpoint = None
                    recovery_decision = None
                    if classification.retryable and prepared.recovery is not None:
                        recovery_decision = await prepared.recovery.decide(
                            conversation_id=session_id
                        )
                        checkpoint = recovery_decision.checkpoint
                    replay_scope = "no_chat_tools"
                    if checkpoint is not None:
                        assert recovery_decision is not None
                        attempt_prompt = None
                        attempt_history = checkpoint.messages
                        replay_scope = str(recovery_decision.strategy)
                    recovery_supported = recovery_decision is not None and (
                        recovery_decision.strategy
                        in {
                            ChatRecoveryStrategy.RESUME_SNAPSHOT,
                            ChatRecoveryStrategy.REPLAY_NO_EFFECT,
                        }
                    )
                    rollback_restart = recovery_decision is not None and (
                        recovery_decision.strategy
                        is ChatRecoveryStrategy.TERMINAL_ROLLBACK_RESTART
                    )
                    if (
                        classification.retryable
                        and rollback_restart
                        and retry_policy.can_retry_after(attempt)
                        and prepared.automatic_restart_count < retry_policy.retries
                    ):
                        raise ChatRollbackRestartRequired(
                            cause=exc,
                            attempt=attempt,
                            max_attempts=retry_policy.max_attempts,
                        ) from exc
                    can_retry = (
                        classification.retryable
                        and retry_policy.can_retry_after(attempt)
                        and (not prepared.has_effective_tools or recovery_supported)
                    )
                    if not can_retry:
                        if prepared.tools and recovery_decision is not None:
                            rejection_reason = (
                                "retry_budget_exhausted"
                                if not retry_policy.can_retry_after(attempt)
                                else recovery_decision.reason
                            )
                            rejected_data = {
                                "event": "chat_recovery_rejected",
                                "status": "rejected",
                                "task_id": task.task_id,
                                "session_id": session_id,
                                "vault_name": vault_name,
                                "strategy": str(recovery_decision.strategy),
                                "reason": rejection_reason,
                                "completed_tool_count": (
                                    recovery_decision.completed_tool_count
                                ),
                                "unresolved_tool_count": (
                                    recovery_decision.unresolved_tool_count
                                ),
                            }
                            chat_executor.logger.warning(
                                "Primary chat automatic recovery rejected",
                                data=rejected_data,
                            )
                            await event_buffer.append(
                                task.task_id,
                                "chat_recovery_rejected",
                                rejected_data,
                            )
                        raise
                    delay_seconds = retry_policy.delay_after(attempt)
                    retry_data = {
                        "event": "chat_retry_scheduled",
                        "task_id": task.task_id,
                        "session_id": session_id,
                        "model": prepared.model,
                        "attempt": attempt,
                        "next_attempt": attempt + 1,
                        "max_attempts": retry_policy.max_attempts,
                        "delay_seconds": delay_seconds,
                        "failure_kind": classification.failure_kind,
                        "error_type": classification.error_type,
                        "replay_scope": replay_scope,
                        "strategy": replay_scope,
                        "reset_response": True,
                    }
                    if recovery_decision is not None:
                        retry_data.update(
                            {
                                "completed_tool_count": (
                                    recovery_decision.completed_tool_count
                                ),
                                "unresolved_tool_count": (
                                    recovery_decision.unresolved_tool_count
                                ),
                            }
                        )
                    if checkpoint is not None:
                        assert recovery_decision is not None
                        retry_data.update(
                            {
                                "recovery_run_id": checkpoint.run_id,
                                "recovery_step_index": checkpoint.step_index,
                                "trimmed_failed_response": (
                                    checkpoint.trimmed_failed_response
                                ),
                            }
                        )
                        checkpoint_data = {
                            "event": "chat_recovery_checkpoint_selected",
                            "status": "selected",
                            "task_id": task.task_id,
                            "session_id": session_id,
                            "vault_name": vault_name,
                            "run_id": checkpoint.run_id,
                            "step_index": checkpoint.step_index,
                            "message_count": len(checkpoint.messages),
                            "trimmed_failed_response": (
                                checkpoint.trimmed_failed_response
                            ),
                            "interrupted": checkpoint.interrupted,
                            "completed_tool_count": (
                                recovery_decision.completed_tool_count
                            ),
                            "unresolved_tool_count": (
                                recovery_decision.unresolved_tool_count
                            ),
                        }
                        chat_executor.logger.info(
                            "Primary chat recovery checkpoint selected",
                            data=checkpoint_data,
                        )
                        await event_buffer.append(
                            task.task_id,
                            "chat_recovery_checkpoint_selected",
                            checkpoint_data,
                        )
                    chat_executor.logger.warning(
                        "Primary chat retry scheduled", data=retry_data
                    )
                    await event_buffer.append(
                        task.task_id,
                        "chat_retry_scheduled",
                        retry_data,
                    )
                    await asyncio.sleep(delay_seconds)

            if final_result:
                deferred_requests = getattr(final_result, "output", None)
                async with chat_session_history_lock(
                    session_id=session_id,
                    vault_name=vault_name,
                ):
                    with _CHAT_STORE.transaction() as connection:
                        _CHAT_STORE.add_messages(
                            session_id,
                            vault_name,
                            chat_executor._messages_after_accepted_user_request(
                                messages_for_canonical_commit
                                or final_result.new_messages()
                            ),
                            connection=connection,
                        )
                        chat_executor._clear_latest_turn_failure(
                            session_id=session_id,
                            vault_name=vault_name,
                            connection=connection,
                        )
                        if isinstance(deferred_requests, DeferredToolRequests):
                            deferred_review = create_deferred_review(
                                vault_name=vault_name,
                                session_id=session_id,
                                originating_task_id=task.task_id,
                                requests=deferred_requests,
                                resume_messages=list(final_result.all_messages()),
                                resume_config=prepared.resume_config(),
                                review_context=capture_deferred_review_context(
                                    vault_path=vault_path,
                                    requests=deferred_requests,
                                ),
                                connection=connection,
                                log_created=False,
                            )
                if deferred_review is not None:
                    log_deferred_review_created(deferred_review)
                chat_executor._log_chat_lifecycle(
                    (
                        "Streaming chat execution paused for inline review"
                        if deferred_review is not None
                        else "Streaming chat execution completed"
                    ),
                    vault_name=vault_name,
                    session_id=session_id,
                    model=prepared.model,
                    tools=prepared.tools,
                    streaming=True,
                    phase="session_persist",
                    prompt_length=len(prepared.prompt_for_history),
                    attached_image_count=prepared.attached_image_count,
                    context_template=prepared.context_template,
                    workspace_path=prepared.workspace_path,
                    extra={
                        **chat_executor._summarize_tool_activity(tool_activity),
                        "response_length": len(full_response),
                        **(
                            {
                                "deferred_review_artifact_ref": deferred_review.artifact_ref,
                                "deferred_review_count": deferred_review.review_count,
                            }
                            if deferred_review is not None
                            else {}
                        ),
                    },
                )
                if deferred_review is not None:
                    await event_buffer.append(
                        task.task_id,
                        "review_required",
                        {
                            "event": "review_required",
                            **summarize_deferred_review(deferred_review),
                        },
                    )

            await event_buffer.append(
                task.task_id,
                "done",
                {
                    "event": "done",
                    "choices": [
                        {
                            "delta": {},
                            "index": 0,
                            "finish_reason": (
                                "tool_review_required"
                                if deferred_review is not None
                                else "stop"
                            ),
                        }
                    ],
                    "tool_summary": tool_activity,
                },
            )

        except asyncio.CancelledError as exc:
            chat_executor._log_chat_failure(
                "Streaming chat execution cancelled",
                vault_name=vault_name,
                session_id=session_id,
                model=prepared.model,
                tools=prepared.tools,
                streaming=True,
                phase="agent_stream",
                prompt_length=len(prepared.prompt_for_history),
                attached_image_count=prepared.attached_image_count,
                context_template=prepared.context_template,
                workspace_path=prepared.workspace_path,
                extra=chat_executor._summarize_tool_activity(tool_activity),
                exc=exc,
            )
            await event_buffer.append(
                task.task_id,
                "cancelled",
                {
                    "event": "cancelled",
                    "choices": [
                        {
                            "delta": {},
                            "index": 0,
                            "finish_reason": "cancelled",
                        }
                    ],
                },
            )
            raise
        except ChatRollbackRestartRequired as exc:
            classification = classify_exception(exc.cause, phase="agent_stream")
            chat_executor._log_chat_failure(
                "Streaming chat requires rollback before automatic restart",
                vault_name=vault_name,
                session_id=session_id,
                model=prepared.model,
                tools=prepared.tools,
                streaming=True,
                phase="agent_stream",
                prompt_length=len(prepared.prompt_for_history),
                attached_image_count=prepared.attached_image_count,
                context_template=prepared.context_template,
                workspace_path=prepared.workspace_path,
                extra={
                    **chat_executor._summarize_tool_activity(tool_activity),
                    "task_id": task.task_id,
                    "strategy": "terminal_rollback_restart",
                },
                exc=exc.cause,
            )
            chat_executor._record_latest_turn_failure(
                session_id=session_id,
                vault_name=vault_name,
                exc=exc.cause,
                phase="agent_stream",
                streaming=True,
                model=prepared.model,
                tools=prepared.tools,
                chat_mode=prepared.chat_mode,
            )
            chat_executor.logger.info(
                "Primary chat awaiting task rollback",
                data={
                    "event": "chat_recovery_awaiting_rollback",
                    "status": "waiting",
                    "task_id": task.task_id,
                    "session_id": session_id,
                    "vault_name": vault_name,
                    "failure_kind": classification.failure_kind,
                },
            )
            raise
        except chat_executor.ChatCapabilityError as exc:
            chat_executor.logger.warning(
                "Streaming capability mismatch", data=exc.details
            )
            chat_executor._record_latest_turn_failure(
                session_id=session_id,
                vault_name=vault_name,
                exc=exc,
                phase="agent_stream",
                streaming=True,
                model=prepared.model,
                tools=prepared.tools,
                chat_mode=prepared.chat_mode,
            )
            await event_buffer.append(
                task.task_id,
                "error",
                _error_event_data(f"\n\nError: {str(exc)}", exc.details),
            )
            raise
        except ContextTemplateExecutionError as exc:
            details = build_context_template_error_details(
                vault_name=vault_name,
                session_id=session_id,
                template_name=exc.template_name,
                phase=exc.phase,
                template_pointer=exc.template_pointer,
            )
            chat_executor.logger.warning(
                "Streaming context template execution failure",
                data=details | {"error": str(exc)},
            )
            chat_executor._record_latest_turn_failure(
                session_id=session_id,
                vault_name=vault_name,
                exc=exc,
                phase="agent_stream",
                streaming=True,
                model=prepared.model,
                tools=prepared.tools,
                chat_mode=prepared.chat_mode,
            )
            await event_buffer.append(
                task.task_id,
                "error",
                _error_event_data(f"\n\nTemplate error: {str(exc)}", details),
            )
            raise
        except chat_executor.ChatContextTemplateError as exc:
            chat_executor.logger.warning(
                "Streaming context template failure", data=exc.details
            )
            chat_executor._record_latest_turn_failure(
                session_id=session_id,
                vault_name=vault_name,
                exc=exc,
                phase="agent_stream",
                streaming=True,
                model=prepared.model,
                tools=prepared.tools,
                chat_mode=prepared.chat_mode,
            )
            await event_buffer.append(
                task.task_id,
                "error",
                _error_event_data(f"\n\nTemplate error: {str(exc)}", exc.details),
            )
            raise
        except UsageLimitExceeded as exc:
            limit_error = chat_executor._build_chat_usage_limit_error(exc)
            chat_executor._log_chat_failure(
                f"Streaming chat {chat_executor._usage_limit_label(limit_error)} exceeded",
                vault_name=vault_name,
                session_id=session_id,
                model=prepared.model,
                tools=prepared.tools,
                streaming=True,
                phase="agent_stream",
                prompt_length=len(prepared.prompt_for_history),
                attached_image_count=prepared.attached_image_count,
                context_template=prepared.context_template,
                workspace_path=prepared.workspace_path,
                extra={
                    **chat_executor._summarize_tool_activity(tool_activity),
                    **limit_error.details,
                },
                exc=exc,
            )
            chat_executor._record_latest_turn_failure(
                session_id=session_id,
                vault_name=vault_name,
                exc=exc,
                phase="agent_stream",
                streaming=True,
                model=prepared.model,
                tools=prepared.tools,
                chat_mode=prepared.chat_mode,
            )
            await event_buffer.append(
                task.task_id,
                "error",
                _error_event_data(
                    (
                        f"\n\n{chat_executor._usage_limit_display_label(limit_error)} "
                        f"reached: {str(limit_error)}"
                    ),
                    limit_error.details,
                ),
            )
            raise limit_error from exc
        except Exception as exc:
            classification = classify_exception(exc, phase="agent_stream")
            chat_executor._log_chat_failure(
                "Streaming chat execution failed",
                vault_name=vault_name,
                session_id=session_id,
                model=prepared.model,
                tools=prepared.tools,
                streaming=True,
                phase="agent_stream",
                prompt_length=len(prepared.prompt_for_history),
                attached_image_count=prepared.attached_image_count,
                context_template=prepared.context_template,
                workspace_path=prepared.workspace_path,
                extra={
                    **chat_executor._summarize_tool_activity(tool_activity),
                    "task_id": task.task_id,
                },
                exc=exc,
            )
            chat_executor._record_latest_turn_failure(
                session_id=session_id,
                vault_name=vault_name,
                exc=exc,
                phase="agent_stream",
                streaming=True,
                model=prepared.model,
                tools=prepared.tools,
                chat_mode=prepared.chat_mode,
            )
            await event_buffer.append(
                task.task_id,
                "error",
                _error_event_data(
                    _stream_failure_display_message(classification.failure_kind),
                    classification.to_metadata(),
                ),
            )
            raise

    if final_result and deferred_review is None:
        await chat_executor._try_auto_compact_after_turn(
            session_id=session_id,
            vault_name=vault_name,
            vault_path=vault_path,
        )


async def _collect_chat_stream_attempt(
    *,
    prepared: chat_executor.PreparedChatExecution,
    user_prompt: chat_executor.PromptInput,
    message_history: list[ModelMessage] | None,
    run_deps: chat_executor.ChatRunDeps,
    usage: RunUsage,
    usage_limits: UsageLimits | None,
    task_id: str,
    event_buffer: ChatTaskEventBuffer,
    tool_activity: dict[str, dict[str, Any]],
    vault_name: str,
    session_id: str,
) -> tuple[Any, str]:
    """Collect one Pydantic chat stream attempt and publish provisional events."""
    final_result = None
    full_response = ""
    async with prepared.agent.run_stream_events(
        user_prompt,
        message_history=message_history,
        deferred_tool_results=prepared.deferred_tool_results,
        deps=run_deps,
        usage_limits=usage_limits,
        usage=usage,
        conversation_id=session_id,
    ) as stream_events:
        async for event in stream_events:
            if isinstance(event, PartStartEvent):
                if isinstance(event.part, TextPart) and event.part.content:
                    delta_text = event.part.content
                    full_response += delta_text
                    await event_buffer.append(
                        task_id, "delta", _delta_event_data(delta_text)
                    )
                elif isinstance(event.part, ThinkingPart) and event.part.content:
                    await event_buffer.append(
                        task_id,
                        "thinking_delta",
                        _thinking_delta_event_data(event.part.content),
                    )
            elif isinstance(event, PartDeltaEvent):
                if isinstance(event.delta, TextPartDelta):
                    delta_text = event.delta.content_delta
                    full_response += delta_text
                    await event_buffer.append(
                        task_id, "delta", _delta_event_data(delta_text)
                    )
                elif isinstance(event.delta, ThinkingPartDelta):
                    thinking_delta = event.delta.content_delta
                    if thinking_delta:
                        await event_buffer.append(
                            task_id,
                            "thinking_delta",
                            _thinking_delta_event_data(thinking_delta),
                        )
            elif isinstance(event, FunctionToolCallEvent):
                await _publish_tool_call_started(
                    task_id=task_id,
                    event=event,
                    event_buffer=event_buffer,
                    tool_activity=tool_activity,
                    vault_name=vault_name,
                    session_id=session_id,
                )
            elif isinstance(event, FunctionToolResultEvent):
                await _publish_tool_call_finished(
                    task_id=task_id,
                    event=event,
                    event_buffer=event_buffer,
                    tool_activity=tool_activity,
                    vault_name=vault_name,
                    session_id=session_id,
                )
            elif isinstance(event, AgentRunResultEvent):
                final_result = event.result
    return final_result, full_response


async def stream_chat_task_sse(
    *,
    task_id: str,
    event_buffer: ChatTaskEventBuffer | None = None,
    after_sequence: int = 0,
    keepalive_seconds: float = 15.0,
) -> AsyncIterator[str]:
    """Stream buffered chat task events as SSE chunks."""
    buffer = event_buffer or CHAT_TASK_EVENT_BUFFER
    iterator = buffer.subscribe(task_id, after_sequence=after_sequence).__aiter__()
    pending_event: asyncio.Task[Any] | None = None
    try:
        while True:
            if pending_event is None:
                pending_event = asyncio.ensure_future(iterator.__anext__())
            try:
                event = await asyncio.wait_for(
                    asyncio.shield(pending_event),
                    timeout=keepalive_seconds,
                )
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            except ChatTaskEventCursorExpired as exc:
                pending_event = None
                yield f"data: {json.dumps({
                    'event': 'chat_event_cursor_expired',
                    'task_id': task_id,
                    'after_sequence': exc.after_sequence,
                    'oldest_available_sequence': exc.oldest_available_sequence,
                    'latest_sequence': exc.latest_sequence,
                })}\n\n"
                return
            except StopAsyncIteration:
                pending_event = None
                return
            pending_event = None

            payload = dict(event.data)
            payload.setdefault("event", event.event)
            payload.setdefault("sequence", event.sequence)
            yield f"data: {json.dumps(payload)}\n\n"
    finally:
        if pending_event is not None and not pending_event.done():
            pending_event.cancel()


def _delta_event_data(delta_text: str) -> dict[str, Any]:
    return {
        "event": "delta",
        "choices": [
            {
                "delta": {"content": delta_text},
                "index": 0,
                "finish_reason": None,
            }
        ],
    }


def _thinking_delta_event_data(delta_text: str) -> dict[str, Any]:
    return {
        "event": "thinking_delta",
        "delta": {"content": delta_text},
    }


def _error_event_data(message: str, details: dict[str, Any]) -> dict[str, Any]:
    return {
        "event": "error",
        "choices": [
            {
                "delta": {"content": message},
                "index": 0,
                "finish_reason": "error",
            }
        ],
        "details": details,
    }


def _stream_failure_display_message(failure_kind: str) -> str:
    """Return concise user-facing copy for a classified stream failure."""
    if failure_kind == "provider_overloaded":
        return (
            "\n\nThe model service is temporarily overloaded. "
            "You can retry this interrupted turn."
        )
    if failure_kind == "provider_unavailable":
        return (
            "\n\nThe model service is temporarily unavailable. "
            "You can retry this interrupted turn."
        )
    if failure_kind == "rate_limited":
        return (
            "\n\nThe model service is temporarily rate-limited. "
            "You can retry this interrupted turn shortly."
        )
    if failure_kind in {"transient_network", "transient_provider"}:
        return (
            "\n\nThe connection to the model service was interrupted. "
            "You can retry this interrupted turn."
        )
    return "\n\nError: An unexpected error occurred"


async def _publish_tool_call_started(
    *,
    task_id: str,
    event: FunctionToolCallEvent,
    event_buffer: ChatTaskEventBuffer,
    tool_activity: dict[str, dict[str, Any]],
    vault_name: str,
    session_id: str,
) -> None:
    tool_id = event.tool_call_id
    tool_part = getattr(event, "part", None)
    tool_name = getattr(tool_part, "tool_name", "tool")
    tool_args = None
    if tool_part is not None:
        try:
            tool_args = tool_part.args_as_json_str()
        except Exception as exc:  # noqa: BLE001 - defensive: upstream variations
            chat_executor.logger.debug(
                "args_as_json_str failed; using raw args",
                data={"error": str(exc)},
            )
            tool_args = tool_part.args
    tool_activity[tool_id] = {
        "tool_name": tool_name,
        "status": "running",
    }
    payload = {
        "event": "tool_call_started",
        "tool_call_id": tool_id,
        "tool_name": tool_name,
    }
    chat_executor.logger.set_sinks(["validation"]).info(
        "Streaming tool call started",
        data={
            "event": "chat_tool_call_started",
            "vault_name": vault_name,
            "session_id": session_id,
            "tool_call_id": tool_id,
            "tool_name": tool_name,
            "arguments_length": len(tool_args or ""),
            "memory_rss_bytes": chat_executor._get_process_rss_bytes(),
        },
    )
    await event_buffer.append(task_id, "tool_call_started", payload)


async def _publish_tool_call_finished(
    *,
    task_id: str,
    event: FunctionToolResultEvent,
    event_buffer: ChatTaskEventBuffer,
    tool_activity: dict[str, dict[str, Any]],
    vault_name: str,
    session_id: str,
) -> None:
    tool_id = event.tool_call_id
    result_part = event.part
    tool_name = getattr(result_part, "tool_name", "tool")
    result_content = None
    try:
        if isinstance(result_part, ToolReturnPart):
            result_content = result_part.model_response_str()
        else:
            result_content = result_part.model_response()
    except Exception as exc:  # noqa: BLE001 - defensive fallback
        chat_executor.logger.debug(
            "Tool result rendering failed; using raw content",
            data={"error": str(exc)},
        )
        result_content = getattr(result_part, "content", None)
    result_metadata = _tool_result_event_metadata(result_part)
    outcome = str(getattr(result_part, "outcome", "success") or "success")
    terminal_state = classify_tool_result_state(
        outcome=outcome,
        metadata=result_metadata,
    )
    result_text = tool_result_as_text(result_content)
    metadata_token_count = result_metadata.get("token_count")
    token_count = (
        metadata_token_count
        if isinstance(metadata_token_count, int)
        and not isinstance(metadata_token_count, bool)
        and metadata_token_count >= 0
        else estimate_token_count(result_text)
    )
    tool_activity[tool_id] = {
        "tool_name": tool_name,
        "status": terminal_state,
        "token_count": token_count,
    }
    payload = {
        "event": "tool_call_finished",
        "tool_call_id": tool_id,
        "tool_name": tool_name,
        "outcome": outcome,
        "terminal_state": terminal_state,
        "token_count": token_count,
    }
    chat_executor.logger.set_sinks(["validation"]).info(
        "Streaming tool call finished",
        data={
            "event": "chat_tool_call_finished",
            "vault_name": vault_name,
            "session_id": session_id,
            "tool_call_id": tool_id,
            "tool_name": tool_name,
            "terminal_state": terminal_state,
            "failure_kind": result_metadata.get("failure_kind"),
            "result_length": len(result_text),
            "result_token_estimate": (
                estimate_token_count(result_text) if result_text else 0
            ),
            "memory_rss_bytes": chat_executor._get_process_rss_bytes(),
        },
    )
    await event_buffer.append(task_id, "tool_call_finished", payload)


_TOOL_RESULT_EVENT_METADATA_KEYS = (
    "token_count",
    "status",
    "state",
    "error_type",
    "failure_kind",
    "retryable",
    "phase",
    "suggested_action",
    "http_status",
    "retry_after",
    "limit_kind",
    "limit_setting",
    "limit",
)


def _tool_result_event_metadata(result_part: Any) -> dict[str, Any]:
    """Return the bounded result-state envelope safe for task events."""
    metadata = getattr(result_part, "metadata", None)
    if not isinstance(metadata, dict):
        return {}
    return {
        key: chat_executor._normalize_tool_detail(metadata[key])
        for key in _TOOL_RESULT_EVENT_METADATA_KEYS
        if key in metadata
    }
