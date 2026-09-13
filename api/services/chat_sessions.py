"""Chat-session persistence, summaries, export, and compaction API services."""

import json
import re
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic_ai.messages import ModelResponse, TextPart, ThinkingPart

from core.chat import export_chat_transcript, remove_chat_transcript_exports
from core.chat.chat_store import (
    StoredChatMessage,
    StoredChatSession,
    StoredChatToolEvent,
)
from core.chat.compaction import compact_chat_history, get_compaction_status
from core.chat.deferred_reviews import (
    StoredDeferredReview,
    get_pending_deferred_review,
)
from core.chat.workspace import normalize_workspace_path
from core.identity import require_current_execution_authority
from core.memory.session_summary import SessionSummary, SessionSummaryStore
from core.runtime.execution_tasks import (
    ExecutionTaskKind,
    ExecutionTaskSource,
    chat_session_scope,
    compaction_task_label,
)
from core.runtime.state import RuntimeStateError, get_runtime_context
from core.runtime.task_runner import ExecutionTaskSpec
from core.settings.store import (
    get_enabled_tool_names,
    get_enabled_tools_config,
)
from core.tools.failures import classify_tool_result_state
from core.tools.utils import estimate_token_count
from core.vault_state.pathing import (
    resolve_configured_vault_root,
    resolve_vault_relative_path,
)
from core.vector import VectorService

from ..exceptions import APIException
from ..models import (
    ChatHistoryCompactionResponse,
    ChatHistoryCompactionStatusResponse,
    ChatSessionDetailResponse,
    ChatSessionExportResponse,
    ChatSessionFailureInfo,
    ChatSessionForkResponse,
    ChatSessionInfo,
    ChatSessionMessageInfo,
    ChatSessionsPurgeResponse,
    ChatSessionToolCallInfo,
    ChatSessionToolEventInfo,
    ChatToolCallDetailResponse,
    ChatWorkspaceInfo,
    DeferredReviewCallInfo,
    DeferredReviewResponse,
)
from ..utils import generate_session_id
from .shared import chat_store as _chat_store
from .shared import logger


def get_enabled_chat_tool_names() -> list[str]:
    """Return app-wide enabled tools that may be exposed to chat agents."""
    configs = get_enabled_tools_config()
    return [
        name
        for name in get_enabled_tool_names()
        if name in configs and getattr(configs[name], "chat_visible", True)
    ]


class ChatSessionVaultMismatch(ValueError):
    """Raised when an existing chat session is requested under another vault."""

    def __init__(self, *, session_id: str, requested_vault: str, bound_vault: str):
        self.session_id = session_id
        self.requested_vault = requested_vault
        self.bound_vault = bound_vault
        super().__init__(
            f"Chat session '{session_id}' belongs to vault '{bound_vault}', "
            f"not vault '{requested_vault}'."
        )


def _require_chat_session_access(
    vault_name: str,
    session_id: str,
) -> StoredChatSession:
    """Resolve one session through the runtime-owned authorization boundary."""
    try:
        session = get_runtime_context().chat_session_access.require_session(session_id)
    except LookupError as exc:
        raise APIException(
            status_code=404,
            error_type="ChatSessionNotFound",
            message=f"Chat session not found: {session_id}",
            details={"session_id": session_id, "vault_name": vault_name},
        ) from exc
    if session.vault_name != vault_name:
        raise APIException(
            status_code=409,
            error_type="ChatSessionVaultMismatch",
            message=f"Chat session '{session_id}' belongs to another vault.",
            details={
                "session_id": session_id,
                "requested_vault": vault_name,
                "bound_vault": session.vault_name,
            },
        )
    return session


