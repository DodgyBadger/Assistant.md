"""
API endpoint implementations for the AssistantMD system.
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)
from pydantic_ai import BinaryContent
from starlette.datastructures import FormData, UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.import_models import (
    ImportJobCancelResponse,
    ImportJobInfo,
    ImportJobListResponse,
    ImportRunNowResponse,
    ImportScanRequest,
    ImportScanResponse,
    ImportUrlRequest,
    ImportUrlResponse,
)
from core.advanced_shell import AdvancedShellConfig
from core.advanced_shell.preflight import AdvancedShellPreflightService
from core.authentication import AuthenticationPolicy
from core.chat.executor import UploadedImageAttachment
from core.chat.task_events import ChatTaskEventCursorExpired
from core.chat.task_execution import (
    CHAT_TASK_EVENT_BUFFER,
    start_chat_turn_retry_task,
    start_queued_chat_stream_task,
    stream_chat_task_sse,
)
from core.ingestion.models import JobStatus
from core.llm.openai_oauth import OPENAI_OAUTH_LOOPBACK_REDIRECT_URI
from core.llm.thinking import normalize_thinking_value, thinking_value_to_label
from core.logger import UnifiedLogger
from core.mcp.oauth import resolve_mcp_oauth_redirect
from core.runtime.execution_tasks import TERMINAL_STATUS_VALUES
from core.runtime.state import RuntimeStateError, get_runtime_context
from core.settings import (
    get_chunking_max_image_bytes_per_image,
    get_chunking_max_image_bytes_total,
    get_chunking_max_image_mb_per_image,
    get_chunking_max_images_per_prompt,
    get_vault_upload_max_bytes_per_file,
    get_vault_upload_max_mb_per_file,
)
from core.vault_state.pathing import VaultRootResolutionError

from .exceptions import (
    APIException,
    ChatSessionVaultMismatchError,
)
from .models import (
    CachePurgeResponse,
    ChatHistoryCompactionRequest,
    ChatHistoryCompactionResponse,
    ChatHistoryCompactionStatusResponse,
    ChatSessionDetailResponse,
    ChatSessionExportRequest,
    ChatSessionExportResponse,
    ChatSessionForkRequest,
    ChatSessionForkResponse,
    ChatSessionInfo,
    ChatSessionModeRequest,
    ChatSessionModeResponse,
    ChatSessionRetryRequest,
    ChatSessionsPurgeRequest,
    ChatSessionsPurgeResponse,
    ChatSessionSummaryResponse,
    ChatSessionSummaryUpdateRequest,
    ChatSessionTitleRequest,
    ChatSessionWorkspaceRequest,
    ChatTaskRequest,
    ChatTaskStartResponse,
    ChatToolCallDetailResponse,
    ChatWorkspaceInfo,
    DeferredReviewResponse,
    DeferredReviewSubmitRequest,
    DeferredReviewSubmitResponse,
    EditProposalResponse,
    ExecuteWorkflowRequest,
    ExecuteWorkflowResponse,
    ExecutionTaskCancelResponse,
    ExecutionTaskInfo,
    ExecutionTaskListResponse,
    GoalCleanupRequest,
    GoalCleanupResponse,
    GoogleClientSecretUpdateRequest,
    GoogleConnectionCreateRequest,
    GoogleConnectionResponse,
    GoogleConnectionUpdateRequest,
    GoogleOAuthCompleteRequest,
    GoogleOAuthStartResponse,
    MCPConnectionCreateRequest,
    MCPConnectionImportRequest,
    MCPConnectionInfo,
    MCPConnectionTestResponse,
    MCPConnectionUpdateRequest,
    MCPCredentialUpdateRequest,
    MCPOAuthClientSecretUpdateRequest,
    MCPOAuthCompleteRequest,
    MCPOAuthStartRequest,
    MCPOAuthStartResponse,
    MCPOAuthStatusResponse,
    MetadataResponse,
    ModelConfigRequest,
    ModelInfo,
    OpenAIOAuthCompleteRequest,
    OpenAIOAuthDeviceCheckResponse,
    OpenAIOAuthDeviceStartResponse,
    OpenAIOAuthStartRequest,
    OpenAIOAuthStartResponse,
    OperationResult,
    ProviderConfigRequest,
    ProviderInfo,
    SecretInfo,
    SecretUpdateRequest,
    SettingInfo,
    SettingUpdateRequest,
    StatusResponse,
    SystemLogResponse,
    SystemMigrationRunRequest,
    SystemMigrationRunResponse,
    SystemMigrationStatusResponse,
    SystemSettingsResponse,
    SystemTemplateSeedResponse,
    TemplateInfo,
    UpdateSettingsRequest,
    VaultActivityResponse,
    VaultActivityRollbackPreviewResponse,
    VaultActivityRollbackRequest,
    VaultActivityRollbackResponse,
    VaultDirectoryListResponse,
    VaultFileReferenceListResponse,
    VaultFileResponse,
    VaultFileRevisionResponse,
    VaultFileRevisionRestoreRequest,
    VaultFileRevisionRestoreResponse,
    VaultFileUpdateRequest,
    VaultPathMutationRequest,
    VaultPathMutationResponse,
    VaultPathResolveRequest,
    VaultPathResolveResponse,
    VaultRescanRequest,
    VaultRescanResponse,
    VaultStateCleanupResponse,
    WorkflowEnabledRequest,
    WorkflowEnabledResponse,
    WorkflowFileResponse,
    WorkflowFileUpdateRequest,
    WorkflowLoadErrorsResponse,
    WorkflowRunHistoryResponse,
)
from .principal import use_request_authority
from .services import (
    ChatSessionVaultMismatch,
    cancel_chat_session_task,
    cancel_execution_task,
    cancel_import_job,
    check_openai_oauth_device_connection,
    cleanup_goals,
    cleanup_vault_state,
    compact_chat_session_history,
    complete_openai_oauth_callback,
    complete_openai_oauth_manual,
    delete_chat_session,
    delete_chat_session_summary,
    delete_configurable_model,
    delete_configurable_provider,
    delete_secret_entry,
    disconnect_openai_oauth_connection,
    execute_workflow_manually,
    export_chat_session_markdown,
    export_system_activity_log,
    fork_chat_session,
    get_active_chat_task,
    get_chat_deferred_review,
    get_chat_edit_proposal,
    get_chat_history_compaction_status,
    get_chat_session_detail,
    get_chat_session_summary,
    get_chat_tool_call_detail,
    get_configurable_models,
    get_configurable_providers,
    get_enabled_chat_tool_names,
    get_execution_task,
    get_general_settings_config,
    get_metadata,
    get_system_activity_log,
    get_system_database_migration_status,
    get_system_settings,
    get_system_status,
    get_vault_activity,
    get_vault_activity_rollback_preview,
    get_vault_file,
    get_vault_file_revisions,
    get_vault_snapshot_file,
    get_workflow_file,
    get_workflow_load_errors,
    get_workflow_run_history,
    import_url_direct,
    list_chat_sessions,
    list_context_templates,
    list_execution_tasks,
    list_recent_import_jobs,
    list_secrets,
    list_vault_directories,
    list_vault_file_references,
    list_workflow_tasks,
    mutate_vault_path,
    purge_chat_sessions,
    purge_expired_cache,
    refresh_system_authoring_templates,
    repair_settings_from_template,
    rescan_vaults_and_update_scheduler,
    resolve_chat_session_for_request,
    resolve_vault_path_references,
    resolve_vault_root,
    resolve_vault_upload_target,
    restore_vault_file_revision,
    rollback_vault_activity,
    run_system_database_migrations,
    scan_import_folder,
    set_chat_session_mode,
    set_chat_session_title,
    set_chat_session_workspace,
    set_workflow_enabled_state,
    start_openai_oauth_connection,
    start_openai_oauth_device_connection,
    submit_chat_deferred_review,
    trigger_import_queue_now,
    update_chat_session_summary,
    update_general_setting_value,
    update_secret,
    update_system_settings,
    update_vault_file,
    update_workflow_file,
    upload_vault_file,
    upsert_configurable_model,
    upsert_configurable_provider,
)
from .services.google_connections import (
    complete_google_oauth,
    create_google_connection,
    delete_google_connection,
    disconnect_google_oauth,
    get_google_connection,
    get_google_connection_by_id,
    list_google_connections,
    set_google_client_secret,
    start_google_oauth,
    update_google_connection,
    update_google_connection_by_id,
)
from .services.mcp import (
    clear_mcp_credential,
    complete_mcp_oauth,
    create_mcp_connection,
    delete_mcp_connection,
    disconnect_mcp_oauth,
    get_mcp_oauth_status,
    list_mcp_connections,
    parse_mcp_connection_import,
    set_mcp_credential,
    set_mcp_oauth_client_secret,
    start_mcp_oauth,
    test_mcp_connection,
    update_mcp_connection,
)
from .utils import create_error_response, serialize_exception

# Create API router
public_router = APIRouter(prefix="/api", tags=["Assistant.md public API"])
router = APIRouter(
    prefix="/api",
    tags=["Assistant.md API"],
    dependencies=[Depends(use_request_authority)],
)
logger = UnifiedLogger(tag="api-endpoints")
_CHAT_TASK_EVENT_KEEPALIVE_SECONDS = 15.0
_CHAT_UPLOAD_READ_CHUNK_SIZE = 1024 * 1024
_VAULT_UPLOAD_MULTIPART_OVERHEAD_BYTES = 64 * 1024


def _mcp_oauth_callback_page(*, success: bool) -> str:
    title = "MCP OAuth connected" if success else "MCP OAuth failed"
    message = (
        "Authorization completed. You can close this tab and return to Assistant.md."
        if success
        else "Authorization could not be completed. Close this tab and retry from Assistant.md."
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{title}</title></head><body>"
        f"<main><h1>{title}</h1><p>{message}</p></main>"
        "</body></html>"
    )


def _google_oauth_callback_page(*, success: bool) -> str:
    title = "Google account connected" if success else "Google OAuth failed"
    message = (
        "Authorization completed. You can close this tab and return to Assistant.md."
        if success
        else "Authorization could not be completed. Close this tab and retry from Assistant.md."
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{title}</title></head><body>"
        f"<main><h1>{title}</h1><p>{message}</p></main>"
        "</body></html>"
    )


def _looks_like_workflow_path(value: str) -> bool:
    """Return True when a workflow identifier looks like a file path instead of a workflow name."""
    normalized = value.strip().replace("\\", "/")
    return "/" in normalized or normalized.endswith((".md", ".markdown"))


async def _parse_chat_task_payload(
    request: Request,
) -> tuple[ChatTaskRequest, list[UploadedImageAttachment]]:
    """Parse chat request from JSON or multipart form-data."""
    content_type = (request.headers.get("content-type") or "").lower()
    if content_type.startswith("application/json"):
        payload = ChatTaskRequest.model_validate(await request.json())
        return payload, []

    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        image_paths = [
            str(item).strip()
            for item in form.getlist("image_paths")
            if str(item).strip()
        ]
        payload = ChatTaskRequest.model_validate(
            {
                "vault_name": str(form.get("vault_name") or "").strip(),
                "prompt": str(form.get("prompt") or "").strip(),
                "image_paths": image_paths,
                "session_id": str(form.get("session_id") or "").strip() or None,
                "model": str(form.get("model") or "").strip(),
                "thinking": str(form.get("thinking") or "").strip() or None,
                "context_template": str(form.get("context_template") or "").strip()
                or None,
                "workspace_path": str(form.get("workspace_path") or "").strip() or None,
                "chat_mode": str(form.get("chat_mode") or "normal").strip() or "normal",
            }
        )
        uploads: list[UploadedImageAttachment] = []
        image_items = [
            item for item in form.getlist("images") if isinstance(item, UploadFile)
        ]
        max_images = get_chunking_max_images_per_prompt()
        if max_images > 0 and len(image_items) > max_images:
            raise APIException(
                status_code=413,
                error_type="ChatImageUploadLimitExceeded",
                message=(
                    f"Too many image uploads ({len(image_items)}). "
                    f"Maximum per prompt is chunking_max_images_per_prompt={max_images}."
                ),
                details={
                    "image_count": len(image_items),
                    "max_images": max_images,
                },
            )

        max_total_bytes = get_chunking_max_image_bytes_total()
        total_image_bytes = 0
        for item in image_items:
            display_name = (
                str(getattr(item, "filename", None) or "uploaded-image").strip()
                or "uploaded-image"
            )
            raw_bytes = await _read_chat_image_upload(item, display_name=display_name)
            if not raw_bytes:
                continue
            total_image_bytes += len(raw_bytes)
            if max_total_bytes > 0 and total_image_bytes > max_total_bytes:
                raise APIException(
                    status_code=413,
                    error_type="ChatImageUploadTotalTooLarge",
                    message=(
                        f"Image uploads exceed the configured total limit "
                        f"({total_image_bytes} bytes)."
                    ),
                    details={
                        "max_total_bytes": max_total_bytes,
                        "total_image_bytes": total_image_bytes,
                    },
                )
            media_type = (
                str(getattr(item, "content_type", None) or "application/octet-stream")
                .strip()
                .lower()
            )
            uploads.append(
                UploadedImageAttachment(
                    display_name=display_name,
                    content=BinaryContent(data=raw_bytes, media_type=media_type),
                )
            )
        return payload, uploads

    raise ValueError(
        "Unsupported Content-Type for chat execution. Use application/json or multipart/form-data."
    )


async def _read_chat_image_upload(item: UploadFile, *, display_name: str) -> bytes:
    """Read one multipart image upload while enforcing the per-image byte limit."""
    max_image_bytes = get_chunking_max_image_bytes_per_image()
    chunks: list[bytes] = []
    total_bytes = 0
    while True:
        chunk = await item.read(_CHAT_UPLOAD_READ_CHUNK_SIZE)
        if not chunk:
            break
        total_bytes += len(chunk)
        if max_image_bytes > 0 and total_bytes > max_image_bytes:
            max_image_mb = get_chunking_max_image_mb_per_image()
            raise APIException(
                status_code=413,
                error_type="ChatImageUploadTooLarge",
                message=(
                    f"Image '{display_name}' is too large to attach ({total_bytes} bytes). "
                    f"Maximum per image is chunking_max_image_mb_per_image={max_image_mb} MB."
                ),
                details={
                    "display_name": display_name,
                    "max_image_bytes": max_image_bytes,
                    "size_bytes": total_bytes,
                },
            )
        chunks.append(chunk)
    return b"".join(chunks)


async def _read_vault_upload(
    upload: UploadFile,
    *,
    max_upload_bytes: int,
    max_upload_mb: int,
) -> bytes:
    """Read one vault upload while enforcing the API payload boundary."""
    chunks: list[bytes] = []
    total_bytes = 0
    while True:
        chunk = await upload.read(_CHAT_UPLOAD_READ_CHUNK_SIZE)
        if not chunk:
            break
        total_bytes += len(chunk)
        if total_bytes > max_upload_bytes:
            raise APIException(
                status_code=413,
                error_type="VaultUploadTooLarge",
                message=(
                    f"Upload exceeds the configured {max_upload_mb} MB per-file limit."
                ),
                details={
                    "filename": str(upload.filename or "")[:255],
                    "max_bytes": max_upload_bytes,
                    "size_bytes": total_bytes,
                    "setting": "vault_upload_max_mb_per_file",
                },
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _validate_vault_upload_content_length(
    request: Request,
    *,
    max_upload_bytes: int,
) -> None:
    """Reject a declared multipart body that cannot fit one configured upload."""
    raw_content_length = request.headers.get("content-length")
    if raw_content_length is None:
        return
    try:
        content_length = int(raw_content_length)
    except ValueError as exc:
        raise APIException(
            status_code=400,
            error_type="InvalidVaultUploadContentLength",
            message="Upload Content-Length must be a non-negative integer.",
        ) from exc
    if content_length < 0:
        raise APIException(
            status_code=400,
            error_type="InvalidVaultUploadContentLength",
            message="Upload Content-Length must be a non-negative integer.",
        )
    max_request_bytes = max_upload_bytes + _VAULT_UPLOAD_MULTIPART_OVERHEAD_BYTES
    if content_length > max_request_bytes:
        raise APIException(
            status_code=413,
            error_type="VaultUploadTooLarge",
            message="Upload request exceeds the configured per-file limit.",
            details={
                "max_bytes": max_upload_bytes,
                "request_bytes": content_length,
                "setting": "vault_upload_max_mb_per_file",
            },
        )


async def _parse_single_vault_upload(
    request: Request,
    *,
    max_upload_bytes: int,
) -> tuple[FormData, UploadFile]:
    """Parse exactly one multipart file and reject unrelated request parts."""
    content_type = (request.headers.get("content-type") or "").lower()
    if not content_type.startswith("multipart/form-data"):
        raise APIException(
            status_code=415,
            error_type="InvalidVaultUploadContentType",
            message="Vault uploads require multipart/form-data.",
        )
    try:
        form = await request.form(
            max_files=1,
            max_fields=0,
            max_part_size=min(max_upload_bytes, 1024 * 1024),
        )
    except StarletteHTTPException as exc:
        raise APIException(
            status_code=400,
            error_type="InvalidVaultUploadMultipart",
            message=str(exc.detail),
        ) from exc

    parts = form.multi_items()
    files = form.getlist("file")
    if (
        len(parts) != 1
        or len(files) != 1
        or parts[0][0] != "file"
        or not isinstance(files[0], UploadFile)
    ):
        await form.close()
        raise APIException(
            status_code=400,
            error_type="InvalidVaultUploadMultipart",
            message="Vault uploads require exactly one multipart file field named 'file'.",
        )
    return form, files[0]


async def _start_chat_task_request(
    chat_request: ChatTaskRequest,
    image_uploads: list[UploadedImageAttachment],
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


#######################################################################
## Health & Status Endpoints
#######################################################################


@public_router.get("/health")
async def health_check() -> JSONResponse:
    """
    Lightweight health check endpoint for Docker healthcheck and monitoring.

    Returns minimal JSON indicating system is responsive.
    Use /api/status for comprehensive system information.
    """
    try:
        # Just verify runtime context exists
        runtime = get_runtime_context()
        scheduler_running = runtime.scheduler.running if runtime.scheduler else False

        return JSONResponse(
            status_code=200,
            content={"status": "healthy", "scheduler_running": scheduler_running},
        )
    except RuntimeStateError:
        # Runtime not initialized yet - still starting up
        return JSONResponse(
            status_code=503, content={"status": "starting", "scheduler_running": False}
        )
    except Exception:
        # Something is wrong
        return JSONResponse(
            status_code=503, content={"status": "unhealthy", "scheduler_running": False}
        )


@router.get("/status", response_model=StatusResponse)
async def get_status(request: Request) -> StatusResponse | JSONResponse:
    """
    Get current system status including vault discovery, scheduler status, and system health.

    Returns comprehensive information about:
    - Discovered vaults and their workflow counts
    - Scheduler status and job information
    - System health indicators
    """
    try:
        # Try to get scheduler from runtime context
        scheduler = None
        try:
            runtime = get_runtime_context()
            scheduler = runtime.scheduler
        except RuntimeStateError:
            pass  # Runtime context not available - status will show scheduler as stopped

        # Get comprehensive system status
        authentication_policy = request.app.state.authentication_policy
        if not isinstance(authentication_policy, AuthenticationPolicy):
            raise RuntimeError("Authentication policy is unavailable.")
        advanced_shell_config = request.app.state.advanced_shell_config
        if not isinstance(advanced_shell_config, AdvancedShellConfig):
            raise RuntimeError("Advanced-shell configuration is unavailable.")
        advanced_shell_preflight = request.app.state.advanced_shell_preflight
        if not isinstance(advanced_shell_preflight, AdvancedShellPreflightService):
            raise RuntimeError("Advanced-shell preflight is unavailable.")
        status = await get_system_status(
            scheduler,
            authentication_mode=authentication_policy.mode,
            advanced_shell_config=advanced_shell_config,
            advanced_shell_preflight=await advanced_shell_preflight.status(),
        )
        return status

    except Exception as e:
        return create_error_response(e)


@router.get("/system/activity-log", response_model=SystemLogResponse)
async def system_activity_log(
    limit: int = 200,
    cursor: str | None = None,
    level: list[str] | None = Query(None),
    tag: list[str] | None = Query(None),
    search: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> SystemLogResponse | JSONResponse:
    """Retrieve one filtered page from retained System Activity."""
    try:
        return await get_system_activity_log(
            limit=limit,
            cursor=cursor,
            levels=tuple(level or ()),
            tags=tuple(tag or ()),
            search=search,
            start_time=start_time,
            end_time=end_time,
        )
    except Exception as e:
        return create_error_response(e)


@router.get("/system/activity-log/export")
async def system_activity_log_export() -> StreamingResponse:
    """Download all retained raw System Activity JSONL segments."""
    return StreamingResponse(
        export_system_activity_log(),
        media_type="application/x-ndjson",
        headers={
            "Content-Disposition": 'attachment; filename="assistantmd-activity.jsonl"'
        },
    )


#######################################################################
## Execution Task Endpoints
#######################################################################


@router.get("/tasks", response_model=ExecutionTaskListResponse)
async def execution_tasks(
    kind: str | None = None,
    scope: str | None = None,
    include_terminal: bool = True,
) -> ExecutionTaskListResponse | JSONResponse:
    """List process-local execution task snapshots."""
    try:
        return await list_execution_tasks(
            kind=kind,
            scope=scope,
            include_terminal=include_terminal,
        )
    except Exception as e:
        return create_error_response(e)


@router.get("/tasks/{task_id}", response_model=ExecutionTaskInfo)
async def execution_task(task_id: str) -> ExecutionTaskInfo | JSONResponse:
    """Return one process-local execution task snapshot."""
    try:
        return await get_execution_task(task_id)
    except Exception as e:
        return create_error_response(e)


@router.post("/tasks/{task_id}/cancel", response_model=ExecutionTaskCancelResponse)
async def cancel_task(task_id: str) -> ExecutionTaskCancelResponse | JSONResponse:
    """Request cancellation for one process-local execution task."""
    try:
        return await cancel_execution_task(task_id)
    except Exception as e:
        return create_error_response(e)


@router.get("/chat/tasks/{task_id}/events", response_model=None)
async def chat_task_events(
    task_id: str, after_sequence: int = 0
) -> StreamingResponse | JSONResponse:
    """Stream buffered process-local chat task events as SSE."""
    try:
        task = await get_execution_task(task_id)
        if task.kind != "chat":
            raise APIException(
                status_code=404,
                error_type="ExecutionTaskNotFound",
                message=f"Chat execution task not found: {task_id}",
                details={"task_id": task_id},
            )
        if (
            task.status in TERMINAL_STATUS_VALUES
            and not await CHAT_TASK_EVENT_BUFFER.has_stream(task_id)
        ):
            raise APIException(
                status_code=410,
                error_type="ChatTaskEventsExpired",
                message=f"Chat task events are no longer retained: {task_id}",
                details={"task_id": task_id},
            )
        try:
            await CHAT_TASK_EVENT_BUFFER.ensure_cursor_available(
                task_id,
                after_sequence=after_sequence,
            )
        except ChatTaskEventCursorExpired as exc:
            raise APIException(
                status_code=410,
                error_type="ChatTaskEventCursorExpired",
                message="Chat task events are no longer retained from the requested cursor.",
                details={
                    "task_id": task_id,
                    "after_sequence": exc.after_sequence,
                    "oldest_available_sequence": exc.oldest_available_sequence,
                    "latest_sequence": exc.latest_sequence,
                },
            ) from exc
        return StreamingResponse(
            stream_chat_task_sse(
                task_id=task_id,
                after_sequence=after_sequence,
                keepalive_seconds=_CHAT_TASK_EVENT_KEEPALIVE_SECONDS,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "Content-Encoding": "identity",
                "X-Accel-Buffering": "no",
                "X-Task-ID": task_id,
                "X-Session-ID": str(task.metadata.get("session_id") or ""),
            },
        )
    except Exception as e:
        return create_error_response(e)


@router.post("/chat/tasks", response_model=ChatTaskStartResponse)
async def start_chat_task(
    request: Request,
) -> ChatTaskStartResponse | JSONResponse:
    """Start task-owned streaming chat execution and return its task snapshot."""
    try:
        chat_request, image_uploads = await _parse_chat_task_payload(request)
        return await _start_chat_task_request(chat_request, image_uploads)
    except Exception as e:
        if isinstance(e, APIException):
            logger.warning(
                "Chat task endpoint request rejected",
                data={
                    "path": str(request.url.path),
                    "method": request.method,
                    "error_type": e.error_type,
                    "message": str(e.detail),
                    "details": e.details,
                },
            )
            return create_error_response(e)
        logger.error(
            "Chat task endpoint request failed",
            data={
                "path": str(request.url.path),
                "method": request.method,
                **serialize_exception(e),
            },
        )
        return create_error_response(e)


@router.get("/system/settings", response_model=SystemSettingsResponse)
async def system_settings() -> SystemSettingsResponse | JSONResponse:
    """Return the current settings configuration file."""
    try:
        return await get_system_settings()
    except Exception as e:
        return create_error_response(e)


@router.put("/system/settings", response_model=SystemSettingsResponse)
async def update_system_settings_endpoint(
    request: UpdateSettingsRequest,
) -> SystemSettingsResponse | JSONResponse:
    """Validate and persist updated settings YAML content."""
    try:
        return await update_system_settings(request.content)
    except Exception as e:
        return create_error_response(e)


@router.post("/system/settings/repair", response_model=SystemSettingsResponse)
async def repair_system_settings() -> SystemSettingsResponse | JSONResponse:
    """Merge missing settings from template into active settings (with backup)."""
    try:
        return repair_settings_from_template()
    except Exception as e:
        return create_error_response(e)


@router.get("/system/settings/general", response_model=list[SettingInfo])
async def list_general_settings() -> list[SettingInfo] | JSONResponse:
    """List general (non-secret) settings entries."""
    try:
        return get_general_settings_config()
    except Exception as e:
        return create_error_response(e)


@router.put("/system/settings/general/{setting_key}", response_model=SettingInfo)
async def update_general_setting(
    setting_key: str, request: SettingUpdateRequest
) -> SettingInfo | JSONResponse:
    """Update a general setting value."""
    try:
        return update_general_setting_value(setting_key, request)
    except Exception as e:
        return create_error_response(e)


#######################################################################
## Import Endpoints
#######################################################################


def _import_job_info(job: Any, *, fallback_vault: str = "") -> ImportJobInfo:
    """Project one durable ingestion job into the public import contract."""
    return ImportJobInfo(
        id=job.id,
        source_uri=job.source_uri,
        vault=job.vault or fallback_vault,
        source_type=job.source_type,
        status=job.status,
        error=job.error,
        outputs=job.outputs,
        selected_strategy=job.selected_strategy,
        selected_provider=job.selected_provider,
        selected_model=job.selected_model,
        strategy_attempts=job.strategy_attempts,
        fallback_reason=job.fallback_reason,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _ocr_options_from_request(request: Any) -> dict[str, object]:
    return {
        key: value
        for key, value in {
            "include_ocr_blocks": request.include_ocr_blocks,
            "ocr_table_format": request.ocr_table_format,
            "extract_ocr_header": request.extract_ocr_header,
            "extract_ocr_footer": request.extract_ocr_footer,
            "ocr_confidence": request.ocr_confidence,
        }.items()
        if value is not None
    }


@router.get("/import/jobs", response_model=ImportJobListResponse)
async def import_jobs(
    limit: int = Query(25, ge=1, le=100),
    vault: str | None = None,
    status: list[JobStatus] | None = Query(None),
    cursor: str | None = None,
) -> ImportJobListResponse | JSONResponse:
    """List recent durable ingestion jobs for queue observability."""
    try:
        jobs, next_cursor, total_matching, status_counts = list_recent_import_jobs(
            limit=limit,
            vault=vault,
            statuses=status,
            cursor=cursor,
        )
        return ImportJobListResponse(
            jobs=[_import_job_info(job) for job in jobs],
            next_cursor=next_cursor,
            total_matching=total_matching,
            status_counts=status_counts,
        )
    except ValueError as e:
        return create_error_response(
            APIException(
                status_code=400,
                error_type="InvalidImportJobCursor",
                message=str(e),
            )
        )
    except Exception as e:
        return create_error_response(e)


@router.post(
    "/import/jobs/{job_id}/cancel",
    response_model=ImportJobCancelResponse,
)
async def cancel_import(job_id: int) -> ImportJobCancelResponse | JSONResponse:
    """Cancel one ingestion job if it has not started processing."""
    try:
        job = cancel_import_job(job_id)
        return ImportJobCancelResponse(job=_import_job_info(job), cancelled=True)
    except ValueError as e:
        message = str(e)
        return create_error_response(
            APIException(
                status_code=404 if "not found" in message else 409,
                error_type=(
                    "IngestionJobNotFound"
                    if "not found" in message
                    else "IngestionJobNotCancellable"
                ),
                message=message,
                details={"job_id": job_id},
            )
        )
    except Exception as e:
        return create_error_response(e)


@router.post("/import/run-now", response_model=ImportRunNowResponse)
async def run_import_queue_now() -> ImportRunNowResponse | JSONResponse:
    """Advance the scheduler-owned ingestion worker to run immediately."""
    try:
        queued_count, triggered_at = trigger_import_queue_now()
        return ImportRunNowResponse(
            accepted=True,
            queued_count=queued_count,
            triggered_at=triggered_at,
        )
    except Exception as e:
        return create_error_response(e)


@router.post("/import/scan", response_model=ImportScanResponse)
async def import_scan(
    request: ImportScanRequest,
) -> ImportScanResponse | JSONResponse:
    try:
        jobs, skipped = await scan_import_folder(
            vault=request.vault,
            queue_only=request.queue_only,
            strategies=request.strategies,
            capture_ocr_images=request.capture_ocr_images,
            pdf_mode=request.pdf_mode,
            ocr_options=_ocr_options_from_request(request),
        )
        job_infos = [
            _import_job_info(job, fallback_vault=request.vault) for job in jobs
        ]
        return ImportScanResponse(jobs_created=job_infos, skipped=skipped)
    except VaultRootResolutionError as e:
        return create_error_response(
            APIException(
                status_code=404 if e.code == "vault_not_found" else 400,
                error_type="InvalidImportVault",
                message=str(e),
                details={"vault_name": e.vault_name},
            )
        )
    except ValueError as e:
        return create_error_response(
            APIException(
                status_code=400,
                error_type="InvalidImportRequest",
                message=str(e),
            )
        )
    except Exception as e:
        return create_error_response(e)


@router.post("/import/url", response_model=ImportUrlResponse)
async def import_url(
    request: ImportUrlRequest,
) -> ImportUrlResponse | JSONResponse:
    try:
        job = await import_url_direct(
            vault=request.vault,
            url=request.url,
            clean_html=request.clean_html,
            strategies=request.strategies,
            pdf_strategies=request.pdf_strategies,
            capture_ocr_images=request.capture_ocr_images,
            pdf_mode=request.pdf_mode,
            ocr_options=_ocr_options_from_request(request),
        )
        return ImportUrlResponse(
            **_import_job_info(job, fallback_vault=request.vault).model_dump()
        )
    except (ValueError, VaultRootResolutionError) as e:
        return create_error_response(
            APIException(
                status_code=400,
                error_type="InvalidImportRequest",
                message=str(e),
            )
        )
    except Exception as e:
        return create_error_response(e)


@router.get("/system/models", response_model=list[ModelInfo])
async def list_models() -> list[ModelInfo] | JSONResponse:
    """List all model configuration entries with availability metadata."""
    try:
        return get_configurable_models()
    except Exception as e:
        return create_error_response(e)


@router.put("/system/models/{model_name}", response_model=ModelInfo)
async def upsert_model(
    model_name: str, request: ModelConfigRequest
) -> ModelInfo | JSONResponse:
    """Create or update a model configuration entry."""
    try:
        return upsert_configurable_model(model_name, request)
    except Exception as e:
        return create_error_response(e)


@router.delete("/system/models/{model_name}", response_model=OperationResult)
async def delete_model(model_name: str) -> OperationResult | JSONResponse:
    """Delete a user-editable model configuration entry."""
    try:
        return delete_configurable_model(model_name)
    except Exception as e:
        return create_error_response(e)


@router.get("/system/providers", response_model=list[ProviderInfo])
async def list_providers() -> list[ProviderInfo] | JSONResponse:
    """List provider configurations."""
    try:
        return get_configurable_providers()
    except Exception as e:
        return create_error_response(e)


@router.post(
    "/system/providers/openai/oauth/start",
    response_model=OpenAIOAuthStartResponse,
)
async def start_openai_oauth(
    payload: OpenAIOAuthStartRequest,
) -> OpenAIOAuthStartResponse | JSONResponse:
    """Start an OpenAI OAuth connection attempt."""
    try:
        return start_openai_oauth_connection(
            payload,
            default_redirect_uri=OPENAI_OAUTH_LOOPBACK_REDIRECT_URI,
        )
    except Exception as e:
        return create_error_response(e)


@router.post(
    "/system/providers/openai/oauth/device/start",
    response_model=OpenAIOAuthDeviceStartResponse,
)
async def start_openai_oauth_device() -> OpenAIOAuthDeviceStartResponse | JSONResponse:
    """Start an OpenAI OAuth device-code connection attempt."""
    try:
        return await start_openai_oauth_device_connection()
    except Exception as e:
        return create_error_response(e)


@router.post(
    "/system/providers/openai/oauth/device/check",
    response_model=OpenAIOAuthDeviceCheckResponse,
)
async def check_openai_oauth_device() -> OpenAIOAuthDeviceCheckResponse | JSONResponse:
    """Check an OpenAI OAuth device-code connection attempt."""
    try:
        return await check_openai_oauth_device_connection()
    except Exception as e:
        return create_error_response(e)


@router.get("/system/providers/openai/oauth/callback", response_model=ProviderInfo)
async def complete_openai_oauth_callback_endpoint(
    code: str, state: str
) -> ProviderInfo | JSONResponse:
    """Complete OpenAI OAuth from callback query parameters."""
    try:
        return await complete_openai_oauth_callback(code=code, state=state)
    except Exception as e:
        return create_error_response(e)


@router.post("/system/providers/openai/oauth/complete", response_model=ProviderInfo)
async def complete_openai_oauth_manual_endpoint(
    request: OpenAIOAuthCompleteRequest,
) -> ProviderInfo | JSONResponse:
    """Complete OpenAI OAuth from a pasted redirect URL or code/state pair."""
    try:
        return await complete_openai_oauth_manual(request)
    except Exception as e:
        return create_error_response(e)


@router.get("/system/providers/openai/oauth/status", response_model=ProviderInfo)
async def get_openai_oauth_status_endpoint() -> ProviderInfo | JSONResponse:
    """Return OpenAI provider status including sanitized OAuth metadata."""
    try:
        return next(
            provider
            for provider in get_configurable_providers()
            if provider.name == "openai"
        )
    except Exception as e:
        return create_error_response(e)


@router.delete("/system/providers/openai/oauth", response_model=OperationResult)
async def disconnect_openai_oauth_endpoint() -> OperationResult | JSONResponse:
    """Disconnect OpenAI OAuth without changing provider auth mode."""
    try:
        return disconnect_openai_oauth_connection()
    except Exception as e:
        return create_error_response(e)


@router.put("/system/providers/{provider_name}", response_model=ProviderInfo)
async def upsert_provider(
    provider_name: str, request: ProviderConfigRequest
) -> ProviderInfo | JSONResponse:
    """Create or update a provider configuration."""
    try:
        return upsert_configurable_provider(provider_name, request)
    except Exception as e:
        return create_error_response(e)


@router.delete("/system/providers/{provider_name}", response_model=OperationResult)
async def delete_provider(provider_name: str) -> OperationResult | JSONResponse:
    """Delete a user-editable provider configuration."""
    try:
        return delete_configurable_provider(provider_name)
    except Exception as e:
        return create_error_response(e)


@router.get("/system/secrets", response_model=list[SecretInfo])
async def list_secrets_endpoint() -> list[SecretInfo] | JSONResponse:
    """List stored secrets and whether they currently have values."""
    try:
        return list_secrets()
    except Exception as e:
        return create_error_response(e)


@router.put("/system/secrets", response_model=OperationResult)
async def set_secret_endpoint(
    request: SecretUpdateRequest,
) -> OperationResult | JSONResponse:
    """Create, update, or clear a stored secret."""
    try:
        return update_secret(request)
    except Exception as e:
        return create_error_response(e)


@router.delete("/system/secrets/{secret_name}", response_model=OperationResult)
async def delete_secret_endpoint(secret_name: str) -> OperationResult | JSONResponse:
    """Delete a stored secret entry entirely."""
    try:
        return delete_secret_entry(secret_name)
    except Exception as e:
        return create_error_response(e)


@router.get(
    "/system/connections/google",
    response_model=GoogleConnectionResponse,
)
async def get_google_connection_endpoint() -> GoogleConnectionResponse | JSONResponse:
    """Return the current principal's sanitized Google connection."""
    try:
        return get_google_connection()
    except Exception as exc:
        return create_error_response(exc)


