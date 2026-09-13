"""
Service layer for API operations.
Handles business logic for status reporting, vault management, etc.
"""

import hashlib
import json
import mimetypes
import re
import shutil
import uuid
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic_ai import DeferredToolResults, ToolApproved, ToolDenied
from pydantic_ai.messages import ModelResponse, TextPart, ThinkingPart
from sqlalchemy import func, select

from core.authoring.template_discovery import (
    list_system_workflow_templates,
    list_templates,
)
from core.chat import export_chat_transcript, remove_chat_transcript_exports
from core.chat.chat_store import StoredChatSession
from core.chat.compaction import compact_chat_history, get_compaction_status
from core.chat.deferred_reviews import (
    DeferredReviewError,
    StoredDeferredReview,
    attach_deferred_review_task,
    deferred_review_conflicts,
    get_deferred_review,
    get_pending_deferred_review,
    mark_deferred_review_submitted,
    mark_deferred_review_terminal,
)
from core.chat.edit_proposals import (
    EditProposalError,
    get_edit_proposal,
)
from core.chat.task_execution import start_deferred_review_resume_task
from core.chat.workspace import normalize_workspace_path
from core.constants import ASSISTANTMD_ROOT_DIR, INLINE_EDIT_DENIAL_MESSAGE
from core.llm.openai_auth import (
    openai_oauth_enabled_from_settings,
    openai_provider_api_key_available,
    openai_provider_base_url_available,
    resolve_openai_auth,
)
from core.llm.openai_oauth import (
    OpenAIOAuthStateError,
    clear_openai_oauth_state,
    complete_openai_oauth,
    complete_openai_oauth_from_redirect,
    get_openai_oauth_status,
    is_openai_oauth_internal_secret,
    poll_openai_oauth_device_code,
    start_openai_oauth_device_code,
)
from core.llm.openai_oauth import (
    start_openai_oauth as start_openai_oauth_attempt,
)
from core.llm.thinking import ThinkingValue, normalize_thinking_value
from core.memory.session_summary import SessionSummaryStore
from core.runtime.execution_tasks import (
    ExecutionTaskKind,
    ExecutionTaskSource,
    chat_session_scope,
    compaction_task_label,
    workflow_vault_scope,
)
from core.runtime.paths import (
    get_system_root,
    resolve_bootstrap_data_root,
    resolve_bootstrap_system_root,
    set_bootstrap_roots,
)
from core.runtime.reload_service import reload_configuration
from core.runtime.state import RuntimeStateError, get_runtime_context
from core.runtime.task_runner import ExecutionTaskSpec
from core.scheduling.job_history import get_scheduler_job_history
from core.scheduling.jobs import setup_scheduler_jobs
from core.scheduling.system_jobs import SYSTEM_JOB_IDS
from core.settings import (
    SettingsError,
    get_default_chat_mode,
    validate_settings,
)
from core.settings.config_editor import (
    delete_model_mapping,
    delete_provider_config,
    list_general_settings,
    update_general_setting,
    upsert_model_mapping,
    upsert_provider_config,
)
from core.settings.secrets_store import (
    delete_secret,
    get_secret_value,
    list_secret_entries,
    remove_secret,
    secret_has_value,
    set_secret_value,
)
from core.settings.store import (
    SETTINGS_TEMPLATE,
    get_active_settings_path,
    get_disabled_tool_names,
    get_enabled_tool_names,
    get_enabled_tools_config,
    get_general_settings,
    get_models_config,
    get_providers_config,
    get_tools_config,
)
from core.settings.upgrades import upgrade_settings_mapping
from core.tools.workflow_run import WorkflowRun
from core.utils.frontmatter import upsert_frontmatter_key
from core.vault_state.activity import VaultActivityContext, use_vault_activity
from core.vault_state.activity_rollback import (
    ActivityRollbackPlan,
    ActivityRollbackUnavailable,
    execute_activity_rollback,
    preview_activity_rollback,
)
from core.vault_state.cleanup import cleanup_expired_vault_state
from core.vault_state.file_mutations import (
    VaultMutationRejected,
    replace_vault_file_content,
    restore_vault_file,
    write_vault_file,
    write_vault_file_bytes,
)
from core.vault_state.file_operations import (
    VaultFileOperationRejected,
    VaultFileOperationResult,
    delete_vault_path_operation,
    make_vault_directory_operation,
    move_vault_directory_operation,
    move_vault_path_operation,
    replace_full_text_content,
)
from core.vault_state.models import VaultFile, VaultFileEvent
from core.vault_state.pathing import (
    VaultRootResolutionError,
    normalize_vault_relative_path,
    resolve_configured_vault_root,
    resolve_vault_relative_path,
)
from core.vault_state.service import VaultStateService
from core.vector import VectorService
from core.workflow_runs import WorkflowRunRecord