def resolve_chat_session_for_request(
    *, requested_session_id: str | None, vault_name: str
) -> str:
    """Return a session ID that is durably bound to the requested vault."""
    session_access = get_runtime_context().chat_session_access
    session_id = (requested_session_id or "").strip()
    if session_id:
        existing_session = session_access.get_session_by_id(session_id)
        if existing_session is not None:
            if existing_session.vault_name != vault_name:
                logger.warning(
                    "Rejected chat session vault mismatch",
                    data={
                        "session_id": session_id,
                        "requested_vault": vault_name,
                        "bound_vault": existing_session.vault_name,
                    },
                )
                raise ChatSessionVaultMismatch(
                    session_id=session_id,
                    requested_vault=vault_name,
                    bound_vault=existing_session.vault_name,
                )
            session_access.ensure_session(session_id, vault_name)
            return session_id
        session_access.ensure_session(session_id, vault_name)
        return session_id

    base_session_id = generate_session_id(vault_name)
    generated_session_id = base_session_id
    suffix = 1
    while session_access.session_id_exists(generated_session_id):
        suffix += 1
        generated_session_id = f"{base_session_id}_{suffix}"
    session_access.ensure_session(generated_session_id, vault_name)
    return generated_session_id


def _chat_workspace_info(vault_name: str, path: str | None) -> ChatWorkspaceInfo | None:
    normalized = (path or "").strip()
    if not normalized:
        return None
    exists = False
    try:
        runtime = get_runtime_context()
        vault_root = resolve_configured_vault_root(
            data_root=runtime.config.data_root,
            vault_name=vault_name,
        )
        workspace_path = resolve_vault_relative_path(
            vault_path=vault_root,
            path=normalized,
        )
        exists = workspace_path.is_dir()
    except (OSError, RuntimeStateError, ValueError):
        exists = False
    return ChatWorkspaceInfo(path=normalized, exists=exists)


def _normalize_workspace_path(path: str | None) -> str:
    """Normalize a safe vault-relative workspace path string."""
    try:
        return normalize_workspace_path(path)
    except ValueError as exc:
        message = str(exc)
        error_type = "InvalidWorkspacePath"
        if "relative to the vault" in message:
            details = {"path": path}
        elif "cannot contain '..'" in message:
            details = {"path": path}
        else:
            details = {"path": path}
        raise APIException(
            status_code=400,
            error_type=error_type,
            message=message,
            details=details,
        ) from exc


def _deferred_review_response(
    review: StoredDeferredReview,
) -> DeferredReviewResponse:
    """Translate a stored deferred review into its API representation."""
    return DeferredReviewResponse(
        artifact_ref=review.artifact_ref,
        artifact_kind="deferred_tool_review",
        vault_name=review.vault_name,
        session_id=review.session_id,
        originating_task_id=review.originating_task_id,
        status=review.status,
        approvals=[
            DeferredReviewCallInfo(
                tool_call_id=call.tool_call_id,
                tool_name=call.tool_name,
                args=call.args,
            )
            for call in review.requests.approvals
        ],
        calls=[
            DeferredReviewCallInfo(
                tool_call_id=call.tool_call_id,
                tool_name=call.tool_name,
                args=call.args,
            )
            for call in review.requests.calls
        ],
        created_at=review.created_at,
        submitted_at=review.submitted_at,
        resumed_task_id=review.resumed_task_id,
    )


def set_chat_session_workspace(
    vault_name: str, session_id: str, path: str | None
) -> ChatWorkspaceInfo | None:
    """Set or clear the workspace path for one chat session."""
    normalized_path = _normalize_workspace_path(path)
    _require_chat_session_access(vault_name, session_id)
    _chat_store.set_session_workspace(
        session_id=session_id,
        vault_name=vault_name,
        workspace_path=normalized_path or None,
    )
    logger.info(
        "Chat session workspace updated",
        data={
            "vault_name": vault_name,
            "session_id": session_id,
            "workspace_path": normalized_path,
            "workspace_set": bool(normalized_path),
        },
    )
    return _chat_workspace_info(vault_name, normalized_path)


def set_chat_session_mode(
    vault_name: str, session_id: str, chat_mode: str
) -> Literal["normal", "inline_edit"]:
    """Set the selected mode for an existing chat session."""
    _require_chat_session_access(vault_name, session_id)
    normalized: Literal["normal", "inline_edit"] = (
        "inline_edit" if str(chat_mode).strip().lower() == "inline_edit" else "normal"
    )
    _chat_store.set_session_chat_mode(
        session_id=session_id,
        vault_name=vault_name,
        chat_mode=normalized,
    )
    return normalized


