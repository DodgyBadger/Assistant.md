"""
Pydantic models for API request and response schemas.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from core.advanced_shell import ExecutionMode
from core.advanced_shell.preflight import AdvancedShellReadiness
from core.authentication import AuthenticationMode

#######################################################################
## Request Models
#######################################################################


class VaultCreateRequest(BaseModel):
    """Request model for creating a new vault."""

    name: str = Field(
        ..., min_length=1, max_length=100, description="Name of the vault to create"
    )


class VaultRescanRequest(BaseModel):
    """Request model for vault rescan operation (empty body)."""

    pass


class ExecuteWorkflowRequest(BaseModel):
    """Request model for manually executing a workflow."""

    global_id: str = Field(..., description="Workflow global ID (vault/name format)")
    vault_name: str | None = Field(
        None,
        description="Vault scope for system workflow templates.",
    )
    expect_failure: bool = Field(
        False,
        description="Validation/testing hint: marks execution failures as expected in workflow logs.",
    )


class WorkflowEnabledRequest(BaseModel):
    """Request model for changing workflow enabled state."""

    global_id: str = Field(
        ..., description="Workflow global ID (vault/name or system/name format)"
    )
    enabled: bool = Field(..., description="Desired enabled state")


class WorkflowEnabledResponse(BaseModel):
    """Response model for workflow enabled-state changes."""

    success: bool = Field(..., description="Whether the enabled state was updated")
    global_id: str = Field(..., description="Workflow global ID")
    enabled_before: bool = Field(..., description="Enabled state before the update")
    enabled_after: bool = Field(..., description="Enabled state after the update")
    message: str = Field(..., description="Human-readable update summary")


class WorkflowFileUpdateRequest(BaseModel):
    """Request model for replacing workflow source content."""

    content: str = Field(..., description="Complete workflow file content")
    expected_sha256: str | None = Field(
        None,
        description="Optional hash from the last read response; rejects stale saves when provided.",
    )


class WorkflowFileResponse(BaseModel):
    """Response model for workflow source content."""

    global_id: str = Field(..., description="Workflow global ID")
    path: str = Field(..., description="Filesystem path for display")
    source: str = Field(..., description="Source scope: vault or system")
    content: str = Field(..., description="Complete workflow file content")
    sha256: str = Field(..., description="SHA-256 hash of the returned content")
    message: str | None = Field(None, description="Human-readable update summary")


class VaultFileUpdateRequest(BaseModel):
    """Request model for replacing a vault text file."""

    content: str = Field(..., description="Complete file content")
    expected_sha256: str | None = Field(
        None,
        description="Optional hash from the last read response; rejects stale saves when provided.",
    )
    create_if_missing: bool = Field(
        False,
        description="Create the file when it does not exist yet.",
    )


class VaultFileResponse(BaseModel):
    """Response model for editable vault text file content."""

    vault_name: str = Field(..., description="Vault name")
    path: str = Field(..., description="Vault-relative file path")
    name: str = Field(..., description="File basename")
    content: str = Field(..., description="Complete text content")
    sha256: str = Field(..., description="SHA-256 hash of the returned content")
    size_bytes: int = Field(..., description="UTF-8 content size in bytes")
    modified_at: datetime | None = Field(
        None, description="Filesystem modification timestamp"
    )
    media_type: str = Field(..., description="Detected media type")
    message: str | None = Field(None, description="Human-readable update summary")


class VaultFileRevisionInfo(BaseModel):
    """One retained pre-mutation state for an exact vault file path."""

    snapshot_id: int = Field(..., description="Retained file snapshot id")
    activity_id: str = Field(..., description="Owning vault activity id")
    activity_kind: str = Field(
        ..., description="Activity kind such as chat or explorer"
    )
    activity_source: str = Field(..., description="Activity source such as api or tool")
    activity_label: str = Field(..., description="User-facing activity label")
    task_id: str | None = Field(None, description="Execution task id when task-backed")
    path: str = Field(..., description="Exact vault-relative path at mutation time")
    operation: str = Field(..., description="Mutation that followed this state")
    exists: bool = Field(..., description="Whether the file existed in this revision")
    content_hash: str | None = Field(None, description="Revision content hash")
    snapshot_available: bool = Field(
        ..., description="Whether retained content can be previewed"
    )
    created_at: datetime = Field(..., description="Mutation timestamp")
    expires_at: datetime | None = Field(
        None, description="Snapshot expiration timestamp"
    )


class VaultFileRevisionResponse(BaseModel):
    """Retained path-based revision history for one vault file."""

    vault_name: str = Field(..., description="Vault name")
    path: str = Field(..., description="Exact vault-relative path")
    revisions: list[VaultFileRevisionInfo] = Field(default_factory=list)


class VaultFileRevisionRestoreRequest(BaseModel):
    """Optimistic concurrency state for restoring a retained revision."""

    expected_sha256: str | None = Field(
        ...,
        description="Current file hash, or null when the caller observed no file.",
    )


class VaultFileRevisionRestoreResponse(BaseModel):
    """Result of restoring one retained file revision."""

    vault_name: str = Field(..., description="Vault name")
    path: str = Field(..., description="Restored vault-relative path")
    snapshot_id: int = Field(..., description="Source revision snapshot id")
    exists: bool = Field(..., description="Whether the restored state contains a file")
    sha256: str | None = Field(None, description="Restored content hash")
    message: str = Field(..., description="Human-readable restore result")


class VaultFileReferenceInfo(BaseModel):
    """One file or folder candidate for chat reference insertion."""

    name: str = Field(..., description="Path basename")
    path: str = Field(..., description="Vault-relative path")
    kind: Literal["file", "directory"] = Field(..., description="Reference kind")
    size_bytes: int | None = Field(None, description="File size in bytes")
    modified_at: datetime | None = Field(
        None, description="Filesystem modification timestamp"
    )
    has_children: bool = Field(
        False, description="Whether a directory has child entries"
    )
    in_workspace: bool = Field(
        False, description="Whether the path is under the requested workspace"
    )


class VaultFileReferenceListResponse(BaseModel):
    """File/folder reference candidates for the chat composer."""

    vault_name: str = Field(..., description="Vault name")
    path: str = Field("", description="Listed vault-relative directory path")
    workspace_path: str = Field(
        "", description="Active workspace path used for ranking/filtering"
    )
    query: str = Field("", description="Search query")
    scope: Literal["workspace", "vault"] = Field(
        "workspace", description="Search/listing scope"
    )
    truncated: bool = Field(
        False, description="Whether additional matching entries were omitted"
    )
    next_offset: int | None = Field(
        None, description="Offset for the next direct-child page"
    )
    items: list[VaultFileReferenceInfo] = Field(
        default_factory=list, description="Reference candidates"
    )


class VaultPathResolveRequest(BaseModel):
    """Candidate vault paths extracted from rendered chat content."""

    paths: list[str] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Candidate file or directory paths to resolve",
    )
    workspace_path: str = Field(
        "",
        description="Active vault-relative workspace used for root-level shorthand",
    )


class VaultPathResolutionInfo(BaseModel):
    """Resolution of one candidate chat path."""

    requested_path: str = Field(..., description="Normalized candidate path")
    path: str = Field(..., description="Resolved vault-relative path")
    kind: Literal["file", "directory", "missing"] = Field(
        ..., description="Resolved kind"
    )
    source: Literal["workspace", "vault", "missing"] = Field(
        ...,
        description="Resolution source",
    )


class VaultPathResolveResponse(BaseModel):
    """Resolved chat path candidates for one vault."""

    vault_name: str = Field(..., description="Vault name")
    workspace_path: str = Field("", description="Workspace used during resolution")
    items: list[VaultPathResolutionInfo] = Field(
        default_factory=list,
        description="Resolution for each unique candidate path",
    )


class VaultPathMutationRequest(BaseModel):
    """One direct user mutation requested from the vault explorer."""

    operation: Literal["create_file", "create_directory", "move", "delete"] = Field(
        ...,
        description="Explorer mutation operation",
    )
    path: str = Field(
        ..., min_length=1, description="Vault-relative source or target path"
    )
    destination: str = Field("", description="Vault-relative move destination")
    content: str = Field("", description="Initial text content for create_file")


class VaultPathMutationResponse(BaseModel):
    """Result of one direct vault explorer mutation."""

    operation: str = Field(..., description="Completed mutation operation")
    path: str = Field(..., description="Vault-relative source or target path")
    destination: str = Field("", description="Vault-relative destination when moved")
    kind: Literal["file", "directory"] = Field(..., description="Mutated path kind")
    message: str = Field(..., description="Human-readable mutation result")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Mutation audit metadata"
    )


class EditProposalResponse(BaseModel):
    """Stored historical inline edit proposal artifact."""

    artifact_ref: str = Field(
        ..., description="Stable edit proposal artifact reference"
    )
    artifact_kind: str = Field("file_edit_proposal", description="Artifact kind")
    vault_name: str = Field(..., description="Owning vault")
    session_id: str = Field(..., description="Owning chat session")
    title: str = Field(..., description="Proposal title")
    summary: str = Field("", description="Proposal summary")
    status: str = Field(..., description="Proposal status")
    edits: list[dict[str, Any]] = Field(
        default_factory=list, description="Proposed file edits"
    )
    created_at: str | None = Field(None, description="Creation timestamp")
    applied_at: str | None = Field(None, description="Applied timestamp")
    applied_edit_ids: list[str] = Field(
        default_factory=list, description="Applied edit ids"
    )


class DeferredReviewCallInfo(BaseModel):
    """One deferred tool call awaiting inline review."""

    tool_call_id: str = Field(..., description="Provider tool call id")
    tool_name: str = Field(..., description="Tool name")
    args: Any = Field(None, description="Validated tool arguments")


class DeferredReviewResponse(BaseModel):
    """Stored deferred inline review request."""

    artifact_ref: str = Field(
        ..., description="Stable deferred review artifact reference"
    )
    artifact_kind: str = Field("deferred_tool_review", description="Artifact kind")
    vault_name: str = Field(..., description="Owning vault")
    session_id: str = Field(..., description="Owning chat session")
    originating_task_id: str = Field(
        ..., description="Task that produced the review request"
    )
    status: str = Field(..., description="Review status")
    approvals: list[DeferredReviewCallInfo] = Field(
        default_factory=list,
        description="Deferred approval calls to render for inline review",
    )
    calls: list[DeferredReviewCallInfo] = Field(
        default_factory=list,
        description="Deferred external calls, reserved for future use",
    )
    created_at: str | None = Field(None, description="Creation timestamp")
    submitted_at: str | None = Field(None, description="Submission timestamp")
    resumed_task_id: str | None = Field(
        None, description="Task created to resume the run"
    )


class DeferredReviewDecision(BaseModel):
    """One inline review decision for a deferred tool call."""

    tool_call_id: str = Field(..., description="Provider tool call id being reviewed")
    decision: Literal["approve", "deny"] = Field(..., description="Review decision")
    override_args: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional edited tool arguments for approved calls",
    )
    message: str = Field("", description="Optional denial reason or review note")


class DeferredReviewSubmitRequest(BaseModel):
    """Submit decisions for a deferred inline review request."""

    decisions: list[DeferredReviewDecision] = Field(
        ..., description="Per-call review decisions"
    )


class DeferredReviewSubmitResponse(BaseModel):
    """Result of submitting a deferred inline review."""

    artifact_ref: str = Field(
        ..., description="Submitted deferred review artifact reference"
    )
    status: str = Field(..., description="Updated review status")
    session_id: str = Field(..., description="Session identifier")
    task: ExecutionTaskInfo = Field(
        ..., description="Execution task created to resume the run"
    )


class ChatTaskRequest(BaseModel):
    """Request model for starting task-owned chat execution."""

    vault_name: str = Field(..., description="Vault context for execution")
    prompt: str = Field(..., min_length=1, description="User prompt text")
    image_paths: list[str] = Field(
        default_factory=list,
        description="Optional image file paths (relative to vault or absolute within vault) to attach",
    )
    session_id: str | None = Field(
        None, description="Session ID (generated if not provided)"
    )
    tools: list[str] = Field(
        default_factory=list,
        description="Deprecated: chat uses app-wide enabled tools resolved server-side.",
    )
    model: str = Field(..., description="Model name to use")
    thinking: str | None = Field(
        None,
        description="Optional per-request thinking override: default, on, off, minimal, low, medium, high, xhigh",
    )
    context_template: str | None = Field(
        None, description="Optional context manager template name"
    )
    workspace_path: str | None = Field(
        None, description="Optional vault-relative workspace directory path"
    )
    chat_mode: Literal["normal", "inline_edit"] = Field(
        "normal",
        description="Chat interaction mode. Inline edit mode routes file_write through inline review.",
    )


#######################################################################
## Response Models
#######################################################################


class VaultInfo(BaseModel):
    """Information about a single vault."""

    name: str = Field(..., description="Vault name")
    path: str = Field(..., description="Full path to vault directory")
    workflow_count: int = Field(..., description="Number of workflows in this vault")
    workflows: list[str] = Field(
        default_factory=list, description="List of workflow names"
    )
    tracked_files: int | None = Field(
        None, description="Current files tracked by vault state"
    )
    files_created_recent: int | None = Field(
        None, description="Files created in the recent vault-state change window"
    )
    files_deleted_recent: int | None = Field(
        None, description="Files deleted in the recent vault-state change window"
    )
    latest_vault_change_at: datetime | None = Field(
        None, description="Latest vault-state change observation"
    )


class SchedulerInfo(BaseModel):
    """Information about the scheduler status."""

    running: bool = Field(..., description="Whether the scheduler is running")
    total_jobs: int = Field(..., description="Total number of scheduled jobs")
    enabled_workflows: int = Field(..., description="Number of enabled workflows")
    disabled_workflows: int = Field(..., description="Number of disabled workflows")
    job_details: list[dict] = Field(
        default_factory=list, description="Detailed job information from APScheduler"
    )


class SystemInfo(BaseModel):
    """Information about system health."""

    startup_time: datetime = Field(..., description="When the system started")
    last_config_reload: datetime | None = Field(
        None, description="Last time configuration was reloaded"
    )
    data_root: str = Field(..., description="Root directory for vault data")
    public_url: str | None = Field(
        None, description="Canonical externally reachable Assistant.md origin"
    )
    public_url_source: Literal["configured", "unconfigured"] = "unconfigured"
    public_url_recommended: bool = True


class ConfigurationIssueInfo(BaseModel):
    """Configuration issue surfaced to the API."""

    name: str = Field(
        ..., description="Identifier for the issue (e.g., tool:web_search)"
    )
    message: str = Field(..., description="Human-readable description of the issue")
    severity: str = Field(..., description="Issue severity (error or warning)")


class ConfigurationStatusInfo(BaseModel):
    """Aggregated configuration health information for API clients."""

    issues: list[ConfigurationIssueInfo] = Field(
        default_factory=list,
        description="Configuration issues discovered during validation",
    )
    tool_availability: dict[str, bool] = Field(
        default_factory=dict, description="Tool availability keyed by tool name"
    )
    model_availability: dict[str, bool] = Field(
        default_factory=dict, description="Model availability keyed by model name"
    )
    default_model: str | None = Field(
        None, description="Default model alias from settings"
    )


class AdvancedShellStatusInfo(BaseModel):
    """Sanitized restart-bound advanced-shell deployment configuration."""

    execution_mode: ExecutionMode = Field(
        ..., description="Effective product execution mode"
    )
    host: str = Field(..., description="Configured advanced-shell hostname")
    port: int = Field(..., description="Configured advanced-shell SSH port")
    user: str = Field(..., description="Configured advanced-shell SSH user")
    readiness_state: AdvancedShellReadiness = Field(
        ..., description="Sanitized authenticated advanced-shell readiness state"
    )
    readiness_message: str = Field(
        ..., description="Sanitized operator-facing readiness explanation"
    )


class StatusResponse(BaseModel):
    """Response model for system status endpoint."""

    vaults: list[VaultInfo] = Field(
        default_factory=list, description="Information about discovered vaults"
    )
    scheduler: SchedulerInfo = Field(..., description="Scheduler status information")
    system: SystemInfo = Field(..., description="System health information")
    total_vaults: int = Field(..., description="Total number of discovered vaults")
    total_workflows: int = Field(
        ..., description="Total number of workflows across all vaults"
    )
    enabled_workflows: list[WorkflowSummary] = Field(
        default_factory=list, description="List of enabled workflows with details"
    )
    disabled_workflows: list[WorkflowSummary] = Field(
        default_factory=list, description="List of disabled workflows with details"
    )
    system_workflow_templates: list[SystemWorkflowTemplateSummary] = Field(
        default_factory=list,
        description="Packaged system workflow templates available to copy into a vault",
    )
    workflow_runs: dict[str, WorkflowRunInfo] = Field(
        default_factory=dict,
        description="Latest durable terminal workflow outcomes keyed by workflow id",
    )
    configuration_errors: list[ConfigurationError] = Field(
        default_factory=list,
        description="Configuration errors encountered during loading",
    )
    configuration_status: ConfigurationStatusInfo = Field(
        default_factory=lambda: ConfigurationStatusInfo(default_model=None),
        description="Aggregated configuration health information",
    )
    authentication_mode: AuthenticationMode = Field(
        ..., description="Effective deployment ingress authentication mode"
    )
    authentication_warning: str | None = Field(
        None,
        description="Sanitized operator warning for the effective authentication mode",
    )
    advanced_shell: AdvancedShellStatusInfo = Field(
        ..., description="Sanitized advanced-shell infrastructure status"
    )


class VaultCreateResponse(BaseModel):
    """Response model for vault creation endpoint."""

    success: bool = Field(..., description="Whether the vault was created successfully")
    vault_name: str = Field(..., description="Name of the created vault")
    vault_path: str = Field(..., description="Full path to the created vault")
    workflow_file: str = Field(..., description="Path to the created workflow file")
    message: str = Field(..., description="Human-readable success message")


class VaultRescanResponse(BaseModel):
    """Response model for vault rescan endpoint."""

    success: bool = Field(..., description="Whether the rescan was successful")
    vaults_discovered: int = Field(..., description="Number of vaults discovered")
    workflows_loaded: int = Field(..., description="Number of workflows loaded")
    enabled_workflows: int = Field(..., description="Number of enabled workflows")
    scheduler_jobs_synced: int = Field(
        ..., description="Number of scheduler jobs synchronized"
    )
    message: str = Field(..., description="Human-readable success message")
    metadata: MetadataResponse | None = Field(
        None, description="Updated metadata after rescan"
    )


class VaultMutationInfo(BaseModel):
    """One recorded path mutation for an attributed vault activity."""

    id: int = Field(..., description="Mutation row id")
    activity_id: str = Field(..., description="Owning vault activity id")
    operation_id: str = Field(..., description="Logical operation id")
    task_id: str | None = Field(None, description="Execution task id when task-backed")
    task_kind: str | None = Field(
        None, description="Task kind such as chat or workflow"
    )
    task_source: str | None = Field(
        None, description="Task source such as api or scheduler"
    )
    task_scope: str | None = Field(None, description="Task scope")
    task_label: str | None = Field(None, description="User-readable task label")
    goal_id: str | None = Field(
        None, description="Optional goal_ops goal id associated with the mutation"
    )
    step_id: str | None = Field(
        None, description="Optional goal_ops step id associated with the mutation"
    )
    path: str = Field(..., description="Vault-relative mutated path")
    related_path: str | None = Field(
        None, description="Related vault-relative path for paired mutations"
    )
    target_kind: Literal["file", "directory"] = Field(
        ..., description="Mutation target kind"
    )
    operation: str = Field(..., description="Mutation operation")
    status: str = Field(..., description="Mutation outcome")
    event_sequence: int | None = Field(
        None, description="Linked vault file event sequence"
    )
    before_exists: bool = Field(
        ..., description="Whether the file existed before mutation"
    )
    before_hash: str | None = Field(None, description="Content hash before mutation")
    before_snapshot_id: int | None = Field(
        None, description="Retained pre-mutation file snapshot id"
    )
    after_exists: bool = Field(
        ..., description="Whether the file existed after mutation"
    )
    after_hash: str | None = Field(None, description="Content hash after mutation")
    after_snapshot_id: int | None = Field(
        None, description="Retained post-mutation file snapshot id"
    )
    snapshot_ref: str | None = Field(
        None, description="Retained pre-mutation snapshot reference"
    )
    created_at: datetime = Field(..., description="Mutation timestamp")
    expires_at: datetime | None = Field(
        None, description="Snapshot retention expiration"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Operation metadata"
    )


class VaultActivityGroupInfo(BaseModel):
    """Vault mutations grouped by one attributed activity."""

    activity_id: str = Field(..., description="Activity group id")
    activity_kind: str = Field(
        ..., description="Activity kind such as chat or workflow"
    )
    activity_label: str = Field(..., description="User-facing activity label")
    chat_session_id: str | None = Field(
        None, description="Chat session id for chat activity groups"
    )
    chat_session_title: str | None = Field(
        None, description="User-defined chat session title"
    )
    chat_session_created_at: str | None = Field(
        None, description="Chat session creation timestamp"
    )
    chat_session_last_activity_at: str | None = Field(
        None, description="Chat session last activity timestamp"
    )
    status: str = Field(..., description="Durable activity outcome")
    rollback_status: str | None = Field(
        None, description="Rollback outcome when applicable"
    )
    task_id: str | None = Field(None, description="Execution task id when task-backed")
    task_kind: str | None = Field(
        None, description="Task kind such as chat or workflow"
    )
    task_source: str | None = Field(
        None, description="Task source such as api or scheduler"
    )
    task_scope: str | None = Field(None, description="Task scope")
    task_label: str | None = Field(None, description="User-readable task label")
    goal_id: str | None = Field(
        None, description="Optional goal_ops goal id associated with the activity"
    )
    step_id: str | None = Field(
        None, description="Optional goal_ops step id associated with the activity"
    )
    vault_id: str = Field(..., description="Stable vault id")
    vault_name: str = Field(..., description="Vault name at mutation time")
    mutation_count: int = Field(..., description="Number of returned path mutations")
    operation_count: int = Field(..., description="Number of logical operations")
    first_mutation_at: datetime = Field(
        ..., description="First returned mutation timestamp"
    )
    last_mutation_at: datetime = Field(
        ..., description="Last returned mutation timestamp"
    )
    expires_at: datetime | None = Field(
        None, description="Earliest activity data expiration"
    )
    mutations: list[VaultMutationInfo] = Field(
        default_factory=list, description="Returned mutations"
    )


class VaultActivityResponse(BaseModel):
    """Response for recent attributed vault activity."""

    vault_name: str = Field(..., description="Requested vault name")
    groups: list[VaultActivityGroupInfo] = Field(
        default_factory=list,
        description="Recent attributed activity groups",
    )


class VaultActivityRollbackIssueInfo(BaseModel):
    """One reason an activity rollback is currently unavailable."""

    code: str = Field(..., description="Stable rollback availability code")
    message: str = Field(..., description="User-readable rollback availability detail")
    path: str | None = Field(None, description="Affected vault-relative path")


class VaultActivityRollbackPathInfo(BaseModel):
    """One exact path transition in an activity rollback preview."""

    path: str = Field(..., description="Vault-relative path")
    action: Literal["restore", "delete"] = Field(..., description="Rollback action")
    expected_exists: bool = Field(..., description="Expected current existence state")
    expected_sha256: str | None = Field(
        None, description="Expected current content hash"
    )
    restore_exists: bool = Field(
        ..., description="Whether rollback restores file content"
    )
    restore_sha256: str | None = Field(None, description="Restored content hash")


class VaultActivityRollbackPreviewResponse(BaseModel):
    """Current all-or-nothing rollback availability for one activity."""

    activity_id: str
    activity_label: str
    vault_name: str
    can_rollback: bool
    restore_count: int
    delete_count: int
    paths: list[VaultActivityRollbackPathInfo] = Field(default_factory=list)
    issues: list[VaultActivityRollbackIssueInfo] = Field(default_factory=list)


class VaultActivityRollbackExpectedState(BaseModel):
    """One path state confirmed by an activity rollback client."""

    path: str
    exists: bool
    sha256: str | None = None


class VaultActivityRollbackRequest(BaseModel):
    """Expected states from the rollback preview being confirmed."""

    expected_states: list[VaultActivityRollbackExpectedState]


class VaultActivityRollbackResponse(BaseModel):
    """Result of a completed explicit activity rollback."""

    success: bool
    source_activity_id: str
    rollback_activity_id: str
    vault_name: str
    restored_count: int
    deleted_count: int
    message: str


class VaultStateCleanupResponse(BaseModel):
    """Response for manual vault-state cleanup."""

    success: bool = Field(..., description="Whether cleanup completed")
    expired_activity_rows_deleted: int = Field(
        ..., description="Deleted expired activity rows"
    )
    expired_mutation_rows_deleted: int = Field(
        ..., description="Deleted expired mutation rows"
    )
    expired_snapshot_rows_deleted: int = Field(
        ..., description="Deleted expired snapshot rows"
    )
    snapshot_files_deleted: int = Field(..., description="Deleted snapshot files")
    snapshot_dirs_deleted: int = Field(..., description="Deleted snapshot directories")
    message: str = Field(..., description="Human-readable cleanup summary")


class ExecuteWorkflowResponse(BaseModel):
    """Response model for starting manual workflow execution."""

    success: bool = Field(..., description="Whether workflow execution was started")
    global_id: str = Field(..., description="Workflow global ID that was started")
    status: str = Field(..., description="Current execution task status")
    task: ExecutionTaskInfo = Field(
        ..., description="Execution task created for this workflow run"
    )
    message: str = Field(..., description="Human-readable execution summary")


class ChatTaskStartResponse(BaseModel):
    """Response model for starting task-owned streaming chat execution."""

    session_id: str = Field(..., description="Session identifier")
    task: ExecutionTaskInfo = Field(
        ..., description="Execution task created for this chat run"
    )


class ExecutionTaskInfo(BaseModel):
    """Process-local execution task snapshot."""

    task_id: str = Field(..., description="Execution task identifier")
    kind: str = Field(..., description="Task kind, such as chat or workflow")
    scope: str = Field(..., description="Task scope")
    source: str = Field(
        ..., description="Task source, such as api, scheduler, tool, or system"
    )
    label: str = Field(..., description="User-readable task label")
    status: str = Field(..., description="Task lifecycle status")
    created_at: datetime = Field(..., description="Task creation timestamp")
    started_at: datetime | None = Field(None, description="Task start timestamp")
    finished_at: datetime | None = Field(None, description="Task terminal timestamp")
    cancel_requested: bool = Field(
        False, description="Whether cancellation has been requested"
    )
    terminal_reason: str | None = Field(
        None, description="Terminal reason when available"
    )
    latest_event: str | None = Field(None, description="Latest task lifecycle event")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Task metadata")


class ExecutionTaskListResponse(BaseModel):
    """Response model for execution task listing."""

    tasks: list[ExecutionTaskInfo] = Field(
        default_factory=list, description="Matching task snapshots"
    )


class ExecutionTaskCancelResponse(BaseModel):
    """Response model for execution task cancellation."""

    task: ExecutionTaskInfo = Field(
        ..., description="Task snapshot after cancellation request"
    )
    cancelled: bool = Field(
        ..., description="Whether the task was already or newly cancelled"
    )


class ModelInfo(BaseModel):
    """Model metadata for UI configuration."""

    name: str = Field(..., description="User-friendly model name")
    provider: str = Field(..., description="Provider (anthropic, openai, google, etc.)")
    model_string: str = Field(..., description="Actual model identifier")
    capabilities: list[str] = Field(
        default_factory=lambda: ["text"],
        description="Declared model capabilities (e.g. text, vision)",
    )
    dimensions: int | None = Field(
        None,
        description="Embedding vector dimensions when this is an embedding model alias",
    )
    available: bool = Field(
        True, description="Whether required credentials are configured"
    )
    user_editable: bool = Field(
        True, description="If the model mapping is user-editable via UI"
    )
    description: str | None = Field(
        None, description="Optional human-readable description"
    )
    status_message: str | None = Field(
        None, description="Optional availability warning or guidance"
    )


class ToolInfo(BaseModel):
    """Tool metadata for UI configuration."""

    name: str = Field(..., description="Tool name")
    description: str = Field(..., description="Tool description")
    requires_secrets: list[str] = Field(
        default_factory=list, description="Secret names required for activation"
    )
    available: bool = Field(
        True, description="Whether required credentials are configured"
    )
    user_editable: bool = Field(
        False, description="If the tool entry is user-editable via UI"
    )
    chat_visible: bool = Field(
        True, description="Whether the tool should be exposed in chat metadata/UI"
    )


class ProviderInfo(BaseModel):
    """Provider configuration metadata."""

    name: str = Field(..., description="Provider name")
    api_key: str | None = Field(
        None, description="Secret name containing the API key, if required"
    )
    base_url: str | None = Field(
        None, description="Secret name or direct URL for custom endpoints"
    )
    user_editable: bool = Field(
        False, description="If the provider entry can be edited via UI"
    )
    api_key_has_value: bool = Field(
        False, description="True if the API key secret currently has a value"
    )
    base_url_has_value: bool = Field(
        False, description="True if the base URL secret or literal value is set"
    )
    status_message: str | None = Field(
        None, description="Optional availability warning or guidance"
    )
    configured_auth_mode: str | None = Field(
        None,
        description="Configured provider auth mode when the provider supports auth modes.",
    )
    effective_auth_mode: str | None = Field(
        None,
        description="Runtime auth mode after global overrides are applied.",
    )
    oauth_enabled: bool = Field(
        False,
        description="True when OpenAI OAuth behavior is globally enabled.",
    )
    oauth_status: str | None = Field(
        None,
        description="Sanitized OAuth connection status for providers that support OAuth.",
    )
    oauth_disabled_reason: str | None = Field(
        None,
        description="Reason OAuth is unavailable or ignored, when applicable.",
    )
    oauth_api_key_fallback_enabled: bool = Field(
        False,
        description="True when OAuth failures may explicitly fall back to API-key auth.",
    )
    oauth_api_key_fallback_available: bool = Field(
        False,
        description="True when an API-key fallback secret is configured.",
    )
    oauth_account_id: str | None = Field(
        None,
        description="Sanitized connected OpenAI account identifier, when available.",
    )
    oauth_expires_at: str | None = Field(
        None,
        description="OAuth token expiry timestamp, when available.",
    )
    oauth_last_refresh_at: str | None = Field(
        None,
        description="Last successful OAuth refresh timestamp, when available.",
    )
    oauth_last_refresh_error: str | None = Field(
        None,
        description="Sanitized OAuth refresh failure category or message.",
    )
    oauth_pending_expires_at: str | None = Field(
        None,
        description="Pending OAuth connection expiry timestamp, when available.",
    )
    oauth_pending_flow: str | None = Field(
        None,
        description="Pending OAuth connection flow, when available.",
    )
    oauth_device_verification_url: str | None = Field(
        None,
        description="Device-code verification URL for pending OpenAI OAuth.",
    )
    oauth_device_user_code: str | None = Field(
        None,
        description="Device-code user code for pending OpenAI OAuth.",
    )
    oauth_device_poll_interval_seconds: int | None = Field(
        None,
        description="Recommended device-code polling interval in seconds.",
    )
    restart_required: bool = Field(
        False,
        description="True when recent edits require a full restart to take effect.",
    )


class IngestionCapabilityInfo(BaseModel):
    """Availability and supported features for one ingestion strategy."""

    available: bool
    provider: str
    missing: list[str] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    default_order: list[str] = Field(default_factory=list)


class MetadataResponse(BaseModel):
    """Unified metadata response for vaults, models, and tools."""

    vaults: list[str] = Field(..., description="Available vault names")
    models: list[ModelInfo] = Field(..., description="Available models")
    tools: list[ToolInfo] = Field(..., description="Available tools")
    settings: dict[str, Any] = Field(
        default_factory=dict,
        description="Selected settings values for UI hints.",
    )
    ingestion_capabilities: dict[str, IngestionCapabilityInfo] = Field(
        default_factory=dict,
        description="Backend-derived ingestion strategy availability and features.",
    )
    default_context_script: str | None = Field(
        None,
        description="Default context script name for chat sessions.",
    )


class TemplateInfo(BaseModel):
    """Context template metadata for UI selection."""

    name: str = Field(..., description="Template filename")
    source: str = Field(..., description="Template source: vault or system")
    path: str | None = Field(None, description="Full path to template, if available")


class ChatWorkspaceInfo(BaseModel):
    """Vault-relative workspace directory associated with a chat session."""

    path: str = Field("", description="Vault-relative workspace directory path")
    exists: bool = Field(False, description="Whether the workspace directory exists")


class ChatSessionInfo(BaseModel):
    """Persisted chat session summary for UI selection."""

    session_id: str = Field(..., description="Session identifier")
    created_at: str = Field(..., description="Session creation timestamp")
    last_activity_at: str = Field(..., description="Most recent activity timestamp")
    title: str | None = Field(None, description="User-defined title, if set")
    workspace: ChatWorkspaceInfo | None = Field(
        None, description="Workspace associated with this session"
    )
    chat_mode: Literal["normal", "inline_edit"] = Field(
        "normal", description="Selected session chat mode"
    )
    has_summary: bool = Field(
        False, description="Whether a session summary record exists"
    )


class ChatSessionWorkspaceRequest(BaseModel):
    """Request to set or clear a chat session workspace."""

    vault_name: str = Field(..., description="Owning vault name")
    path: str | None = Field(
        None, description="Vault-relative workspace directory path"
    )


class ChatSessionModeRequest(BaseModel):
    """Request to change a persisted chat session mode."""

    vault_name: str = Field(..., description="Owning vault name")
    chat_mode: Literal["normal", "inline_edit"] = Field(
        ..., description="Selected chat mode"
    )


class ChatSessionModeResponse(BaseModel):
    """Persisted chat session mode."""

    session_id: str = Field(..., description="Session identifier")
    chat_mode: Literal["normal", "inline_edit"] = Field(
        ..., description="Selected chat mode"
    )


class ChatSessionForkRequest(BaseModel):
    """Request to fork one persisted chat session."""

    vault_name: str = Field(..., description="Owning vault name")
    through_sequence_index: int = Field(
        ...,
        ge=0,
        description="Persisted message sequence index to fork through, inclusive",
    )


class ChatSessionForkResponse(BaseModel):
    """Response returned after creating a forked chat session."""

    session: ChatSessionInfo = Field(..., description="New forked session summary")
    source_session_id: str = Field(..., description="Source session identifier")
    through_sequence_index: int = Field(
        ..., description="Inclusive source message sequence fork point"
    )
    copied_message_count: int = Field(
        ..., description="Number of messages copied into the fork"
    )


class ChatSessionSummaryResponse(BaseModel):
    """Lightweight session summary payload for UI previews."""

    session_id: str = Field(..., description="Session identifier")
    vault_name: str = Field(..., description="Owning vault name")
    has_summary: bool = Field(
        ..., description="Whether a session summary record exists"
    )
    summary: str | None = Field(None, description="Extracted session summary")
    user_intent: str | None = Field(None, description="Extracted user intent")
    created_at: str | None = Field(
        None, description="Session summary creation timestamp"
    )
    updated_at: str | None = Field(None, description="Session summary update timestamp")
    domain: str | None = Field(None, description="Extracted domain")
    work_product: str | None = Field(None, description="Extracted work product")
    workspace_path: str | None = Field(
        None, description="Workspace path stored for this session summary"
    )
    named_entities: str | None = Field(None, description="Extracted named entities")
    source_summary: str | None = Field(None, description="Extracted source summary")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Summary metadata"
    )
    artifacts: list[dict[str, Any]] = Field(
        default_factory=list, description="Linked summary artifacts"
    )
    vector_index: dict[str, Any] = Field(
        default_factory=dict, description="Vector index coverage"
    )


class ChatSessionSummaryUpdateRequest(BaseModel):
    """Request to manually update a session summary record."""

    summary: str | None = Field(None, description="Replacement summary")
    domain: str | None = Field(None, description="Replacement domain")
    work_product: str | None = Field(None, description="Replacement work product")
    user_intent: str | None = Field(None, description="Replacement user intent")
    workspace_path: str | None = Field(None, description="Replacement workspace path")
    named_entities: str | None = Field(None, description="Replacement named entities")
    source_summary: str | None = Field(None, description="Replacement source summary")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Replacement summary metadata"
    )


class ChatSessionTitleRequest(BaseModel):
    """Request to set or clear the user-defined title for a session."""

    vault_name: str = Field(..., description="Owning vault name")
    title: str | None = Field(None, description="New title; null or empty clears it")


class ChatSessionExportRequest(BaseModel):
    """Request to export one persisted chat session as markdown."""

    vault_name: str = Field(..., description="Owning vault name")


class ChatSessionRetryRequest(BaseModel):
    """Request to retry the latest unfinished chat turn."""

    vault_name: str = Field(..., description="Owning vault name")


class ChatSessionMessageInfo(BaseModel):
    """Persisted normalized chat message for session rehydration."""

    sequence_index: int = Field(
        ..., description="Stable sequence index within the session"
    )
    fork_sequence_index: int | None = Field(
        None,
        description="Effective inclusive message sequence to use when forking from this rendered message",
    )
    role: str = Field(..., description="Normalized role for rendering")
    content: str = Field(..., description="Normalized rendered message content")
    thinking_content: str = Field(
        "", description="Persisted provider reasoning/thinking content for display"
    )
    message_type: str = Field(..., description="Provider-native message class name")
    direction: str = Field(
        ..., description="Request/response direction for the provider-native message"
    )
    is_tool_message: bool = Field(
        False, description="Whether this row represents a tool call/return message"
    )
    tool_call_ids: list[str] = Field(
        default_factory=list, description="Tool calls declared by this message"
    )
    tool_return_ids: list[str] = Field(
        default_factory=list, description="Tool returns declared by this message"
    )


class ChatSessionToolEventInfo(BaseModel):
    """Persisted structured tool event for explicit detail inspection."""

    tool_call_id: str = Field(..., description="Tool call identifier")
    tool_name: str = Field(..., description="Tool name")
    event_type: str = Field(
        ..., description="Event type such as call, result, or overflow_cached"
    )
    created_at: str = Field(..., description="Event timestamp")
    args: dict[str, Any] | None = Field(
        None, description="Tool arguments when captured"
    )
    result_text: str | None = Field(None, description="Tool result text or summary")
    result_metadata: dict[str, Any] = Field(
        default_factory=dict, description="Structured tool result metadata"
    )
    artifact_ref: str | None = Field(
        None, description="Cache/artifact reference when present"
    )


class ChatSessionToolCallInfo(BaseModel):
    """Non-confidential tool-call metadata for session rehydration."""

    tool_call_id: str = Field(..., description="Tool call identifier")
    tool_name: str = Field(..., description="Tool name")
    status: Literal["running", "completed", "failed", "interrupted"] = Field(
        ..., description="Tool call lifecycle state"
    )
    token_count: int | None = Field(
        None, description="Estimated token count for the tool result"
    )


class ChatToolCallDetailResponse(BaseModel):
    """Persisted full-detail payload for one session-owned tool call."""

    session_id: str = Field(..., description="Session identifier")
    tool_call_id: str = Field(..., description="Tool call identifier")
    tool_name: str = Field(..., description="Tool name")
    args: dict[str, Any] | None = Field(
        None, description="Complete persisted tool arguments"
    )
    result_text: str | None = Field(
        None, description="Complete persisted result or cache notice"
    )
    result_metadata: dict[str, Any] = Field(
        default_factory=dict, description="Structured result metadata"
    )
    artifact_ref: str | None = Field(
        None, description="Cache/artifact reference when present"
    )
    events: list[ChatSessionToolEventInfo] = Field(
        default_factory=list, description="Persisted events for this tool call"
    )


class ChatSessionFailureInfo(BaseModel):
    """Internal recovery marker for an accepted chat turn that did not complete."""

    status: str = Field(..., description="Failure marker status")
    phase: str = Field(..., description="Execution phase where the turn failed")
    streaming: bool = Field(..., description="Whether the failed turn was streaming")
    error_type: str = Field(..., description="Stable exception type")
    error: str = Field("", description="Concise failure message")
    failure_kind: str = Field("", description="Stable failure category")
    retryable: bool = Field(
        False, description="Whether retrying the same request may succeed"
    )
    http_status: int | None = Field(
        None, description="Provider HTTP status when available"
    )
    retry_after: str | None = Field(
        None, description="Provider retry-after hint when available"
    )
    model: str | None = Field(None, description="Model selected for the failed turn")
    tools: list[str] = Field(
        default_factory=list, description="Tools selected for the failed turn"
    )
    accepted_user_sequence_index: int = Field(
        ..., description="Accepted user message sequence index"
    )
    recorded_at: str = Field(..., description="Marker timestamp")
    suggested_action: str = Field("", description="Agent-safe recovery guidance")
    manual_retry_count: int = Field(
        0, description="Manual retry attempts started for this marker"
    )
    last_manual_retry_task_id: str | None = Field(
        None, description="Latest manual retry task id"
    )
    last_manual_retry_started_at: str | None = Field(
        None, description="Latest manual retry start timestamp"
    )


class ChatSessionDetailResponse(BaseModel):
    """Persisted chat session payload for client-side rehydration."""

    session_id: str = Field(..., description="Session identifier")
    vault_name: str = Field(..., description="Owning vault name")
    workspace: ChatWorkspaceInfo | None = Field(
        None, description="Workspace associated with this session"
    )
    chat_mode: Literal["normal", "inline_edit"] = Field(
        "normal", description="Selected session chat mode"
    )
    pending_review: DeferredReviewResponse | None = Field(
        None,
        description="Active deferred review that must be resolved before another prompt",
    )
    latest_failure: ChatSessionFailureInfo | None = Field(
        None, description="Latest unfinished-turn marker"
    )
    messages: list[ChatSessionMessageInfo] = Field(
        default_factory=list, description="Persisted messages"
    )
    tool_calls: list[ChatSessionToolCallInfo] = Field(
        default_factory=list,
        description="Effective tool calls with non-confidential lifecycle metadata",
    )


class VaultDirectoryInfo(BaseModel):
    """One child directory in a vault directory listing."""

    name: str = Field(..., description="Directory basename")
    path: str = Field(..., description="Vault-relative directory path")
    has_children: bool = Field(
        False, description="Whether this directory has child directories"
    )


class VaultDirectoryListResponse(BaseModel):
    """Directory listing response for workspace selection."""

    path: str = Field("", description="Listed vault-relative directory path")
    directories: list[VaultDirectoryInfo] = Field(
        default_factory=list, description="Child directories"
    )


class ChatSessionsPurgeRequest(BaseModel):
    """Request to purge old chat sessions for a vault."""

    vault_name: str = Field(..., description="Vault to purge sessions from")
    older_than_days: int | None = Field(
        None, description="Delete sessions older than this many days; null deletes all"
    )


class ChatSessionsPurgeResponse(BaseModel):
    """Result of a chat session purge operation."""

    deleted: int = Field(..., description="Number of sessions deleted")
    message: str = Field(..., description="Human-readable summary")


class GoalCleanupRequest(BaseModel):
    """Request to remove old completed or cancelled goals for a vault."""

    vault_name: str = Field(..., description="Vault to clean goals from")
    status: str = Field(
        "completed",
        description='Goal status filter: "completed", "cancelled", or "completed_or_cancelled"',
    )
    older_than_days: int | None = Field(
        None,
        description="Delete goals older than this many days; null deletes all matches",
    )


class GoalCleanupResponse(BaseModel):
    """Result of a goal cleanup operation."""

    success: bool = Field(True, description="Whether cleanup completed successfully")
    deleted: int = Field(..., description="Number of goals deleted")
    message: str = Field(..., description="Human-readable summary")


class ChatSessionExportResponse(BaseModel):
    """Result of exporting one chat session transcript."""

    session_id: str = Field(..., description="Session identifier")
    filename: str = Field(..., description="Transcript filename created for the export")
    path: str = Field(..., description="Absolute transcript path in the vault")


class ChatHistoryCompactionRequest(BaseModel):
    """Request to compact one persisted chat session."""

    vault_name: str = Field(..., description="Owning vault name")
    focus: str | None = Field(None, description="Optional summary focus instructions")


class ChatHistoryCompactionStatusResponse(BaseModel):
    """Estimated compaction status for one chat session."""

    session_id: str = Field(..., description="Session identifier")
    vault_name: str = Field(..., description="Owning vault name")
    compaction_type: str = Field(..., description="Configured compaction policy")
    messages_before: int = Field(..., description="Current stored message count")
    estimated_tokens_before: int = Field(
        ..., description="Estimated current history tokens"
    )
    compaction_token_threshold: int = Field(
        ..., description="Configured compaction threshold"
    )
    compaction_keep_recent: int = Field(
        ..., description="Target recent message count to keep"
    )
    recommended: bool = Field(
        ..., description="Whether compaction is currently recommended"
    )
    already_compacted: bool = Field(
        ..., description="Whether this session has prior compaction metadata"
    )


class ChatHistoryCompactionResponse(BaseModel):
    """Result of compacting one chat session."""

    session_id: str = Field(..., description="Session identifier")
    vault_name: str = Field(..., description="Owning vault name")
    status: str = Field(..., description="Compaction status")
    messages_before: int = Field(..., description="Message count before compaction")
    messages_after: int = Field(..., description="Message count after compaction")
    estimated_tokens_before: int = Field(
        ..., description="Estimated tokens before compaction"
    )
    estimated_tokens_after: int = Field(
        ..., description="Estimated tokens after compaction"
    )
    kept_recent: int = Field(..., description="Recent raw messages preserved verbatim")
    summary_message_index: int = Field(..., description="Stored summary message index")
    compaction_id: str = Field(..., description="Compaction audit identifier")
    compacted_at: str = Field(..., description="Compaction timestamp")
    source: str = Field(..., description="Compaction source")


class ModelConfigRequest(BaseModel):
    """Payload for creating or updating a model mapping."""

    provider: str = Field(..., description="Provider name the model uses")
    model_string: str = Field(..., description="Provider-specific model identifier")
    capabilities: list[str] | None = Field(
        None,
        description='Optional model capabilities list (e.g. ["text", "vision"] or ["embedding"])',
    )
    dimensions: int | None = Field(
        None,
        description="Embedding vector dimensions for embedding-capable model aliases",
    )
    description: str | None = Field(
        None, description="Optional description for UI display"
    )


class ProviderConfigRequest(BaseModel):
    """Payload for creating or updating a provider configuration."""

    api_key: str | None = Field(
        None, description="Secret name containing the provider API key"
    )
    base_url: str | None = Field(
        None, description="Either a direct URL or the name of a stored secret"
    )
    auth_mode: Literal["api_key", "oauth"] | None = Field(
        None,
        description="OpenAI auth mode; only supported for the built-in openai provider",
    )
    oauth_api_key_fallback_enabled: bool | None = Field(
        None,
        description="Allow OpenAI OAuth failures to fall back to API-key auth",
    )
    api_key_value: str | None = Field(
        None, description="Optional API key value to persist in the secrets store"
    )
    base_url_value: str | None = Field(
        None, description="Optional base URL value to persist in the secrets store"
    )


class OpenAIOAuthStartRequest(BaseModel):
    """Payload for starting an OpenAI OAuth connection."""

    redirect_uri: str | None = Field(
        None,
        description="Optional callback URI; defaults to the Codex loopback callback",
    )


class OpenAIOAuthStartResponse(BaseModel):
    """Bootstrap response for an OpenAI OAuth connection attempt."""

    auth_url: str = Field(..., description="Authorization URL to open in a browser")
    state: str = Field(
        ..., description="Opaque OAuth state for this connection attempt"
    )
    redirect_uri: str = Field(..., description="Callback URI bound to this attempt")
    expires_at: str = Field(..., description="Pending connection expiry timestamp")


class OpenAIOAuthDeviceStartResponse(BaseModel):
    """Bootstrap response for an OpenAI OAuth device-code connection attempt."""

    verification_url: str = Field(..., description="URL where the user enters the code")
    user_code: str = Field(..., description="Short device code to enter")
    expires_at: str = Field(..., description="Pending connection expiry timestamp")
    poll_interval_seconds: int = Field(
        ...,
        description="Recommended polling interval in seconds",
    )


class OpenAIOAuthCompleteRequest(BaseModel):
    """Payload for completing OpenAI OAuth manually."""

    redirect_url: str | None = Field(
        None,
        description="Full pasted redirect URL containing code and state",
    )
    code: str | None = Field(None, description="Authorization code")
    state: str | None = Field(None, description="OAuth state")


class OpenAIOAuthDeviceCheckResponse(BaseModel):
    """Response for checking an OpenAI OAuth device-code connection attempt."""

    status: str = Field(..., description="Current device-code status")
    provider: ProviderInfo = Field(..., description="Updated OpenAI provider status")


class OperationResult(BaseModel):
    """Generic success response for configuration operations."""

    success: bool = Field(True, description="Whether the operation succeeded")
    message: str = Field(..., description="Human-readable summary")
    restart_required: bool = Field(
        False,
        description="True when a full restart is still required for secret changes.",
    )


class CachePurgeResponse(BaseModel):
    """Response model for manual cache maintenance operations."""

    success: bool = Field(True, description="Whether the purge completed successfully")
    message: str = Field(..., description="Human-readable purge summary")
    purged_count: int = Field(
        ..., description="Number of expired cache artifacts removed"
    )


class SystemTemplateSeedResponse(BaseModel):
    """Response model for manual system authoring template refresh."""

    success: bool = Field(
        ..., description="Whether the refresh completed without copy errors"
    )
    message: str = Field(..., description="Human-readable refresh summary")
    created: list[str] = Field(
        default_factory=list, description="System template files created"
    )
    updated: list[str] = Field(
        default_factory=list, description="System template files overwritten"
    )
    skipped: list[str] = Field(
        default_factory=list, description="System template files left unchanged"
    )
    errors: list[str] = Field(
        default_factory=list, description="Copy errors encountered during refresh"
    )


class SystemMigrationTargetInfo(BaseModel):
    """Migration status for one managed system database."""

    db_name: str = Field(..., description="System database name")
    namespace: str = Field(
        ..., description="Migration namespace tracked inside the database"
    )
    db_path: str = Field(..., description="Filesystem path to the database")
    exists: bool = Field(..., description="Whether the database file currently exists")
    applied_versions: list[int] = Field(
        default_factory=list, description="Applied migration versions"
    )
    pending_versions: list[int] = Field(
        default_factory=list, description="Pending migration versions"
    )
    backup_path: str | None = Field(
        None, description="Backup created during the latest migration run"
    )
    inspection_error: str | None = Field(
        None, description="Why migration versions could not be inspected"
    )


class SystemMigrationStatusResponse(BaseModel):
    """Response containing system database migration status."""

    success: bool = Field(
        True, description="Whether the status request completed successfully"
    )
    message: str = Field(..., description="Human-readable migration status summary")
    system_root: str = Field(
        ..., description="Filesystem path to the active system directory"
    )
    pending_count: int = Field(..., description="Total pending migration versions")
    targets: list[SystemMigrationTargetInfo] = Field(default_factory=list)


class SystemMigrationRunRequest(BaseModel):
    """Request payload for running system database migrations."""

    backup: bool = Field(
        True,
        description="Create timestamped backups before applying pending migrations",
    )


class SystemMigrationRunResponse(SystemMigrationStatusResponse):
    """Response containing final status after running system database migrations."""

    backups_created: list[str] = Field(
        default_factory=list, description="Backup files created during the run"
    )


class SecretInfo(BaseModel):
    """Information about a stored secret without revealing its value."""

    name: str = Field(..., description="Secret name")
    has_value: bool = Field(..., description="True if the secret currently has a value")
    stored: bool = Field(
        False, description="True when the secret exists in the user-writable store"
    )


class SecretUpdateRequest(BaseModel):
    """Request payload for setting or updating a stored secret."""

    name: str = Field(..., description="Secret name")
    value: str | None = Field(
        None, description="New value for the secret (empty to clear)"
    )


class MCPStdioConfigInfo(BaseModel):
    """Structured launch definition in the advanced shell."""

    model_config = ConfigDict(extra="forbid")

    executable: str = Field(..., min_length=1, max_length=2048)
    arguments: list[str] = Field(default_factory=list, max_length=64)
    working_directory: str = Field(..., min_length=1, max_length=2048)
    environment: dict[str, str] = Field(default_factory=dict)
    roots: list[str] = Field(default_factory=list, max_length=16)


class MCPConnectionImportRequest(BaseModel):
    """Strict YAML or JSON advanced-shell stdio configuration supplied by a user."""

    model_config = ConfigDict(extra="forbid")

    configuration: str = Field(..., min_length=1, max_length=65536)


class MCPConnectionCreateRequest(BaseModel):
    """Create one current-principal MCP connection."""

    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(..., min_length=1, max_length=120)
    url: str | None = Field(None, min_length=1, max_length=2048)
    transport: Literal["streamable_http", "sse", "advanced_shell_stdio"] = (
        "streamable_http"
    )
    auth_mode: Literal["none", "bearer", "header", "oauth"] = "none"
    header_name: str | None = Field(None, max_length=128)
    enabled: bool = True
    allow_private_http: bool = False
    allowed_tools: list[str] | None = None
    credential: SecretStr | None = Field(None, max_length=16384)
    oauth_client_id: str | None = Field(None, max_length=2048)
    oauth_client_secret: SecretStr | None = Field(None, max_length=16384)
    oauth_scopes: list[str] | None = None
    stdio: MCPStdioConfigInfo | None = None


class MCPConnectionUpdateRequest(BaseModel):
    """Replace mutable current-principal MCP connection settings."""

    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(..., min_length=1, max_length=120)
    url: str | None = Field(None, min_length=1, max_length=2048)
    transport: Literal["streamable_http", "sse", "advanced_shell_stdio"]
    auth_mode: Literal["none", "bearer", "header", "oauth"]
    header_name: str | None = Field(None, max_length=128)
    enabled: bool
    allow_private_http: bool = False
    allowed_tools: list[str] | None = None
    oauth_client_id: str | None = Field(None, max_length=2048)
    oauth_scopes: list[str] | None = None
    stdio: MCPStdioConfigInfo | None = None


class MCPCredentialUpdateRequest(BaseModel):
    """Write-only static credential update."""

    model_config = ConfigDict(extra="forbid")

    credential: SecretStr = Field(..., min_length=1, max_length=16384)


class MCPOAuthClientSecretUpdateRequest(BaseModel):
    """Write-only pre-registered OAuth client secret update."""

    model_config = ConfigDict(extra="forbid")

    client_secret: SecretStr = Field(..., min_length=1, max_length=16384)


class MCPConnectionInfo(BaseModel):
    """Sanitized MCP connection metadata returned to the current user."""

    connection_id: str
    slug: str
    display_name: str
    url: str | None
    transport: Literal["streamable_http", "sse", "advanced_shell_stdio"]
    auth_mode: Literal["none", "bearer", "header", "oauth"]
    header_name: str | None
    enabled: bool
    allow_private_http: bool
    allowed_tools: list[str] | None
    credential_present: bool
    oauth_client_id: str | None
    oauth_client_secret_present: bool
    oauth_scopes: list[str] | None
    oauth_redirect_uri: str | None
    oauth_redirect_source: Literal["configured", "browser_fallback"]
    config_version: int
    created_at: str
    updated_at: str
    stdio: MCPStdioConfigInfo | None = None


class MCPConnectionTestResponse(BaseModel):
    """Sanitized MCP connection readiness result."""

    status: str
    ready: bool
    tool_count: int | None
    tool_names: list[str] = Field(default_factory=list)
    message: str


class MCPOAuthStartRequest(BaseModel):
    """Start a headless-safe MCP OAuth connection attempt."""

    model_config = ConfigDict(extra="forbid")

    redirect_uri: str | None = Field(None, max_length=2048)


class MCPOAuthStartResponse(BaseModel):
    """Authorization URL and expiry for an MCP OAuth attempt."""

    auth_url: str
    state: str
    redirect_uri: str
    expires_at: str
    redirect_source: Literal["configured", "browser_fallback"]


class MCPOAuthCompleteRequest(BaseModel):
    """Complete MCP OAuth from a pasted redirect or explicit values."""

    model_config = ConfigDict(extra="forbid")

    redirect_url: str | None = Field(None, max_length=4096)
    code: str | None = Field(None, max_length=4096)
    state: str | None = Field(None, max_length=4096)


class MCPOAuthStatusResponse(BaseModel):
    """Sanitized current OAuth state for one MCP connection."""

    status: str
    connected: bool
    pending_expires_at: str | None = None


class GmailConnectionPreferencesRequest(BaseModel):
    """Principal-owned Gmail result and content limits."""

    model_config = ConfigDict(extra="forbid")

    search_default_results: int = Field(20, ge=1, le=500)
    search_max_results: int = Field(100, ge=1, le=500)
    message_max_characters: int = Field(50_000, ge=1, le=250_000)
    thread_max_messages: int = Field(25, ge=1, le=100)
    attachment_download_enabled: bool = False
    attachment_max_mb: int = Field(25, ge=1, le=100)
    draft_creation_enabled: bool = False
    draft_max_characters: int = Field(50_000, ge=1, le=250_000)


class GoogleConnectionUpdateRequest(BaseModel):
    """Replace current-principal Google connection metadata."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(None, min_length=1, max_length=120)
    client_id: str = Field(..., min_length=1, max_length=2048)
    is_default: bool | None = None
    gmail: GmailConnectionPreferencesRequest = Field(
        default_factory=lambda: GmailConnectionPreferencesRequest(
            search_default_results=20,
            search_max_results=100,
            message_max_characters=50_000,
            thread_max_messages=25,
            attachment_download_enabled=False,
            attachment_max_mb=25,
            draft_creation_enabled=False,
            draft_max_characters=50_000,
        )
    )