from ..exceptions import APIException, SystemConfigurationError
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
    ChatWorkspaceInfo,
    ConfigurationIssueInfo,
    ConfigurationStatusInfo,
    DeferredReviewCallInfo,
    DeferredReviewResponse,
    DeferredReviewSubmitResponse,
    EditProposalResponse,
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
    SchedulerInfo,
    SecretInfo,
    SecretUpdateRequest,
    SettingInfo,
    SettingUpdateRequest,
    StatusResponse,
    SystemInfo,
    SystemSettingsResponse,
    SystemWorkflowTemplateSummary,
    TemplateInfo,
    ToolInfo,
    VaultActivityResponse,
    VaultActivityRollbackIssueInfo,
    VaultActivityRollbackPathInfo,
    VaultActivityRollbackPreviewResponse,
    VaultActivityRollbackResponse,
    VaultDirectoryInfo,
    VaultDirectoryListResponse,
    VaultFileReferenceInfo,
    VaultFileReferenceListResponse,
    VaultFileResponse,
    VaultFileRevisionInfo,
    VaultFileRevisionResponse,
    VaultFileRevisionRestoreResponse,
    VaultInfo,
    VaultPathMutationResponse,
    VaultPathResolutionInfo,
    VaultPathResolveResponse,
    VaultStateCleanupResponse,
    WorkflowEnabledResponse,
    WorkflowFileResponse,
    WorkflowSummary,
)
from ..models import (
    ConfigurationError as APIConfigurationError,
)
from ..utils import generate_session_id
from .chat_sessions import (
    ChatSessionVaultMismatch,
    _deferred_review_response,
    _normalize_workspace_path,
    compact_chat_session_history,
    delete_chat_session,
    delete_chat_session_summary,
    export_chat_session_markdown,
    fork_chat_session,
    get_chat_history_compaction_status,
    get_chat_session_detail,
    get_chat_session_summary,
    get_chat_tool_call_detail,
    get_enabled_chat_tool_names,
    list_chat_sessions,
    purge_chat_sessions,
    resolve_chat_session_for_request,
    set_chat_session_mode,
    set_chat_session_title,
    set_chat_session_workspace,
    update_chat_session_summary,
)
from .configuration import (
    check_openai_oauth_device_connection,
    complete_openai_oauth_callback,
    complete_openai_oauth_manual,
    delete_configurable_model,
    delete_configurable_provider,
    delete_secret_entry,
    disconnect_openai_oauth_connection,
    get_configurable_models,
    get_configurable_providers,
    get_general_settings_config,
    get_metadata,
    get_system_settings,
    list_secrets,
    repair_settings_from_template,
    start_openai_oauth_connection,
    start_openai_oauth_device_connection,
    update_general_setting_value,
    update_secret,
    update_system_settings,
    upsert_configurable_model,
    upsert_configurable_provider,
)
from .deferred_reviews import (
    get_chat_deferred_review,
    get_chat_edit_proposal,
    submit_chat_deferred_review,
)
from .execution_tasks import (
    _execution_task_info,
    cancel_chat_session_task,
    cancel_execution_task,
    get_active_chat_task,
    get_execution_task,
    list_execution_tasks,
    list_workflow_tasks,
)
from .ingestion import (
    cancel_import_job,
    import_url_direct,
    list_recent_import_jobs,
    scan_import_folder,
    trigger_import_queue_now,
)
from .maintenance import (
    cleanup_goals,
    get_system_database_migration_status,
    purge_expired_cache,
    refresh_system_authoring_templates,
    run_system_database_migrations,
)
from .shared import (
    get_vault_path as _get_vault_path,
)
from .shared import (
    get_workflow_loader as _get_workflow_loader,
)
from .shared import logger
from .system import (
    collect_scheduler_status,
    collect_system_health,
    collect_vault_status,
    get_system_status,
    get_workflow_load_errors,
    list_context_templates,
    set_system_startup_time,
)
from .system_activity import export_system_activity_log, get_system_activity_log
from .vault_activity import (
    SnapshotFileResponse,
    cleanup_vault_state,
    get_vault_activity,
    get_vault_activity_rollback_preview,
    get_vault_snapshot_file,
    rollback_vault_activity,
)
from .vault_files import (
    get_vault_file,
    get_vault_file_revisions,
    list_vault_directories,
    list_vault_file_references,
    mutate_vault_path,
    resolve_vault_path_references,
    resolve_vault_root,
    resolve_vault_upload_target,
    restore_vault_file_revision,
    update_vault_file,
    upload_vault_file,
)
from .workflows import (
    _project_latest_workflow_runs,
    _sha256_text,
    execute_workflow_manually,
    get_configuration_errors,
    get_system_workflow_template_summaries,
    get_workflow_file,
    get_workflow_run_history,
    get_workflow_summaries,
    rescan_vaults_and_update_scheduler,
    set_workflow_enabled_state,
    update_workflow_file,
)
