"""Shared authorized submission for normal and browser-revised chat turns."""

from core.chat.executor import UploadedImageAttachment
from core.chat.task_execution import start_queued_chat_stream_task
from core.llm.thinking import normalize_thinking_value, thinking_value_to_label
from core.logger import UnifiedLogger

from ..exceptions import ChatSessionVaultMismatchError
from ..models import ChatTaskRequest, ChatTaskStartResponse
from .chat_sessions import (
    ChatSessionVaultMismatch,
    get_enabled_chat_tool_names,
    resolve_chat_session_for_request,
    set_chat_session_workspace,
)
from .execution_tasks import get_execution_task
from .vault_files import resolve_vault_root

logger = UnifiedLogger(tag="api-endpoints")


async def start_chat_task_request(
    chat_request: ChatTaskRequest,
    image_uploads: list[UploadedImageAttachment],
    *,
    display_prompt: str | None = None,
) -> ChatTaskStartResponse:
    """Start task-owned streaming chat execution."""
    vault_path = str(resolve_vault_root(chat_request.vault_name))
    enabled_tools = get_enabled_chat_tool_names()
    try:
        session_id = resolve_chat_session_for_request(
            requested_session_id=chat_request.session_id,
            vault_name=chat_request.vault_name,
        )
        if chat_request.workspace_path is not None:
            set_chat_session_workspace(
                chat_request.vault_name,
                session_id,
                chat_request.workspace_path,
            )
    except ChatSessionVaultMismatch as exc:
        raise ChatSessionVaultMismatchError(
            session_id=exc.session_id,
            requested_vault=exc.requested_vault,
            bound_vault=exc.bound_vault,
        ) from exc

    resolved_thinking = normalize_thinking_value(
        chat_request.thinking, source_name="chat thinking"
    )
    logger.info(
        "Chat task request accepted",
        data={
            "vault_name": chat_request.vault_name,
            "session_id": session_id,
            "streaming": True,
            "model": chat_request.model,
            "thinking": thinking_value_to_label(resolved_thinking),
            "tools": list(enabled_tools),
            "tools_count": len(enabled_tools),
            "prompt_length": len(chat_request.prompt),
            "image_path_count": len(chat_request.image_paths),
            "image_upload_count": len(image_uploads),
            "context_template": chat_request.context_template,
            "workspace_path": chat_request.workspace_path,
            "chat_mode": chat_request.chat_mode,
        },
    )
    started = await start_queued_chat_stream_task(
        vault_name=chat_request.vault_name,
        vault_path=vault_path,
        prompt=chat_request.prompt,
        display_prompt=display_prompt,
        image_paths=chat_request.image_paths,
        image_uploads=image_uploads,
        session_id=session_id,
        tools=enabled_tools,
        model=chat_request.model,
        thinking=resolved_thinking,
        context_template=chat_request.context_template,
        chat_mode=chat_request.chat_mode,
    )
    task = await get_execution_task(started.task.task_id)
    return ChatTaskStartResponse(session_id=session_id, task=task)