class GoogleClientSecretUpdateRequest(BaseModel):
    """Write-only Google OAuth client secret update."""

    model_config = ConfigDict(extra="forbid")

    client_secret: SecretStr = Field(..., min_length=1, max_length=16384)


class GoogleConnectionResponse(BaseModel):
    """Sanitized Google connection configuration and authorization state."""

    connection_id: str | None
    slug: str | None
    display_name: str | None
    is_default: bool
    state: Literal[
        "not_configured", "authorization_required", "ready", "reconnect_required"
    ]
    configured: bool
    connected: bool
    client_id: str | None
    client_secret_present: bool
    account_email: str | None
    granted_scopes: list[str] = Field(default_factory=list)
    config_version: int | None
    gmail: GmailConnectionPreferencesRequest
    gmail_available: bool
    gmail_missing_scopes: list[str] = Field(default_factory=list)
    gmail_draft_available: bool
    gmail_draft_missing_scopes: list[str] = Field(default_factory=list)
    oauth_redirect_uri: str | None


class GoogleConnectionCreateRequest(GoogleConnectionUpdateRequest):
    """Create a named principal-owned Google connection."""

    display_name: str = Field(..., min_length=1, max_length=120)
    is_default: bool = False


class GoogleOAuthStartResponse(BaseModel):
    """Google authorization URL and pending request details."""

    authorization_url: str
    redirect_uri: str
    expires_at: str
    requested_scopes: list[str]