def list_chat_sessions(vault_name: str) -> list[ChatSessionInfo]:
    """List persisted chat sessions for a vault ordered by latest activity."""
    sessions = get_runtime_context().chat_session_access.list_sessions(vault_name)
    summary_store = SessionSummaryStore()
    return [
        ChatSessionInfo(
            session_id=session.session_id,
            created_at=session.created_at,
            last_activity_at=session.last_activity_at,
            title=session.title or None,
            workspace=_chat_workspace_info(
                vault_name,
                _chat_store.get_session_workspace_path(session.session_id, vault_name),
            ),
            chat_mode=_chat_store.get_session_chat_mode(session.session_id, vault_name),
            has_summary=summary_store.get_session_summary(
                vault_name=vault_name,
                session_id=session.session_id,
            )
            is not None,
        )
        for session in sessions
    ]


def fork_chat_session(
    *,
    vault_name: str,
    source_session_id: str,
    through_sequence_index: int,
) -> ChatSessionForkResponse:
    """Create a new chat session from a source session prefix."""
    source_session = _require_chat_session_access(vault_name, source_session_id)

    source_messages = _chat_store.get_stored_messages(source_session_id, vault_name)
    highest_sequence = max(
        (message.sequence_index for message in source_messages), default=-1
    )
    if highest_sequence < 0:
        raise APIException(
            status_code=400,
            error_type="ChatSessionForkEmpty",
            message=f"Chat session has no messages to fork: {source_session_id}",
            details={"session_id": source_session_id, "vault_name": vault_name},
        )
    if through_sequence_index > highest_sequence:
        raise APIException(
            status_code=400,
            error_type="ChatSessionForkPointInvalid",
            message=(
                f"Fork point {through_sequence_index} is beyond the latest "
                f"effective message sequence {highest_sequence}."
            ),
            details={
                "session_id": source_session_id,
                "vault_name": vault_name,
                "through_sequence_index": through_sequence_index,
                "highest_sequence_index": highest_sequence,
            },
        )

    new_session_id = _generate_unique_chat_session_id(vault_name)
    new_title = _forked_session_title(source_session)
    copied_message_count = _chat_store.fork_session(
        source_session_id=source_session_id,
        new_session_id=new_session_id,
        vault_name=vault_name,
        through_sequence_index=through_sequence_index,
        title=new_title,
        metadata_update={
            "fork": {
                "source_session_id": source_session_id,
                "through_sequence_index": through_sequence_index,
                "created_at": datetime.now(UTC).isoformat(),
            }
        },
    )
    new_session = _chat_store.get_session(
        session_id=new_session_id, vault_name=vault_name
    )
    if new_session is None:  # pragma: no cover - defensive consistency check
        raise RuntimeError(f"Forked session was not persisted: {new_session_id}")

    logger.info(
        "Chat session forked",
        data={
            "vault_name": vault_name,
            "source_session_id": source_session_id,
            "new_session_id": new_session_id,
            "through_sequence_index": through_sequence_index,
            "copied_message_count": copied_message_count,
            "workspace_path": _chat_store.get_session_workspace_path(
                new_session_id, vault_name
            )
            or None,
        },
    )
    return ChatSessionForkResponse(
        session=ChatSessionInfo(
            session_id=new_session.session_id,
            created_at=new_session.created_at,
            last_activity_at=new_session.last_activity_at,
            title=new_session.title or None,
            workspace=_chat_workspace_info(
                vault_name,
                _chat_store.get_session_workspace_path(
                    new_session.session_id, vault_name
                ),
            ),
            chat_mode=_chat_store.get_session_chat_mode(
                new_session.session_id, vault_name
            ),
            has_summary=False,
        ),
        source_session_id=source_session_id,
        through_sequence_index=through_sequence_index,
        copied_message_count=copied_message_count,
    )


def _generate_unique_chat_session_id(vault_name: str) -> str:
    base_session_id = generate_session_id(vault_name)
    generated_session_id = base_session_id
    suffix = 1
    while _chat_store.get_session_by_id(generated_session_id) is not None:
        suffix += 1
        generated_session_id = f"{base_session_id}_{suffix}"
    return generated_session_id


def _forked_session_title(source_session: StoredChatSession) -> str:
    title = (source_session.title or "").strip()
    if title:
        return f"{title} (fork)"
    return f"Fork of {source_session.session_id}"