@router.put(
    "/system/connections/google",
    response_model=GoogleConnectionResponse,
)
async def update_google_connection_endpoint(
    payload: GoogleConnectionUpdateRequest,
) -> GoogleConnectionResponse | JSONResponse:
    """Create or update Google OAuth client metadata and Gmail preferences."""
    try:
        return update_google_connection(payload)
    except Exception as exc:
        return create_error_response(exc)


@router.put(
    "/system/connections/google/client-secret",
    response_model=GoogleConnectionResponse,
)
async def set_google_client_secret_endpoint(
    payload: GoogleClientSecretUpdateRequest,
) -> GoogleConnectionResponse | JSONResponse:
    """Set the write-only Google OAuth client secret."""
    try:
        return set_google_client_secret(payload)
    except Exception as exc:
        return create_error_response(exc)


@router.post(
    "/system/connections/google/oauth/start",
    response_model=GoogleOAuthStartResponse,
)
async def start_google_oauth_endpoint() -> GoogleOAuthStartResponse | JSONResponse:
    """Start Google OAuth for the Gmail read capability."""
    try:
        return start_google_oauth()
    except Exception as exc:
        return create_error_response(exc)


@router.get("/system/connections/google/oauth/callback")
async def complete_google_oauth_callback_endpoint(
    code: str | None = Query(None),
    state: str | None = Query(None),
) -> HTMLResponse:
    """Complete Google OAuth from its registered browser callback."""
    try:
        await complete_google_oauth(
            GoogleOAuthCompleteRequest(redirect_url=None, code=code, state=state)
        )
        return HTMLResponse(_google_oauth_callback_page(success=True))
    except Exception as exc:
        logger.warning(
            "Google OAuth browser callback failed",
            data={
                "event": "google_oauth_callback_failed",
                "error_type": type(exc).__name__,
            },
        )
        return HTMLResponse(_google_oauth_callback_page(success=False), status_code=400)