class GoogleOAuthCompleteRequest(BaseModel):
    """Complete Google OAuth from a callback or pasted redirect URL."""

    model_config = ConfigDict(extra="forbid")

    redirect_url: str | None = Field(None, max_length=4096)
    code: str | None = Field(None, max_length=4096)
    state: str | None = Field(None, max_length=4096)


class SystemActivityEntryInfo(BaseModel):
    """One parsed retained System Activity entry."""

    id: str = Field(..., description="Stable identifier within the retained log window")
    timestamp: datetime = Field(..., description="Event timestamp")
    level: str = Field(..., description="Normalized log level")
    tag: str = Field(..., description="Subsystem tag")
    message: str = Field(..., description="Diagnostic message")
    boot_id: int | None = Field(None, description="Runtime boot id when available")
    data: dict[str, Any] = Field(
        default_factory=dict, description="Structured diagnostic metadata"
    )


class SystemLogResponse(BaseModel):
    """One structured page from retained System Activity."""

    entries: list[SystemActivityEntryInfo] = Field(
        default_factory=list,
        description="Parsed entries ordered newest first",
    )
    next_cursor: str | None = Field(
        None, description="Opaque cursor for the next older page"
    )
    earliest_retained_timestamp: datetime | None = Field(
        None,
        description="Timestamp of the oldest retained parseable entry",
    )
    total_matching: int = Field(
        0, description="Matching entries at and older than this page"
    )
    retained_size_bytes: int = Field(
        0, description="Total bytes across retained activity segments"
    )
    available_levels: list[str] = Field(
        default_factory=list, description="Levels present in retained history"
    )
    available_tags: list[str] = Field(
        default_factory=list, description="Tags present in retained history"
    )