def get_chat_session_summary(vault_name: str, session_id: str) -> dict:
    """Return a lightweight summary preview for one chat session."""
    _require_chat_session_access(vault_name, session_id)
    session_summary = SessionSummaryStore().get_session_summary(
        vault_name=vault_name,
        session_id=session_id,
    )
    if session_summary is None:
        return {
            "session_id": session_id,
            "vault_name": vault_name,
            "has_summary": False,
            "summary": None,
            "user_intent": None,
            "created_at": None,
            "updated_at": None,
            "domain": None,
            "work_product": None,
            "workspace_path": _chat_store.get_session_workspace_path(
                session_id, vault_name
            )
            or None,
            "named_entities": None,
            "source_summary": None,
            "metadata": {},
            "artifacts": [],
            "vector_index": {
                "indexed_fields": 0,
                "expected_fields": 0,
                "indexed_field_types": [],
                "missing_field_types": [],
            },
        }
    return _session_summary_response(session_summary)


async def update_chat_session_summary(
    *,
    vault_name: str,
    session_id: str,
    data: dict[str, Any],
) -> dict:
    """Manually update one session summary record and refresh search indexes."""
    _require_chat_session_access(vault_name, session_id)
    store = SessionSummaryStore()
    existing = store.get_session_summary(vault_name=vault_name, session_id=session_id)
    if existing is None:
        raise APIException(
            status_code=404,
            error_type="SessionSummaryNotFound",
            message=f"Session summary not found: {session_id}",
            details={"session_id": session_id, "vault_name": vault_name},
        )
    previous = existing
    session_summary = store.update_session_summary_fields(
        vault_name=vault_name,
        session_id=session_id,
        summary=data.get("summary"),
        domain=data.get("domain"),
        work_product=data.get("work_product"),
        user_intent=data.get("user_intent"),
        workspace_path=data.get("workspace_path"),
        named_entities=data.get("named_entities"),
        source_summary=data.get("source_summary"),
        metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
    )
    try:
        indexed_fields = await _index_session_summary_for_api(
            store,
            vault_name=vault_name,
            session_id=session_id,
        )
    except Exception:
        _restore_session_summary_for_api(
            store,
            vault_name=vault_name,
            session_id=session_id,
            previous_summary=previous,
        )
        raise
    response = _session_summary_response(session_summary)
    response["indexed_fields"] = indexed_fields
    return response


def delete_chat_session_summary(vault_name: str, session_id: str) -> dict:
    """Delete one session summary record without deleting the chat session."""
    _require_chat_session_access(vault_name, session_id)
    deleted = SessionSummaryStore().delete_session_summary(
        vault_name=vault_name,
        session_id=session_id,
    )
    return {
        "session_id": session_id,
        "vault_name": vault_name,
        "deleted": deleted,
    }