@router.post(
    "/system/connections/google/oauth/complete",
    response_model=GoogleConnectionResponse,
)
async def complete_google_oauth_manual_endpoint(
    payload: GoogleOAuthCompleteRequest,
) -> GoogleConnectionResponse | JSONResponse:
    """Complete Google OAuth using a pasted callback URL."""
    try:
        return await complete_google_oauth(payload)
    except Exception as exc:
        return create_error_response(exc)


@router.delete(
    "/system/connections/google/oauth",
    response_model=OperationResult,
)
async def disconnect_google_oauth_endpoint() -> OperationResult | JSONResponse:
    """Disconnect the Google account while preserving client configuration."""
    try:
        return disconnect_google_oauth()
    except Exception as exc:
        return create_error_response(exc)


@router.get(
    "/system/connections/google/connections",
    response_model=list[GoogleConnectionResponse],
)
async def list_google_connections_endpoint() -> (
    list[GoogleConnectionResponse] | JSONResponse
):
    """List the current principal's Google connections."""
    try:
        return list_google_connections()
    except Exception as exc:
        return create_error_response(exc)


@router.post(
    "/system/connections/google/connections",
    response_model=GoogleConnectionResponse,
)
async def create_google_connection_endpoint(
    payload: GoogleConnectionCreateRequest,
) -> GoogleConnectionResponse | JSONResponse:
    """Create a named Google connection for the current principal."""
    try:
        return create_google_connection(payload)
    except Exception as exc:
        return create_error_response(exc)