class SystemSettingsResponse(BaseModel):
    """Response containing settings configuration for editing."""

    path: str = Field(..., description="Filesystem path to the active settings file")
    content: str = Field(..., description="Raw YAML content of the settings file")
    size_bytes: int = Field(..., description="Total size of the settings file in bytes")


class UpdateSettingsRequest(BaseModel):
    """Request payload when updating settings YAML content."""

    content: str = Field(..., description="New YAML content to persist")


class SettingInfo(BaseModel):
    """Information about a general (non-secret) application setting."""

    key: str = Field(..., description="Setting name")
    value: str = Field(..., description="Current value rendered as string")
    description: str | None = Field(None, description="Human-readable description")
    category: str | None = Field(None, description="Settings UI grouping label")
    restart_required: bool = Field(
        False, description="True when edits recommend a restart"
    )


class SettingUpdateRequest(BaseModel):
    """Request payload for updating a general setting value."""

    value: str = Field(..., description="New value for the setting")


class ErrorResponse(BaseModel):
    """Standard error response model."""

    success: bool = Field(False, description="Always false for error responses")
    error: str = Field(..., description="Error type or category")
    message: str = Field(..., description="Human-readable error message")
    details: dict | None = Field(None, description="Additional error details")


class WorkflowLoadErrorsResponse(BaseModel):
    """Structured workflow load errors for authoring and repair loops."""

    errors: list[ConfigurationError] = Field(
        default_factory=list,
        description="Workflow configuration errors discovered during loading",
    )