async def _index_session_summary_for_api(
    store: SessionSummaryStore,
    *,
    vault_name: str,
    session_id: str,
) -> int:
    try:
        indexed_fields = await store.index_session_summary_fields(
            vault_name=vault_name,
            session_id=session_id,
            vector_service=VectorService(),
        )
        logger.info(
            "session_summary_field_indexing_completed",
            data={
                "source": "api",
                "vault_name": vault_name,
                "session_id": session_id,
                "indexed_fields": indexed_fields,
            },
        )
        return indexed_fields
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "session_summary_field_indexing_failed",
            data={
                "source": "api",
                "vault_name": vault_name,
                "session_id": session_id,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        raise APIException(
            status_code=500,
            error_type="SessionSummaryIndexingFailed",
            message=f"Failed to refresh session summary vector index for {session_id}",
            details={
                "session_id": session_id,
                "vault_name": vault_name,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        ) from exc


def _restore_session_summary_for_api(
    store: SessionSummaryStore,
    *,
    vault_name: str,
    session_id: str,
    previous_summary: SessionSummary,
) -> None:
    store.upsert_session_summary(
        vault_name=vault_name,
        session_id=session_id,
        title=previous_summary.title,
        summary=previous_summary.summary,
        domain=previous_summary.domain,
        work_product=previous_summary.work_product,
        user_intent=previous_summary.user_intent,
        named_entities=previous_summary.named_entities,
        source_summary=previous_summary.source_summary,
        workspace_path=previous_summary.workspace_path,
        metadata=previous_summary.metadata,
    )
    if previous_summary.artifacts:
        store.add_session_artifacts(
            vault_name=vault_name,
            session_id=session_id,
            artifacts=tuple(previous_summary.artifacts),
        )


def _session_summary_response(session_summary: SessionSummary) -> dict[str, Any]:
    return {
        "session_id": session_summary.session_id,
        "vault_name": session_summary.vault_name,
        "has_summary": True,
        "summary": session_summary.summary,
        "user_intent": session_summary.user_intent,
        "created_at": session_summary.created_at,
        "updated_at": session_summary.updated_at,
        "domain": session_summary.domain,
        "work_product": session_summary.work_product,
        "workspace_path": session_summary.workspace_path,
        "named_entities": session_summary.named_entities,
        "source_summary": session_summary.source_summary,
        "metadata": session_summary.metadata,
        "artifacts": [artifact.to_dict() for artifact in session_summary.artifacts],
        "vector_index": SessionSummaryStore().get_session_summary_vector_index_status(
            vault_name=session_summary.vault_name,
            session_id=session_summary.session_id,
        ),
    }


def get_chat_session_detail(
    vault_name: str, session_id: str
) -> ChatSessionDetailResponse:
    """Return persisted chat messages for one session."""
    _require_chat_session_access(vault_name, session_id)
    messages = _chat_store.get_stored_messages(session_id, vault_name)
    declaration_counts = _chat_store.get_tool_call_declaration_counts(
        session_id, vault_name
    )
    tool_events = _chat_store.get_tool_events(
        session_id, vault_name, committed_only=True
    )
    metadata = _chat_store.get_session_metadata(session_id, vault_name)
    latest_failure = _chat_session_failure_info(metadata.get("latest_turn_failure"))
    pending_review = get_pending_deferred_review(
        vault_name=vault_name, session_id=session_id
    )
    return ChatSessionDetailResponse(
        session_id=session_id,
        vault_name=vault_name,
        workspace=_chat_workspace_info(
            vault_name, _chat_store.get_session_workspace_path(session_id, vault_name)
        ),
        chat_mode=_chat_store.get_session_chat_mode(session_id, vault_name),
        pending_review=(
            _deferred_review_response(pending_review)
            if pending_review is not None
            else None
        ),
        latest_failure=latest_failure,
        messages=[_chat_session_message_info(message) for message in messages],
        tool_calls=_effective_tool_call_info(messages, declaration_counts, tool_events),
    )


def get_chat_tool_call_detail(
    vault_name: str,
    session_id: str,
    tool_call_id: str,
) -> ChatToolCallDetailResponse:
    """Return complete persisted detail for one session-owned tool call."""
    _require_chat_session_access(vault_name, session_id)
    messages = _chat_store.get_stored_messages(session_id, vault_name)
    effective_tool_call_ids = _effective_tool_call_ids(messages)
    declaration_counts = _chat_store.get_tool_call_declaration_counts(
        session_id, vault_name
    )
    if (
        tool_call_id not in effective_tool_call_ids
        or declaration_counts.get(tool_call_id) != 1
    ):
        _raise_chat_tool_call_not_found(session_id, tool_call_id)
    events = _chat_store.get_tool_events_for_call(
        session_id,
        vault_name,
        tool_call_id,
    )
    if not events:
        _raise_chat_tool_call_not_found(session_id, tool_call_id)
    if not _tool_call_events_are_unambiguous(events):
        logger.warning(
            "Ambiguous chat tool detail withheld",
            data={
                "session_id": session_id,
                "tool_call_id": tool_call_id,
                "call_event_count": sum(event.event_type == "call" for event in events),
            },
        )
        _raise_chat_tool_call_not_found(session_id, tool_call_id)

    args = next(
        (_load_json_object(event.args_json) for event in events if event.args_json),
        None,
    )
    result_event = next(
        (event for event in reversed(events) if event.event_type != "call"),
        None,
    )
    logger.debug(
        "Chat tool detail loaded",
        data={
            "session_id": session_id,
            "tool_call_id": tool_call_id,
            "event_count": len(events),
            "has_result": result_event is not None,
            "has_artifact_ref": bool(result_event and result_event.artifact_ref),
        },
    )
    return ChatToolCallDetailResponse(
        session_id=session_id,
        tool_call_id=tool_call_id,
        tool_name=events[0].tool_name,
        args=args,
        result_text=result_event.result_text if result_event else None,
        result_metadata=(
            _load_json_object(result_event.result_metadata_json) or {}
            if result_event
            else {}
        ),
        artifact_ref=result_event.artifact_ref if result_event else None,
        events=[_tool_event_info(event) for event in events],
    )


def _raise_chat_tool_call_not_found(session_id: str, tool_call_id: str) -> None:
    """Hide tool details that are absent from the effective chat history."""
    logger.debug(
        "Chat tool detail not found",
        data={"session_id": session_id, "tool_call_id": tool_call_id},
    )
    raise APIException(
        status_code=404,
        error_type="ChatToolCallNotFound",
        message=f"Tool call not found: {tool_call_id}",
        details={"session_id": session_id, "tool_call_id": tool_call_id},
    )


def _tool_event_info(event: StoredChatToolEvent) -> ChatSessionToolEventInfo:
    """Convert one stored tool event into the shared API representation."""
    return ChatSessionToolEventInfo(
        tool_call_id=event.tool_call_id,
        tool_name=event.tool_name,
        event_type=event.event_type,
        created_at=event.created_at,
        args=_load_json_object(event.args_json),
        result_text=event.result_text,
        result_metadata=_load_json_object(event.result_metadata_json) or {},
        artifact_ref=event.artifact_ref,
    )


def _tool_call_events_are_unambiguous(events: list[StoredChatToolEvent]) -> bool:
    """Return whether stored rows describe exactly one tool invocation."""
    return sum(event.event_type == "call" for event in events) == 1


def _effective_tool_call_info(
    messages: list[StoredChatMessage],
    declaration_counts: dict[str, int],
    events: list[StoredChatToolEvent],
) -> list[ChatSessionToolCallInfo]:
    """Return safe summaries for tool calls retained in effective history."""
    events_by_id: dict[str, list[StoredChatToolEvent]] = {}
    for event in events:
        events_by_id.setdefault(event.tool_call_id, []).append(event)

    summaries: list[ChatSessionToolCallInfo] = []
    for tool_call_id in _effective_tool_call_ids(messages):
        if declaration_counts.get(tool_call_id) != 1:
            continue
        call_events = events_by_id.get(tool_call_id)
        if not call_events or not _tool_call_events_are_unambiguous(call_events):
            continue
        summaries.append(
            ChatSessionToolCallInfo(
                tool_call_id=tool_call_id,
                tool_name=call_events[0].tool_name,
                status=_stored_tool_call_status(call_events),
                token_count=_stored_tool_call_token_count(call_events),
            )
        )
    return summaries


def _effective_tool_call_ids(messages: list[StoredChatMessage]) -> list[str]:
    """Return effective IDs that have both a call and its tool return."""
    ordered_ids: list[str] = []
    seen_ids: set[str] = set()
    returned_ids: set[str] = set()
    for message in messages:
        returned_ids.update(
            tool_call_id for tool_call_id in message.tool_return_ids if tool_call_id
        )
        for tool_call_id in message.tool_call_ids:
            if tool_call_id and tool_call_id not in seen_ids:
                seen_ids.add(tool_call_id)
                ordered_ids.append(tool_call_id)
    return [
        tool_call_id for tool_call_id in ordered_ids if tool_call_id in returned_ids
    ]


def _stored_tool_call_status(
    events: list[StoredChatToolEvent],
) -> Literal["completed", "failed", "interrupted"]:
    """Derive a settled UI state without exposing stored tool contents."""
    result_event = next(
        (event for event in reversed(events) if event.event_type != "call"),
        None,
    )
    if result_event is None:
        return "interrupted"
    return classify_tool_result_state(
        metadata=_load_json_object(result_event.result_metadata_json) or {}
    )


def _stored_tool_call_token_count(
    events: list[StoredChatToolEvent],
) -> int | None:
    """Return the stored estimated result size for a completed tool call."""
    result_event = next(
        (event for event in reversed(events) if event.event_type != "call"),
        None,
    )
    if result_event is None:
        return None
    metadata = _load_json_object(result_event.result_metadata_json) or {}
    value = metadata.get("token_count")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if result_event.result_text is None:
        return 0
    return estimate_token_count(result_event.result_text)


def _chat_session_message_info(message: StoredChatMessage) -> ChatSessionMessageInfo:
    """Return browser-safe display data while withholding tool message contents."""
    is_tool_message = (
        _is_tool_message_text(message.content_text)
        or bool(message.tool_call_ids)
        or bool(message.tool_return_ids)
    )
    is_model_response = isinstance(message.message, ModelResponse)
    return ChatSessionMessageInfo(
        sequence_index=message.sequence_index,
        fork_sequence_index=message.fork_sequence_index,
        role=message.role,
        content=(
            _chat_message_display_content(message)
            if is_model_response or not is_tool_message
            else ""
        ),
        thinking_content=(
            _chat_message_thinking_content(message) if is_model_response else ""
        ),
        message_type=message.message_type,
        direction=message.direction,
        is_tool_message=is_tool_message,
        tool_call_ids=list(message.tool_call_ids),
        tool_return_ids=list(message.tool_return_ids),
    )


def _chat_message_display_content(message: StoredChatMessage) -> str:
    """Return chat content for UI rendering without changing stored search text."""
    if not isinstance(message.message, ModelResponse):
        return str(message.content_text)

    text_parts: list[str] = []
    for part in getattr(message.message, "parts", []) or []:
        if isinstance(part, TextPart) and isinstance(part.content, str):
            content = part.content.strip()
            if content:
                text_parts.append(content)

    if not text_parts and message.tool_call_ids:
        return ""
    if not text_parts:
        return str(message.content_text)

    return "\n\n".join(text_parts)


def _chat_message_thinking_content(message: StoredChatMessage) -> str:
    """Return persisted provider thinking content separately from answer markdown."""
    if not isinstance(message.message, ModelResponse):
        return ""

    thinking_parts: list[str] = []
    for part in getattr(message.message, "parts", []) or []:
        if isinstance(part, ThinkingPart) and isinstance(part.content, str):
            content = part.content.strip()
            if content:
                thinking_parts.append(content)

    if not thinking_parts:
        return ""

    return _format_thinking_display_text("\n\n".join(thinking_parts))


def _format_thinking_display_text(text: str) -> str:
    """Light display cleanup for providers that stream sentence chunks without spaces."""
    return re.sub(r"""([.!?]["')\]]?)(?=[A-Z])""", r"\1 ", text)


def _chat_session_failure_info(value: Any) -> ChatSessionFailureInfo | None:
    if not isinstance(value, dict):
        return None
    if value.get("status") != "failed":
        return None
    try:
        return ChatSessionFailureInfo(
            status=str(value.get("status") or "failed"),
            phase=str(value.get("phase") or "unknown"),
            streaming=bool(value.get("streaming")),
            error_type=str(value.get("error_type") or "Error"),
            error=str(value.get("error") or ""),
            failure_kind=str(value.get("failure_kind") or ""),
            retryable=bool(value.get("retryable", False)),
            http_status=(
                None
                if value.get("http_status") is None
                else int(str(value.get("http_status")))
            ),
            retry_after=(
                None
                if value.get("retry_after") is None
                else str(value.get("retry_after"))
            ),
            model=None if value.get("model") is None else str(value.get("model")),
            tools=[str(item) for item in value.get("tools") or ()],
            accepted_user_sequence_index=int(
                str(value.get("accepted_user_sequence_index"))
            ),
            recorded_at=str(value.get("recorded_at") or ""),
            suggested_action=str(value.get("suggested_action") or ""),
            manual_retry_count=max(int(value.get("manual_retry_count") or 0), 0),
            last_manual_retry_task_id=(
                None
                if value.get("last_manual_retry_task_id") is None
                else str(value.get("last_manual_retry_task_id"))
            ),
            last_manual_retry_started_at=(
                None
                if value.get("last_manual_retry_started_at") is None
                else str(value.get("last_manual_retry_started_at"))
            ),
        )
    except (TypeError, ValueError):
        return None


def set_chat_session_title(vault_name: str, session_id: str, title: str | None) -> None:
    """Set or clear the user-defined title for a chat session."""
    _require_chat_session_access(vault_name, session_id)
    _chat_store.set_session_title(session_id, vault_name, title)


def export_chat_session_markdown(
    vault_name: str, vault_path: str, session_id: str
) -> ChatSessionExportResponse:
    """Export one chat session transcript to the vault on demand."""
    _require_chat_session_access(vault_name, session_id)
    session_summary = SessionSummaryStore().get_session_summary(
        vault_name=vault_name,
        session_id=session_id,
    )
    exported = export_chat_transcript(
        store=_chat_store,
        vault_path=vault_path,
        vault_name=vault_name,
        session_id=session_id,
        session_summary=session_summary.summary if session_summary else None,
    )
    return ChatSessionExportResponse(
        session_id=session_id,
        filename=exported.filename,
        path=exported.path,
    )


async def get_chat_history_compaction_status(
    vault_name: str,
    session_id: str,
) -> ChatHistoryCompactionStatusResponse:
    """Return compaction status for one chat session."""
    _require_chat_session_access(vault_name, session_id)
    status = await get_compaction_status(
        session_id=session_id,
        vault_name=vault_name,
        store=_chat_store,
    )
    return ChatHistoryCompactionStatusResponse(**asdict(status))


async def compact_chat_session_history(
    vault_name: str,
    vault_path: str,
    session_id: str,
    *,
    focus: str | None,
) -> ChatHistoryCompactionResponse:
    """Compact one chat session through the shared compaction service."""
    _require_chat_session_access(vault_name, session_id)
    runtime = get_runtime_context()
    result = await runtime.task_runner.run_inline(
        ExecutionTaskSpec(
            kind=ExecutionTaskKind.HISTORY_COMPACTION,
            scope=chat_session_scope(session_id),
            source=ExecutionTaskSource.API,
            label=compaction_task_label(session_id),
            authority=require_current_execution_authority(),
            metadata={"vault": vault_name, "session_id": session_id},
        ),
        lambda _task: compact_chat_history(
            session_id=session_id,
            vault_name=vault_name,
            vault_path=vault_path,
            focus=focus,
            source=ExecutionTaskSource.API,
            store=_chat_store,
        ),
    )
    return ChatHistoryCompactionResponse(**result.as_api_dict())


def delete_chat_session(vault_name: str, vault_path: str, session_id: str) -> None:
    """Delete one chat session and its session summary."""
    _require_chat_session_access(vault_name, session_id)
    del vault_path
    _chat_store.delete_sessions(vault_name, session_id=session_id)
    SessionSummaryStore().delete_session_summary(
        vault_name=vault_name, session_id=session_id
    )


def purge_chat_sessions(
    vault_name: str,
    vault_path: str,
    *,
    older_than_days: int | None,
) -> ChatSessionsPurgeResponse:
    """Delete old chat sessions and their transcript files for a vault."""
    sessions = get_runtime_context().chat_session_access.list_sessions(vault_name)
    if older_than_days is None:
        selected_ids = [session.session_id for session in sessions]
    else:
        cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
        selected_ids = [
            session.session_id
            for session in sessions
            if _stored_timestamp(session.last_activity_at) < cutoff
        ]
    deleted_ids: list[str] = []
    for session_id in selected_ids:
        deleted_ids.extend(
            _chat_store.delete_sessions(vault_name, session_id=session_id)
        )
    summary_store = SessionSummaryStore()
    for session_id in deleted_ids:
        summary_store.delete_session_summary(
            vault_name=vault_name, session_id=session_id
        )
    remove_chat_transcript_exports(vault_path=vault_path, session_ids=deleted_ids)

    n = len(deleted_ids)
    if n == 0:
        message = "No sessions matched."
    elif n == 1:
        message = "Deleted 1 session."
    else:
        message = f"Deleted {n} sessions."
    return ChatSessionsPurgeResponse(deleted=n, message=message)


def _stored_timestamp(value: str) -> datetime:
    """Parse a SQLite session timestamp as an aware UTC value."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _is_tool_message_text(content: str) -> bool:
    text = (content or "").strip()
    return text.startswith("[") and "]" in text


def _load_json_object(raw_value: str | None) -> dict[str, Any] | None:
    if not raw_value:
        return None
    try:
        parsed = json.loads(raw_value)
    except Exception:
        return {"raw": raw_value}
    if isinstance(parsed, dict):
        return parsed
    return {"value": parsed}