@router.get(
    "/system/connections/google/connections/{connection_id}",
    response_model=GoogleConnectionResponse,
)
async def get_google_connection_by_id_endpoint(
    connection_id: str,
) -> GoogleConnectionResponse | JSONResponse:
    """Return one current-principal Google connection."""
    try:
        return get_google_connection_by_id(connection_id)
    except Exception as exc:
        return create_error_response(exc)


@router.put(
    "/system/connections/google/connections/{connection_id}",
    response_model=GoogleConnectionResponse,
)
async def update_google_connection_by_id_endpoint(
    connection_id: str,
    payload: GoogleConnectionUpdateRequest,
) -> GoogleConnectionResponse | JSONResponse:
    """Update one named Google connection."""
    try:
        return update_google_connection_by_id(connection_id, payload)
    except Exception as exc:
        return create_error_response(exc)


@router.put(
    "/system/connections/google/connections/{connection_id}/client-secret",
    response_model=GoogleConnectionResponse,
)
async def set_google_client_secret_by_id_endpoint(
    connection_id: str,
    payload: GoogleClientSecretUpdateRequest,
) -> GoogleConnectionResponse | JSONResponse:
    """Set the write-only client secret for one Google connection."""
    try:
        return set_google_client_secret(payload, connection_id)
    except Exception as exc:
        return create_error_response(exc)


