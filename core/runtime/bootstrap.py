"""
Runtime bootstrap for AssistantMD system.

Provides single entry point for initializing all runtime services
with proper configuration, error handling, and lifecycle management.
"""

import asyncio
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from core.advanced_shell.capability import AdvancedShellCapabilityService
from core.authoring.template_discovery import WorkflowLoader, seed_system_templates
from core.chat.chat_store import ChatStore
from core.chat.session_access import ChatSessionAccessService
from core.connections import BuiltInConnectionService
from core.identity import (
    LOCAL_USER_AUTHORITY,
    AuthorizationService,
    use_execution_authority,
)
from core.ingestion.jobs import fail_processing_jobs
from core.ingestion.service import IngestionService
from core.ingestion.worker import IngestionWorker
from core.integrations.google import (
    GmailResourceService,
    GoogleConnectionService,
    GoogleOAuthCoordinator,
)
from core.logger import UnifiedLogger
from core.mcp import MCPConnectionManager, MCPConnectionService
from core.mcp.oauth import MCPOAuthCoordinator
from core.scheduling.database import create_job_store
from core.scheduling.job_history import attach_scheduler_history_listener
from core.secrets import get_encrypted_secrets_service, initialize_secrets_bootstrap
from core.secrets.legacy_migration import migrate_legacy_secrets_yaml
from core.settings import (
    get_mcp_max_concurrent_advanced_shell_stdio_launches,
    validate_settings,
)
from core.settings.store import get_general_settings, refresh_settings_cache
from core.system_migrations import run_system_migrations
from core.vault_state.activity import handle_task_terminal_for_activity
from core.vault_state.rollback import handle_task_terminal_for_rollback
from core.workflow_runs import WorkflowRunStore

from . import state as runtime_state

# Note: Job setup now handled via runtime_context.reload_workflows()
from .background import RuntimeBackgroundSpawner
from .config import RuntimeConfig, RuntimeConfigError
from .context import RuntimeContext
from .execution_tasks import TaskCoordinator
from .paths import set_bootstrap_roots
from .state import clear_runtime_context, set_runtime_context
from .task_access import ExecutionTaskAccessService
from .task_runner import ExecutionTaskRunner
from .workflow_governor import WorkflowGovernor