class WorkflowRunInfo(BaseModel):
    """One durable workflow execution attempt."""

    run_id: str
    workflow_id: str
    workflow_name: str
    vault_name: str
    source: str
    status: str
    queued_at: datetime
    task_id: str | None = None
    step_name: str | None = None
    scheduled_run_time: datetime | None = None
    reason: str | None = None
    message: str | None = None
    failure: dict[str, Any] | None = None
    output_files: list[str] = Field(default_factory=list)
    execution_time_seconds: float | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class WorkflowRunHistoryResponse(BaseModel):
    """Recent durable runs for one workflow."""

    workflow_id: str
    runs: list[WorkflowRunInfo] = Field(default_factory=list)


#######################################################################
## Internal Data Models
#######################################################################


class WorkflowSummary(BaseModel):
    """Summary information about a workflow for internal use."""

    global_id: str
    name: str
    vault: str
    enabled: bool
    run_type: str
    schedule_cron: str | None
    description: str


class SystemWorkflowTemplateSummary(BaseModel):
    """Summary information about a packaged system workflow template."""

    name: str
    run_type: str
    enabled: bool
    schedule_cron: str | None
    description: str
    path: str


class ConfigurationError(BaseModel):
    """Configuration error information for API responses."""

    vault: str
    workflow_name: str | None = Field(None, description="Workflow name if determinable")
    file_path: str
    error_message: str
    error_type: str
    timestamp: datetime
    connection_id: str | None = None
    slug: str | None = None
    display_name: str | None = None
    is_default: bool = False