@router.post(
    "/system/connections/google/connections/{connection_id}/oauth/start",
    response_model=GoogleOAuthStartResponse,
)
async def start_google_oauth_by_id_endpoint(
    connection_id: str,
) -> GoogleOAuthStartResponse | JSONResponse:
    """Start OAuth for one Google connection."""
    try:
        return start_google_oauth(connection_id)
    except Exception as exc:
        return create_error_response(exc)


@router.post(
    "/system/connections/google/connections/{connection_id}/oauth/complete",
    response_model=GoogleConnectionResponse,
)
async def complete_google_oauth_manual_by_id_endpoint(
    connection_id: str,
    payload: GoogleOAuthCompleteRequest,
) -> GoogleConnectionResponse | JSONResponse:
    """Complete OAuth for one connection using a pasted callback URL."""
    try:
        return await complete_google_oauth(payload, connection_id)
    except Exception as exc:
        return create_error_response(exc)


@router.delete(
    "/system/connections/google/connections/{connection_id}/oauth",
    response_model=OperationResult,
)
async def disconnect_google_oauth_by_id_endpoint(
    connection_id: str,
) -> OperationResult | JSONResponse:
    """Disconnect one Google grant while preserving its client configuration."""
    try:
        return disconnect_google_oauth(connection_id)
    except Exception as exc:
        return create_error_response(exc)


@router.delete(
    "/system/connections/google/connections/{connection_id}",
    response_model=OperationResult,
)
async def delete_google_connection_by_id_endpoint(
    connection_id: str,
    replacement_default_id: str | None = Query(None),
) -> OperationResult | JSONResponse:
    """Delete one Google connection and its encrypted credentials."""
    try:
        return delete_google_connection(
            connection_id, replacement_default_id=replacement_default_id
        )
    except Exception as exc:
        return create_error_response(exc)


@router.get("/system/mcp/connections", response_model=list[MCPConnectionInfo])
async def list_mcp_connections_endpoint() -> list[MCPConnectionInfo] | JSONResponse:
    """List current-user MCP connections without credential values."""
    try:
        return list_mcp_connections()
    except Exception as e:
        return create_error_response(e)


@router.post("/system/mcp/connections", response_model=MCPConnectionInfo)
async def create_mcp_connection_endpoint(
    request: MCPConnectionCreateRequest,
) -> MCPConnectionInfo | JSONResponse:
    """Create a current-user MCP connection."""
    try:
        return create_mcp_connection(request)
    except Exception as e:
        return create_error_response(e)


@router.post(
    "/system/mcp/connections/import/parse",
    response_model=MCPConnectionCreateRequest,
)
async def parse_mcp_connection_import_endpoint(
    request: MCPConnectionImportRequest,
) -> MCPConnectionCreateRequest | JSONResponse:
    """Parse strict YAML/JSON into the normal connection create contract."""
    try:
        return parse_mcp_connection_import(request)
    except Exception as e:
        return create_error_response(e)


@router.put("/system/mcp/connections/{connection_id}", response_model=MCPConnectionInfo)
async def update_mcp_connection_endpoint(
    connection_id: str, request: MCPConnectionUpdateRequest
) -> MCPConnectionInfo | JSONResponse:
    """Update mutable current-user MCP connection settings."""
    try:
        return update_mcp_connection(connection_id, request)
    except Exception as e:
        return create_error_response(e)


@router.put(
    "/system/mcp/connections/{connection_id}/credential",
    response_model=MCPConnectionInfo,
)
async def set_mcp_credential_endpoint(
    connection_id: str, request: MCPCredentialUpdateRequest
) -> MCPConnectionInfo | JSONResponse:
    """Set a write-only static credential for a current-user connection."""
    try:
        return set_mcp_credential(connection_id, request)
    except Exception as e:
        return create_error_response(e)


@router.delete(
    "/system/mcp/connections/{connection_id}/credential",
    response_model=MCPConnectionInfo,
)
async def clear_mcp_credential_endpoint(
    connection_id: str,
) -> MCPConnectionInfo | JSONResponse:
    """Clear a static credential for a current-user connection."""
    try:
        return clear_mcp_credential(connection_id)
    except Exception as e:
        return create_error_response(e)


@router.put(
    "/system/mcp/connections/{connection_id}/oauth/client-secret",
    response_model=MCPConnectionInfo,
)
async def set_mcp_oauth_client_secret_endpoint(
    connection_id: str, request: MCPOAuthClientSecretUpdateRequest
) -> MCPConnectionInfo | JSONResponse:
    """Set a write-only OAuth client secret for a current-user connection."""
    try:
        return set_mcp_oauth_client_secret(connection_id, request)
    except Exception as e:
        return create_error_response(e)


@router.post(
    "/system/mcp/connections/{connection_id}/test",
    response_model=MCPConnectionTestResponse,
)
async def test_mcp_connection_endpoint(
    connection_id: str,
) -> MCPConnectionTestResponse | JSONResponse:
    """Return sanitized connection readiness; transport arrives in slice 7."""
    try:
        return await test_mcp_connection(connection_id)
    except Exception as e:
        return create_error_response(e)


@router.post(
    "/system/mcp/connections/{connection_id}/oauth/start",
    response_model=MCPOAuthStartResponse,
)
async def start_mcp_oauth_endpoint(
    connection_id: str,
    payload: MCPOAuthStartRequest,
    request: Request,
) -> MCPOAuthStartResponse | JSONResponse:
    """Start a headless-safe OAuth attempt for one MCP connection."""
    try:
        request_fallback = payload.redirect_uri or str(
            request.url_for(
                "complete_mcp_oauth_callback_endpoint",
                connection_id=connection_id,
            )
        )
        resolved = resolve_mcp_oauth_redirect(
            connection_id=connection_id,
            public_origin=get_runtime_context().config.public_origin,
            fallback_uri=request_fallback,
        )
        return await start_mcp_oauth(
            connection_id,
            redirect_uri=resolved.redirect_uri,
            redirect_source=resolved.source,
        )
    except Exception as e:
        return create_error_response(e)


@router.get(
    "/system/mcp/connections/{connection_id}/oauth/callback",
)
async def complete_mcp_oauth_callback_endpoint(
    connection_id: str,
    code: str,
    state: str,
) -> HTMLResponse:
    """Complete MCP OAuth from a browser callback."""
    try:
        await complete_mcp_oauth(
            connection_id,
            MCPOAuthCompleteRequest(redirect_url=None, code=code, state=state),
        )
        return HTMLResponse(_mcp_oauth_callback_page(success=True))
    except Exception as e:
        error_response = create_error_response(e)
        return HTMLResponse(
            _mcp_oauth_callback_page(success=False),
            status_code=error_response.status_code,
        )