async def bootstrap_runtime(
    config: RuntimeConfig,
    *,
    advanced_shell: AdvancedShellCapabilityService | None = None,
) -> RuntimeContext:
    """
    Bootstrap AssistantMD runtime with centralized service initialization.

    Creates and configures all core services using the provided configuration,
    establishes scheduler with job persistence, and returns a unified context
    for accessing services throughout the application lifecycle.

    Args:
        config: Runtime configuration with paths and settings

    Returns:
        RuntimeContext with initialized services

    Raises:
        RuntimeBootstrapError: If bootstrap process fails
        RuntimeConfigError: If configuration is invalid
        RuntimeStartupError: If service initialization fails
    """
    logger = UnifiedLogger(tag="runtime-bootstrap")
    logger.info(
        "Starting runtime bootstrap",
        data={"data_root": str(config.data_root)},
    )

    try:
        # Make bootstrap roots available for helpers that run before context is set
        set_bootstrap_roots(config.data_root, config.system_root)
        refresh_settings_cache()
        secrets_status = initialize_secrets_bootstrap(config.system_root)
        migration_status = run_system_migrations(config.system_root, backup=True)
        logger.info(
            "Startup system database migration check completed",
            data={
                "pending_after": migration_status.pending_count,
                "backups_created": sum(
                    1 for target in migration_status.targets if target.backup_path
                ),
            },
        )
        mcp_connections: MCPConnectionService | None = None
        mcp_manager: MCPConnectionManager | None = None
        mcp_oauth: MCPOAuthCoordinator | None = None
        if not secrets_status.ready:
            logger.warning(
                "Encrypted secrets are locked",
                data={
                    "event": "secrets_locked",
                    "reason": secrets_status.reason,
                },
            )
        else:
            secrets_service = get_encrypted_secrets_service()
            migration_result = migrate_legacy_secrets_yaml(
                system_root=config.system_root,
                service=secrets_service,
            )
            logger.info(
                "Legacy secrets migration checked",
                data={
                    "event": "legacy_secrets_migration_checked",
                    "phase": migration_result.phase,
                    "imported_count": migration_result.imported_count,
                    "skipped_oauth_count": migration_result.skipped_oauth_count,
                    "source_retired": migration_result.source_retired,
                },
            )
            manager_holder: list[MCPConnectionManager] = []

            def invalidate_mcp_connection(
                principal_id: str, connection_id: str
            ) -> None:
                if manager_holder:
                    manager_holder[0].invalidate(principal_id, connection_id)

            mcp_connections = MCPConnectionService(
                system_root=str(config.system_root),
                secrets=secrets_service,
                on_change=invalidate_mcp_connection,
                advanced_shell_stdio_enabled=bool(
                    advanced_shell is not None and advanced_shell.enabled
                ),
            )
            mcp_manager = MCPConnectionManager(
                connections=mcp_connections,
                advanced_shell_stdio=(
                    advanced_shell.transport_config
                    if advanced_shell is not None and advanced_shell.enabled
                    else None
                ),
                advanced_shell_readiness=(
                    advanced_shell.readiness
                    if advanced_shell is not None and advanced_shell.enabled
                    else None
                ),
                max_concurrent_stdio_launches=(
                    get_mcp_max_concurrent_advanced_shell_stdio_launches()
                ),
            )
            manager_holder.append(mcp_manager)
            mcp_manager.start()
            mcp_oauth = MCPOAuthCoordinator(
                connections=mcp_connections,
                manager=mcp_manager,
            )
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            refresh_settings_cache()

        # Ensure packaged system templates exist without overwriting runtime edits.
        seed_system_templates(config.system_root)

        # Validate configuration before continuing bootstrap
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            config_status = validate_settings()
        if not config_status.is_healthy:
            error_messages = [
                f"{issue.name}: {issue.message}" for issue in config_status.errors
            ]
            logger.error(
                "Critical configuration validation failed",
                metadata={"errors": error_messages},
            )
            raise RuntimeConfigError("; ".join(error_messages))

        # Ensure env defaults reflect the configured roots before services that read env/context
        import os

        os.environ["CONTAINER_DATA_ROOT"] = str(config.data_root)
        os.environ["CONTAINER_SYSTEM_ROOT"] = str(config.system_root)
        built_in_connections = BuiltInConnectionService(
            system_root=str(config.system_root),
            available=secrets_status.ready,
        )
        google_connection = (
            GoogleConnectionService(
                connections=built_in_connections,
                secrets=get_encrypted_secrets_service(),
            )
            if secrets_status.ready
            else None
        )
        google_oauth = (
            GoogleOAuthCoordinator(
                connections=built_in_connections,
                google=google_connection,
                secrets=get_encrypted_secrets_service(),
            )
            if google_connection is not None
            else None
        )
        gmail = (
            GmailResourceService(
                connections=built_in_connections,
                google=google_connection,
                oauth=google_oauth,
            )
            if google_connection is not None and google_oauth is not None
            else None
        )

        # Initialize workflow loader with configured data root
        workflow_loader = WorkflowLoader(
            _data_root=str(config.data_root), _allow_direct_instantiation=True
        )

        # Initialize ingestion service
        ingestion_service = IngestionService()
        interrupted_job_ids = fail_processing_jobs(
            "Import interrupted by an application restart"
        )
        if interrupted_job_ids:
            logger.warning(
                "Interrupted ingestion jobs reconciled",
                data={
                    "event": "ingestion_jobs_reconciled",
                    "job_ids": interrupted_job_ids,
                    "status": "failed",
                    "reason": "application_restart",
                },
            )
        # Determine ingestion worker interval and batch size from settings (with safe fallbacks)
        ingestion_interval = 120
        ingestion_max_concurrent = (
            config.features.get("ingestion_max_concurrent", 1)
            if isinstance(config.features, dict)
            else 1
        )
        try:
            general_settings = get_general_settings()
            interval_setting = general_settings.get("ingestion_worker_interval_seconds")
            if interval_setting is not None:
                ingestion_interval = int(interval_setting.value)
            try:
                batch_setting = general_settings.get("ingestion_worker_batch_size")
                if batch_setting is not None:
                    ingestion_max_concurrent = int(batch_setting.value)
            except Exception:
                pass
        except Exception:
            pass

        task_coordinator = TaskCoordinator(
            terminal_observers=[
                handle_task_terminal_for_rollback,
                handle_task_terminal_for_activity,
            ],
        )
        background_tasks: set[asyncio.Task] = set()
        background_spawner = RuntimeBackgroundSpawner(
            background_loop=asyncio.get_running_loop(),
            background_tasks=background_tasks,
        )
        task_runner = ExecutionTaskRunner(
            task_coordinator=task_coordinator,
            background_spawner=background_spawner,
        )
        ingestion_worker = IngestionWorker(
            process_job_fn=ingestion_service.process_job,
            max_concurrent=ingestion_max_concurrent,
            task_coordinator=task_coordinator,
            task_runner=task_runner,
        )
        # Create persistent job store for scheduler
        job_store = create_job_store(system_root=str(config.system_root))

        # Initialize scheduler with job store and worker configuration
        jobstores = {"default": job_store}
        scheduler = AsyncIOScheduler(
            jobstores=jobstores, max_workers=config.max_scheduler_workers
        )

        # Start scheduler paused to allow job synchronization.
        # If the job store has stale job references (e.g. from a module rename),
        # wipe it and retry with a clean store so startup isn't blocked.
        try:
            scheduler.start(paused=True)
            attach_scheduler_history_listener(scheduler)
        except Exception as start_err:
            logger.warning(
                "Scheduler failed to start — job store may contain stale references. "
                "Wiping job store and retrying.",
                data={"error": str(start_err)},
            )
            try:
                scheduler.shutdown(wait=False)
            except Exception:
                pass
            job_store = create_job_store(system_root=str(config.system_root), wipe=True)
            scheduler = AsyncIOScheduler(
                jobstores={"default": job_store},
                max_workers=config.max_scheduler_workers,
            )
            scheduler.start(paused=True)
            attach_scheduler_history_listener(scheduler)

        # Create runtime context with all initialized services
        boot_id = runtime_state.next_boot_id()
        started_at = datetime.now(UTC)
        workflow_run_store = WorkflowRunStore(str(config.system_root))
        authorization = AuthorizationService()
        chat_store = ChatStore(str(config.system_root))
        chat_session_access = ChatSessionAccessService(chat_store, authorization)
        execution_task_access = ExecutionTaskAccessService(
            task_coordinator,
            authorization,
        )
        workflow_governor = WorkflowGovernor(
            task_coordinator=task_coordinator,
            task_runner=task_runner,
            workflow_run_store=workflow_run_store,
        )
        runtime_context = RuntimeContext(
            config=config,
            scheduler=scheduler,
            workflow_loader=workflow_loader,
            logger=logger,
            ingestion=ingestion_service,
            ingestion_worker=ingestion_worker,
            ingestion_interval=ingestion_interval,
            task_coordinator=task_coordinator,
            authorization=authorization,
            execution_task_access=execution_task_access,
            chat_store=chat_store,
            chat_session_access=chat_session_access,
            task_runner=task_runner,
            workflow_governor=workflow_governor,
            workflow_run_store=workflow_run_store,
            built_in_connections=built_in_connections,
            google_connection=google_connection,
            google_oauth=google_oauth,
            gmail=gmail,
            mcp_connections=mcp_connections,
            mcp_manager=mcp_manager,
            mcp_oauth=mcp_oauth,
            background_spawner=background_spawner,
            advanced_shell=advanced_shell,
            boot_id=boot_id,
            started_at=started_at,
            background_tasks=background_tasks,
        )

        # Register context globally before job synchronization
        set_runtime_context(runtime_context)

        try:
            # Load workflow configurations and synchronize jobs using runtime context
            await runtime_context.reload_workflows(
                manual=False,
                refresh_vault_state=False,
            )

            # Resume scheduler after successful synchronization
            scheduler.resume()
            runtime_context.start_background_vault_state_refresh(reason="startup")

        except Exception:
            # If job synchronization fails, clean up and rethrow
            scheduler.shutdown(wait=False)
            clear_runtime_context()
            raise

        logger.info(
            "Runtime bootstrap completed successfully",
            data={
                "data_root": str(config.data_root),
                "system_root": str(config.system_root),
                "scheduler_workers": config.max_scheduler_workers,
                "features": config.features,
            },
        )

        return runtime_context

    except Exception as e:
        if not isinstance(e, RuntimeConfigError):
            logger.error(f"Runtime bootstrap failed: {e}")

        # Attempt cleanup of any partially initialized services
        try:
            if "scheduler" in locals() and scheduler and scheduler.running:
                scheduler.shutdown(wait=False)
        except Exception as cleanup_error:
            logger.error(f"Error during bootstrap cleanup: {cleanup_error}")
        try:
            if "mcp_manager" in locals() and mcp_manager is not None:
                await mcp_manager.shutdown()
        except Exception as cleanup_error:
            logger.error(f"Error during MCP manager cleanup: {cleanup_error}")

        if isinstance(e, RuntimeConfigError):
            # Configuration errors retain their public type after partial-start cleanup.
            raise

        raise RuntimeStartupError(f"Failed to bootstrap runtime: {e}") from e


class RuntimeBootstrapError(Exception):
    """Base exception for runtime bootstrap failures."""

    pass


class RuntimeStartupError(RuntimeBootstrapError):
    """Raised when service initialization fails during bootstrap."""

    pass