@router.post(
    "/system/mcp/connections/{connection_id}/oauth/complete",
    response_model=MCPOAuthStatusResponse,
)
async def complete_mcp_oauth_manual_endpoint(
    connection_id: str,
    payload: MCPOAuthCompleteRequest,
) -> MCPOAuthStatusResponse | JSONResponse:
    """Complete MCP OAuth from a pasted redirect URL."""
    try:
        return await complete_mcp_oauth(connection_id, payload)
    except Exception as e:
        return create_error_response(e)


@router.get(
    "/system/mcp/connections/{connection_id}/oauth/status",
    response_model=MCPOAuthStatusResponse,
)
async def get_mcp_oauth_status_endpoint(
    connection_id: str,
) -> MCPOAuthStatusResponse | JSONResponse:
    """Return sanitized OAuth status for one MCP connection."""
    try:
        return await get_mcp_oauth_status(connection_id)
    except Exception as e:
        return create_error_response(e)


@router.delete(
    "/system/mcp/connections/{connection_id}/oauth",
    response_model=OperationResult,
)
async def disconnect_mcp_oauth_endpoint(
    connection_id: str,
) -> OperationResult | JSONResponse:
    """Disconnect OAuth for one MCP connection."""
    try:
        return await disconnect_mcp_oauth(connection_id)
    except Exception as e:
        return create_error_response(e)


@router.delete(
    "/system/mcp/connections/{connection_id}", response_model=OperationResult
)
async def delete_mcp_connection_endpoint(
    connection_id: str,
) -> OperationResult | JSONResponse:
    """Delete a current-user MCP connection and static credential."""
    try:
        return delete_mcp_connection(connection_id)
    except Exception as e:
        return create_error_response(e)


@router.post("/system/cache/purge-expired", response_model=CachePurgeResponse)
async def purge_expired_cache_endpoint() -> CachePurgeResponse | JSONResponse:
    """Manually delete expired cache artifacts."""
    try:
        return purge_expired_cache()
    except Exception as e:
        return create_error_response(e)


@router.post("/system/goals/cleanup", response_model=GoalCleanupResponse)
async def cleanup_goals_endpoint(
    request: GoalCleanupRequest,
) -> GoalCleanupResponse | JSONResponse:
    """Manually delete old completed or cancelled goals for a vault."""
    try:
        return cleanup_goals(
            request.vault_name,
            status=request.status,
            older_than_days=request.older_than_days,
        )
    except Exception as e:
        return create_error_response(e)


@router.get("/system/migrations/status", response_model=SystemMigrationStatusResponse)
async def get_system_database_migration_status_endpoint() -> (
    SystemMigrationStatusResponse | JSONResponse
):
    """Inspect registered system database migrations."""
    try:
        return get_system_database_migration_status()
    except Exception as e:
        return create_error_response(e)


@router.post("/system/migrations/run", response_model=SystemMigrationRunResponse)
async def run_system_database_migrations_endpoint(
    request: SystemMigrationRunRequest = SystemMigrationRunRequest(backup=True),
) -> SystemMigrationRunResponse | JSONResponse:
    """Run registered system database migrations."""
    try:
        return run_system_database_migrations(backup=request.backup)
    except Exception as e:
        return create_error_response(e)


@router.post(
    "/system/authoring/seed-refresh", response_model=SystemTemplateSeedResponse
)
async def refresh_system_authoring_templates_endpoint() -> (
    SystemTemplateSeedResponse | JSONResponse
):
    """Manually refresh packaged system Authoring templates."""
    try:
        return refresh_system_authoring_templates()
    except Exception as e:
        return create_error_response(e)


@router.post("/vault-state/cleanup", response_model=VaultStateCleanupResponse)
async def cleanup_vault_state_endpoint() -> VaultStateCleanupResponse | JSONResponse:
    """Manually delete expired vault-state activity and safety artifacts."""
    try:
        return cleanup_vault_state()
    except Exception as e:
        return create_error_response(e)


#######################################################################
## Vault Management Endpoints
#######################################################################


@router.post("/vaults/rescan", response_model=VaultRescanResponse)
async def rescan_vaults(
    request: VaultRescanRequest = VaultRescanRequest(),
) -> VaultRescanResponse | JSONResponse:
    """
    Force immediate rediscovery of all vault directories and reload workflow configurations.

    This endpoint:
    - Rediscovers all vault directories
    - Reloads all workflow configurations from discovered vaults
    - Updates the scheduler with new/modified/removed workflow jobs
    - Returns summary of discovered vaults and workflows
    """
    try:
        # Try to get scheduler from runtime context
        scheduler = None
        try:
            runtime = get_runtime_context()
            scheduler = runtime.scheduler
        except RuntimeStateError:
            pass  # Runtime context not available - rescan will reload configs but not update jobs

        # Perform the rescan operation
        results = await rescan_vaults_and_update_scheduler(scheduler)
        metadata = await get_metadata()

        return VaultRescanResponse(
            success=True,
            vaults_discovered=results["vaults_discovered"],
            workflows_loaded=results["workflows_loaded"],
            enabled_workflows=results["enabled_workflows"],
            scheduler_jobs_synced=results["scheduler_jobs_synced"],
            message=f"Rescan completed successfully: {results['vaults_discovered']} vaults, {results['enabled_workflows']} enabled workflows, {results['scheduler_jobs_synced']} jobs synced",
            metadata=metadata,
        )

    except Exception as e:
        return create_error_response(e)


@router.get("/vaults/{vault_name}/activity", response_model=VaultActivityResponse)
async def vault_activity(
    vault_name: str,
    limit: int = 50,
    task_id: str | None = None,
    include_expired: bool = False,
    operation: str | None = None,
) -> VaultActivityResponse | JSONResponse:
    """Return recent durable attributed activity for one vault."""
    try:
        return get_vault_activity(
            vault_name=vault_name,
            limit=limit,
            task_id=task_id,
            include_expired=include_expired,
            operation=operation,
        )
    except Exception as e:
        return create_error_response(e)


@router.get(
    "/vaults/{vault_name}/activity/{activity_id}/rollback",
    response_model=VaultActivityRollbackPreviewResponse,
)
async def vault_activity_rollback_preview(
    vault_name: str, activity_id: str
) -> VaultActivityRollbackPreviewResponse | JSONResponse:
    """Return current all-or-nothing rollback availability for one activity."""
    try:
        return get_vault_activity_rollback_preview(
            vault_name=vault_name,
            activity_id=activity_id,
        )
    except Exception as e:
        return create_error_response(e)


@router.post(
    "/vaults/{vault_name}/activity/{activity_id}/rollback",
    response_model=VaultActivityRollbackResponse,
)
async def vault_activity_rollback(
    vault_name: str,
    activity_id: str,
    request: VaultActivityRollbackRequest,
) -> VaultActivityRollbackResponse | JSONResponse:
    """Restore every supported path in one activity atomically."""
    try:
        return rollback_vault_activity(
            vault_name=vault_name,
            activity_id=activity_id,
            expected_states=[
                (state.path, state.exists, state.sha256)
                for state in request.expected_states
            ],
        )
    except Exception as e:
        return create_error_response(e)


@router.get("/vault-state/snapshots/{snapshot_id}/content", response_model=None)
async def vault_snapshot_content(snapshot_id: int) -> FileResponse | JSONResponse:
    """Serve one retained vault-state file snapshot inline."""
    try:
        snapshot = get_vault_snapshot_file(snapshot_id)
        return FileResponse(
            snapshot.path,
            media_type=snapshot.media_type,
            filename=snapshot.filename,
            content_disposition_type="inline",
        )
    except Exception as e:
        return create_error_response(e)


@router.post("/workflows/execute", response_model=ExecuteWorkflowResponse)
async def execute_workflow(
    request: ExecuteWorkflowRequest,
) -> ExecuteWorkflowResponse | JSONResponse:
    """
    Execute a specific workflow manually.
    """
    try:
        result = await execute_workflow_manually(
            request.global_id,
            request.expect_failure,
            vault_name=request.vault_name,
        )
        response = ExecuteWorkflowResponse(**result)
        return response
    except Exception as e:
        return create_error_response(e)


@router.post("/workflows/enabled", response_model=WorkflowEnabledResponse)
async def set_workflow_enabled(
    request: WorkflowEnabledRequest,
) -> WorkflowEnabledResponse | JSONResponse:
    """Set a workflow enabled flag in frontmatter."""
    try:
        return await set_workflow_enabled_state(request.global_id, request.enabled)
    except Exception as e:
        return create_error_response(e)


@router.get("/workflows/file", response_model=WorkflowFileResponse)
async def workflow_file(global_id: str) -> WorkflowFileResponse | JSONResponse:
    """Return editable workflow file content."""
    try:
        return get_workflow_file(global_id)
    except Exception as e:
        return create_error_response(e)


@router.put("/workflows/file", response_model=WorkflowFileResponse)
async def save_workflow_file(
    global_id: str,
    request: WorkflowFileUpdateRequest,
) -> WorkflowFileResponse | JSONResponse:
    """Replace workflow file content and reload workflows."""
    try:
        return await update_workflow_file(
            global_id,
            content=request.content,
            expected_sha256=request.expected_sha256,
        )
    except Exception as e:
        return create_error_response(e)


@router.get("/workflows/load-errors", response_model=WorkflowLoadErrorsResponse)
async def workflow_load_errors(
    vault_name: str | None = None, workflow_name: str | None = None
) -> WorkflowLoadErrorsResponse | JSONResponse:
    """Return workflow load errors without exposing the full system status payload."""
    try:
        if workflow_name and _looks_like_workflow_path(workflow_name):
            raise APIException(
                status_code=400,
                error_type="InvalidWorkflowNameFilter",
                message=(
                    "workflow_load_errors expects a workflow name, not a file path. "
                    "Use compile-only workflow testing for draft files under AssistantMD/Workflows/."
                ),
                details={"workflow_name": workflow_name},
            )
        return WorkflowLoadErrorsResponse(
            errors=get_workflow_load_errors(
                vault_name=vault_name, workflow_name=workflow_name
            )
        )
    except Exception as e:
        return create_error_response(e)


@router.get("/workflows/runs", response_model=WorkflowRunHistoryResponse)
async def workflow_run_history(
    global_id: str, limit: int = 50
) -> WorkflowRunHistoryResponse | JSONResponse:
    """Return recent durable outcomes for one workflow."""
    try:
        return WorkflowRunHistoryResponse(
            **get_workflow_run_history(global_id, limit=limit)
        )
    except Exception as e:
        return create_error_response(e)


@router.get("/workflows/tasks", response_model=ExecutionTaskListResponse)
async def workflow_tasks(
    vault_name: str | None = None,
) -> ExecutionTaskListResponse | JSONResponse:
    """List process-local workflow execution task snapshots."""
    try:
        return await list_workflow_tasks(vault_name=vault_name)
    except Exception as e:
        return create_error_response(e)


@router.get("/metadata", response_model=MetadataResponse)
async def metadata() -> MetadataResponse | JSONResponse:
    """
    Get metadata for UI (vaults, models, tools).
    """
    try:
        return await get_metadata()
    except Exception as e:
        return create_error_response(e)


@router.get("/context/templates", response_model=list[TemplateInfo])
async def context_templates(vault_name: str) -> list[TemplateInfo] | JSONResponse:
    """
    List available context templates for a vault (vault + system sources).
    """
    try:
        return list_context_templates(vault_name)
    except Exception as e:
        return create_error_response(e)


@router.get(
    "/vaults/{vault_name}/directories", response_model=VaultDirectoryListResponse
)
async def vault_directories(
    vault_name: str, path: str | None = None
) -> VaultDirectoryListResponse | JSONResponse:
    """Return child directories for workspace selection."""
    try:
        return list_vault_directories(vault_name, path)
    except Exception as e:
        return create_error_response(e)


@router.get(
    "/vaults/{vault_name}/file-refs", response_model=VaultFileReferenceListResponse
)
async def vault_file_references(
    vault_name: str,
    path: str | None = None,
    workspace_path: str | None = None,
    query: str | None = None,
    scope: str = "workspace",
    limit: int = 100,
    offset: int = 0,
) -> VaultFileReferenceListResponse | JSONResponse:
    """Return file and folder candidates for chat reference insertion."""
    try:
        return list_vault_file_references(
            vault_name=vault_name,
            path=path,
            workspace_path=workspace_path,
            query=query,
            scope=scope,
            limit=limit,
            offset=offset,
        )
    except Exception as e:
        return create_error_response(e)


@router.post(
    "/vaults/{vault_name}/file-refs/resolve", response_model=VaultPathResolveResponse
)
async def resolve_vault_file_references(
    vault_name: str,
    request: VaultPathResolveRequest,
) -> VaultPathResolveResponse | JSONResponse:
    """Resolve candidate file and directory references from rendered chat content."""
    try:
        return resolve_vault_path_references(
            vault_name=vault_name,
            paths=request.paths,
            workspace_path=request.workspace_path,
        )
    except Exception as e:
        return create_error_response(e)


@router.post(
    "/vaults/{vault_name}/paths/mutate", response_model=VaultPathMutationResponse
)
async def mutate_vault_explorer_path(
    vault_name: str,
    request: VaultPathMutationRequest,
) -> VaultPathMutationResponse | JSONResponse:
    """Apply one direct user mutation from the vault explorer."""
    try:
        return mutate_vault_path(
            vault_name=vault_name,
            operation=request.operation,
            path=request.path,
            destination=request.destination,
            content=request.content,
        )
    except Exception as e:
        return create_error_response(e)


@router.post(
    "/vaults/{vault_name}/files/upload", response_model=VaultPathMutationResponse
)
async def upload_vault_explorer_file(
    vault_name: str,
    path: str,
    request: Request,
) -> VaultPathMutationResponse | JSONResponse:
    """Upload one create-only binary file into the selected vault."""
    form: FormData | None = None
    try:
        max_upload_mb = get_vault_upload_max_mb_per_file()
        max_upload_bytes = get_vault_upload_max_bytes_per_file()
        if max_upload_bytes == 0:
            raise APIException(
                status_code=403,
                error_type="VaultUploadsDisabled",
                message="Vault Explorer uploads are disabled in settings.",
                details={"setting": "vault_upload_max_mb_per_file"},
            )
        _, normalized_path, _ = resolve_vault_upload_target(
            vault_name=vault_name,
            path=path,
        )
        _validate_vault_upload_content_length(
            request,
            max_upload_bytes=max_upload_bytes,
        )
        form, upload = await _parse_single_vault_upload(
            request,
            max_upload_bytes=max_upload_bytes,
        )
        content = await _read_vault_upload(
            upload,
            max_upload_bytes=max_upload_bytes,
            max_upload_mb=max_upload_mb,
        )
        return upload_vault_file(
            vault_name=vault_name,
            path=normalized_path,
            content=content,
        )
    except Exception as e:
        return create_error_response(e)
    finally:
        if form is not None:
            await form.close()


@router.get("/vaults/{vault_name}/files", response_model=VaultFileResponse)
async def vault_file(vault_name: str, path: str) -> VaultFileResponse | JSONResponse:
    """Return editable vault file content."""
    try:
        return get_vault_file(vault_name, path)
    except Exception as e:
        return create_error_response(e)


@router.get(
    "/vaults/{vault_name}/files/revisions",
    response_model=VaultFileRevisionResponse,
)
async def vault_file_revisions(
    vault_name: str, path: str, limit: int = 50
) -> VaultFileRevisionResponse | JSONResponse:
    """Return retained revision history for one exact vault file path."""
    try:
        return get_vault_file_revisions(
            vault_name=vault_name,
            path=path,
            limit=limit,
        )
    except Exception as e:
        return create_error_response(e)


@router.post(
    "/vaults/{vault_name}/files/revisions/{snapshot_id}/restore",
    response_model=VaultFileRevisionRestoreResponse,
)
async def restore_vault_revision(
    vault_name: str,
    snapshot_id: int,
    request: VaultFileRevisionRestoreRequest,
) -> VaultFileRevisionRestoreResponse | JSONResponse:
    """Restore one retained exact-path file revision."""
    try:
        return restore_vault_file_revision(
            vault_name=vault_name,
            snapshot_id=snapshot_id,
            expected_sha256=request.expected_sha256,
        )
    except Exception as e:
        return create_error_response(e)


@router.put("/vaults/{vault_name}/files", response_model=VaultFileResponse)
async def save_vault_file(
    vault_name: str, path: str, request: VaultFileUpdateRequest
) -> VaultFileResponse | JSONResponse:
    """Replace vault file content with optimistic concurrency checks."""
    try:
        return update_vault_file(
            vault_name=vault_name,
            path=path,
            content=request.content,
            expected_sha256=request.expected_sha256,
            create_if_missing=request.create_if_missing,
        )
    except Exception as e:
        return create_error_response(e)


@router.get(
    "/vaults/{vault_name}/chat/{session_id}/edit-proposals/{artifact_ref:path}",
    response_model=EditProposalResponse,
)
async def chat_edit_proposal(
    vault_name: str, session_id: str, artifact_ref: str
) -> EditProposalResponse | JSONResponse:
    """Return a chat edit proposal artifact."""
    try:
        return get_chat_edit_proposal(
            vault_name=vault_name,
            session_id=session_id,
            artifact_ref=artifact_ref,
        )
    except Exception as e:
        return create_error_response(e)


@router.get(
    "/vaults/{vault_name}/chat/{session_id}/deferred-reviews/{artifact_ref:path}",
    response_model=DeferredReviewResponse,
)
async def chat_deferred_review(
    vault_name: str, session_id: str, artifact_ref: str
) -> DeferredReviewResponse | JSONResponse:
    """Return a deferred inline review request."""
    try:
        return get_chat_deferred_review(
            vault_name=vault_name,
            session_id=session_id,
            artifact_ref=artifact_ref,
        )
    except Exception as e:
        return create_error_response(e)


@router.post(
    "/vaults/{vault_name}/chat/{session_id}/deferred-reviews/{artifact_ref:path}/submit",
    response_model=DeferredReviewSubmitResponse,
)
async def submit_deferred_review_artifact(
    vault_name: str,
    session_id: str,
    artifact_ref: str,
    request: DeferredReviewSubmitRequest,
) -> DeferredReviewSubmitResponse | JSONResponse:
    """Submit deferred inline review decisions and resume the chat run."""
    try:
        resolved_session_id = resolve_chat_session_for_request(
            requested_session_id=session_id,
            vault_name=vault_name,
        )
        return await submit_chat_deferred_review(
            vault_name=vault_name,
            session_id=resolved_session_id,
            artifact_ref=artifact_ref,
            decisions=[decision.model_dump() for decision in request.decisions],
        )
    except ChatSessionVaultMismatch as exc:
        return create_error_response(
            ChatSessionVaultMismatchError(
                session_id=exc.session_id,
                requested_vault=exc.requested_vault,
                bound_vault=exc.bound_vault,
            )
        )
    except Exception as e:
        return create_error_response(e)


@router.get("/chat/sessions", response_model=list[ChatSessionInfo])
async def chat_sessions(vault_name: str) -> list[ChatSessionInfo] | JSONResponse:
    """
    List persisted chat sessions for a vault ordered by latest activity.
    """
    try:
        return list_chat_sessions(vault_name)
    except Exception as e:
        return create_error_response(e)


@router.get("/chat/sessions/{session_id}/active-task", response_model=ExecutionTaskInfo)
async def chat_session_active_task(session_id: str) -> ExecutionTaskInfo | JSONResponse:
    """Return the active process-local execution task for a chat session."""
    try:
        return await get_active_chat_task(session_id)
    except Exception as e:
        return create_error_response(e)


@router.post(
    "/chat/sessions/{session_id}/cancel", response_model=ExecutionTaskCancelResponse
)
async def cancel_chat_session(
    session_id: str,
) -> ExecutionTaskCancelResponse | JSONResponse:
    """Request cancellation for the active process-local task in a chat session."""
    try:
        return await cancel_chat_session_task(session_id)
    except Exception as e:
        return create_error_response(e)


@router.get(
    "/chat/sessions/{session_id}/summary", response_model=ChatSessionSummaryResponse
)
async def chat_session_summary(
    session_id: str, vault_name: str
) -> ChatSessionSummaryResponse | JSONResponse:
    """Return a lightweight summary preview for one chat session."""
    try:
        return ChatSessionSummaryResponse.model_validate(
            get_chat_session_summary(vault_name, session_id)
        )
    except Exception as e:
        return create_error_response(e)


@router.put(
    "/chat/sessions/{session_id}/summary", response_model=ChatSessionSummaryResponse
)
async def update_chat_session_summary_endpoint(
    session_id: str,
    vault_name: str,
    request: ChatSessionSummaryUpdateRequest,
) -> ChatSessionSummaryResponse | JSONResponse:
    """Manually update one session summary record."""
    try:
        return ChatSessionSummaryResponse.model_validate(
            await update_chat_session_summary(
                vault_name=vault_name,
                session_id=session_id,
                data=request.model_dump(mode="python"),
            )
        )
    except Exception as e:
        return create_error_response(e)


@router.delete("/chat/sessions/{session_id}/summary", response_model=None)
async def delete_chat_session_summary_endpoint(
    session_id: str, vault_name: str
) -> dict[str, Any] | JSONResponse:
    """Delete one session summary record without deleting the chat session."""
    try:
        return delete_chat_session_summary(vault_name, session_id)
    except Exception as e:
        return create_error_response(e)


@router.get("/chat/sessions/{session_id}", response_model=ChatSessionDetailResponse)
async def chat_session_detail(
    session_id: str, vault_name: str
) -> ChatSessionDetailResponse | JSONResponse:
    """
    Load one persisted chat session for UI rehydration.
    """
    try:
        return get_chat_session_detail(vault_name, session_id)
    except Exception as e:
        return create_error_response(e)


@router.get(
    "/chat/sessions/{session_id}/tools/{tool_call_id}",
    response_model=ChatToolCallDetailResponse,
)
async def chat_tool_call_detail(
    session_id: str,
    tool_call_id: str,
    vault_name: str,
) -> ChatToolCallDetailResponse | JSONResponse:
    """Load complete persisted detail for one session-owned tool call."""
    try:
        return get_chat_tool_call_detail(vault_name, session_id, tool_call_id)
    except Exception as e:
        return create_error_response(e)


@router.delete("/chat/sessions/{session_id}", response_model=None)
async def delete_chat_session_endpoint(
    session_id: str, vault_name: str
) -> dict[str, Any] | JSONResponse:
    """Delete one chat session from the canonical store."""
    try:
        vault_path = str(resolve_vault_root(vault_name))
        delete_chat_session(vault_name, vault_path, session_id)
        return {"session_id": session_id, "deleted": True}
    except Exception as e:
        return create_error_response(e)


@router.patch("/chat/sessions/{session_id}/title", response_model=None)
async def set_session_title(
    session_id: str, request: ChatSessionTitleRequest
) -> dict[str, Any] | JSONResponse:
    """Set or clear the user-defined title for a chat session."""
    try:
        title = (request.title or "").strip() or None
        set_chat_session_title(request.vault_name, session_id, title)
        return {"session_id": session_id, "title": title}
    except Exception as e:
        return create_error_response(e)


@router.patch(
    "/chat/sessions/{session_id}/workspace", response_model=ChatWorkspaceInfo | None
)
async def set_session_workspace(
    session_id: str, request: ChatSessionWorkspaceRequest
) -> ChatWorkspaceInfo | None | JSONResponse:
    """Set or clear the workspace path for a chat session."""
    try:
        return set_chat_session_workspace(request.vault_name, session_id, request.path)
    except Exception as e:
        return create_error_response(e)


@router.patch(
    "/chat/sessions/{session_id}/mode", response_model=ChatSessionModeResponse
)
async def set_session_mode(
    session_id: str, request: ChatSessionModeRequest
) -> ChatSessionModeResponse | JSONResponse:
    """Persist the selected mode for a chat session."""
    try:
        chat_mode = set_chat_session_mode(
            request.vault_name, session_id, request.chat_mode
        )
        return ChatSessionModeResponse(session_id=session_id, chat_mode=chat_mode)
    except Exception as e:
        return create_error_response(e)


@router.post("/chat/sessions/{session_id}/fork", response_model=ChatSessionForkResponse)
async def fork_chat_session_endpoint(
    session_id: str, request: ChatSessionForkRequest
) -> ChatSessionForkResponse | JSONResponse:
    """Fork one persisted chat session through a specific message sequence."""
    try:
        return fork_chat_session(
            vault_name=request.vault_name,
            source_session_id=session_id,
            through_sequence_index=request.through_sequence_index,
        )
    except Exception as e:
        return create_error_response(e)


@router.post("/chat/sessions/{session_id}/retry", response_model=ChatTaskStartResponse)
async def retry_chat_session_turn_endpoint(
    session_id: str,
    request: ChatSessionRetryRequest,
) -> ChatTaskStartResponse | JSONResponse:
    """Retry the latest retryable unfinished chat turn for one session."""
    try:
        resolve_chat_session_for_request(
            requested_session_id=session_id,
            vault_name=request.vault_name,
        )
        try:
            await get_active_chat_task(session_id)
        except APIException as exc:
            if exc.error_type != "ExecutionTaskNotFound":
                raise
        else:
            raise APIException(
                status_code=409,
                error_type="ChatTaskAlreadyActive",
                message=f"Chat session already has an active task: {session_id}",
                details={"session_id": session_id},
            )

        vault_path = str(resolve_vault_root(request.vault_name))
        started = await start_chat_turn_retry_task(
            vault_name=request.vault_name,
            vault_path=vault_path,
            session_id=session_id,
        )
        task = await get_execution_task(started.task.task_id)
        return ChatTaskStartResponse(session_id=session_id, task=task)
    except ValueError as exc:
        return create_error_response(
            APIException(
                status_code=409,
                error_type="ChatTurnRetryUnavailable",
                message=str(exc),
                details={"session_id": session_id, "vault_name": request.vault_name},
            )
        )
    except Exception as e:
        return create_error_response(e)


@router.post(
    "/chat/sessions/{session_id}/export", response_model=ChatSessionExportResponse
)
async def export_chat_session_endpoint(
    session_id: str, request: ChatSessionExportRequest
) -> ChatSessionExportResponse | JSONResponse:
    """Export one persisted chat session transcript into the owning vault."""
    try:
        vault_path = str(resolve_vault_root(request.vault_name))
        return export_chat_session_markdown(request.vault_name, vault_path, session_id)
    except Exception as e:
        return create_error_response(e)


@router.get(
    "/chat/sessions/{session_id}/compaction-status",
    response_model=ChatHistoryCompactionStatusResponse,
)
async def chat_history_compaction_status_endpoint(
    session_id: str, vault_name: str
) -> ChatHistoryCompactionStatusResponse | JSONResponse:
    """Return compaction status for one persisted chat session."""
    try:
        return await get_chat_history_compaction_status(vault_name, session_id)
    except Exception as e:
        return create_error_response(e)


@router.post(
    "/chat/sessions/{session_id}/compact", response_model=ChatHistoryCompactionResponse
)
async def compact_chat_history_endpoint(
    session_id: str,
    request: ChatHistoryCompactionRequest,
) -> ChatHistoryCompactionResponse | JSONResponse:
    """Compact one persisted chat session into a summary plus recent turns."""
    try:
        vault_path = str(resolve_vault_root(request.vault_name))
        return await compact_chat_session_history(
            request.vault_name,
            vault_path,
            session_id,
            focus=request.focus,
        )
    except Exception as e:
        return create_error_response(e)


@router.post("/chat/sessions/purge", response_model=ChatSessionsPurgeResponse)
async def purge_chat_sessions_endpoint(
    request: ChatSessionsPurgeRequest,
) -> ChatSessionsPurgeResponse | JSONResponse:
    """
    Delete old chat sessions and their transcript files for a vault.
    """
    try:
        vault_path = str(resolve_vault_root(request.vault_name))
        return purge_chat_sessions(
            request.vault_name,
            vault_path,
            older_than_days=request.older_than_days,
        )
    except Exception as e:
        return create_error_response(e)


#######################################################################
## Error Handlers (Note: These will be registered with the main FastAPI app)
#######################################################################


def register_exception_handlers(app: FastAPI) -> None:
    """Register exception handlers with the FastAPI app."""

    @app.exception_handler(APIException)
    async def api_exception_handler(
        request: Request, exc: APIException
    ) -> JSONResponse:
        """Handle API-specific exceptions with proper error responses."""
        return create_error_response(exc)

    @app.exception_handler(Exception)
    async def general_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        """Handle unexpected exceptions with generic error responses."""
        return create_error_response(exc)
