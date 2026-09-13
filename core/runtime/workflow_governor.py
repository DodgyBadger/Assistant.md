"""Workflow execution policy built on the process-local task coordinator."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any, cast

from core.authoring.workflow_execution import (
    WorkflowExecutionResult,
    WorkflowExecutionTarget,
    execute_workflow_by_id,
    execute_workflow_target,
)
from core.identity import (
    ExecutionAuthority,
    get_current_execution_authority,
)
from core.logger import UnifiedLogger
from core.settings import (
    get_max_concurrent_workflows,
    get_workflow_task_timeout_seconds,
)
from core.tools.failures import FailureClassification, classify_exception
from core.workflow_runs import WorkflowRunStore

from .execution_tasks import (
    ExecutionTaskKind,
    ExecutionTaskSnapshot,
    ExecutionTaskSource,
    ExecutionTaskStatus,
    TaskCoordinator,
    workflow_vault_scope,
)
from .task_runner import (
    ExecutionGatePolicy,
    ExecutionGateWait,
    ExecutionTaskHooks,
    ExecutionTaskRunner,
    ExecutionTaskSpec,
)

WorkflowSource = ExecutionTaskSource


def _authority_for_workflow_source(
    source: WorkflowSource,
    *,
    authority: ExecutionAuthority | None = None,
) -> ExecutionAuthority:
    """Resolve authority at a workflow execution entrypoint."""
    if authority is not None:
        return authority
    current = get_current_execution_authority()
    if current is not None:
        return current
    raise RuntimeError(
        f"Workflow source '{source}' requires an active execution authority."
    )


class WorkflowGovernor:
    """Coordinate workflow execution lanes and shared workflow task policy."""

    def __init__(
        self,
        *,
        task_coordinator: TaskCoordinator,
        task_runner: ExecutionTaskRunner,
        workflow_run_store: WorkflowRunStore,
        logger: UnifiedLogger | None = None,
    ) -> None:
        self._task_coordinator = task_coordinator
        self._logger = logger or UnifiedLogger(tag="workflow-governor")
        self._task_runner = task_runner
        self._workflow_run_store = workflow_run_store
        self._global_limit_guard = asyncio.Lock()
        self._global_limit = 0
        self._global_semaphore: asyncio.Semaphore | None = None

    async def execute_workflow(
        self,
        *,
        global_id: str,
        source: WorkflowSource,
        step_name: str | None = None,
        expect_failure: bool = False,
        include_load_errors: bool = False,
        task_id: str | None = None,
        authority: ExecutionAuthority | None = None,
        execution_target: WorkflowExecutionTarget | None = None,
    ) -> WorkflowExecutionResult:
        """Execute one workflow after waiting for its vault and global lanes."""
        vault_name, workflow_name = self._split_workflow_identity(global_id)
        if execution_target is not None and execution_target.global_id != global_id:
            raise ValueError("Workflow execution target does not match global_id")
        workflow_path = (
            execution_target.vault_relative_path
            if execution_target is not None
            else None
        )
        authority = _authority_for_workflow_source(source, authority=authority)
        source_value = str(source)
        active_task_id = task_id or ""
        task_context = (
            self._task_coordinator.track_existing_task(task_id)
            if task_id
            else self._task_coordinator.track_current_task(
                kind=ExecutionTaskKind.WORKFLOW,
                scope=workflow_vault_scope(vault_name),
                source=source,
                label=global_id,
                authority=authority,
                metadata={
                    "workflow_id": global_id,
                    "vault": vault_name,
                    "step_name": step_name,
                    "workflow_path": workflow_path,
                },
                start_immediately=False,
            )
        )
        async with task_context as task:
            active_task_id = task.task_id
            workflow_run = self._workflow_run_store.create_run(
                workflow_id=global_id,
                workflow_name=workflow_name,
                vault_name=vault_name,
                source=source_value,
                owner_principal_id=authority.principal_id,
                task_id=active_task_id,
                step_name=step_name,
            )
            try:
                await self._task_coordinator.update_metadata(
                    active_task_id,
                    {"workflow_run_id": workflow_run.run_id},
                )
            except Exception as exc:
                classification = classify_exception(exc, phase="workflow_setup")
                reason = f"{type(exc).__name__}: {exc}"
                self._workflow_run_store.finalize_run(
                    workflow_run.run_id,
                    status="failed",
                    reason=reason,
                    message=str(exc),
                    failure=_build_workflow_failure_metadata(
                        global_id=global_id,
                        vault_name=vault_name,
                        workflow_name=workflow_name,
                        workflow_path=workflow_path,
                        step_name=step_name,
                        source=source_value,
                        status="failed",
                        reason=reason,
                        classification=classification,
                        output_files=[],
                        message=str(exc),
                    ),
                )
                raise

            async def _log_vault_gate_queue(
                queued_task: ExecutionTaskSnapshot,
                _wait: ExecutionGateWait,
            ) -> None:
                self._log_queue_event(
                    "workflow_task_queued_for_vault",
                    global_id=global_id,
                    vault_name=vault_name,
                    workflow_name=workflow_name,
                    source=source_value,
                    task_id=queued_task.task_id,
                    workflow_path=workflow_path,
                    reason=f"workflow_vault_active:{vault_name}",
                )

            async def _run_in_vault_gate() -> WorkflowExecutionResult:
                global_semaphore: asyncio.Semaphore | None = None
                global_permit_acquired = False
                try:
                    global_semaphore = await self._get_global_semaphore()
                    if global_semaphore is not None:
                        if global_semaphore.locked():
                            await self._task_coordinator.heartbeat(
                                active_task_id,
                                status="queued_for_global_capacity",
                                metadata={
                                    "workflow_queue_reason": "workflow_global_capacity_active",
                                },
                            )
                            self._log_queue_event(
                                "workflow_task_queued_for_global_capacity",
                                global_id=global_id,
                                vault_name=vault_name,
                                workflow_name=workflow_name,
                                source=source_value,
                                task_id=active_task_id,
                                workflow_path=workflow_path,
                                reason="workflow_global_capacity_active",
                            )
                        await global_semaphore.acquire()
                        global_permit_acquired = True

                    await self._task_coordinator.mark_started(active_task_id)
                    self._workflow_run_store.mark_started(workflow_run.run_id)
                    await self._task_coordinator.heartbeat(
                        active_task_id,
                        status="workflow_running",
                        metadata={
                            "workflow_queue_reason": None,
                            "workflow_id": global_id,
                            "workflow_name": workflow_name,
                            "vault": vault_name,
                            "step_name": step_name,
                            "workflow_path": workflow_path,
                        },
                    )
                    self._log_workflow_event(
                        "workflow_task_started",
                        global_id=global_id,
                        vault_name=vault_name,
                        workflow_name=workflow_name,
                        source=source_value,
                        task_id=active_task_id,
                        workflow_path=workflow_path,
                        step_name=step_name,
                        expect_failure=expect_failure,
                        include_load_errors=include_load_errors,
                    )
                    timeout = get_workflow_task_timeout_seconds()

                    async def _record_workflow_timeout(
                        _task_id: str,
                        timeout_seconds: float,
                        reason: str,
                    ) -> WorkflowExecutionResult:
                        timeout_classification = FailureClassification(
                            error_type="TimeoutError",
                            failure_kind="workflow_timeout",
                            retryable=False,
                            phase="workflow_execution",
                            message=reason,
                            suggested_action=(
                                "Do not retry the same broad workflow unchanged. Narrow the workflow scope, "
                                "split the batch, or increase the workflow timeout if the scope is intentional."
                            ),
                        )
                        timeout_result = WorkflowExecutionResult(
                            success=False,
                            global_id=global_id,
                            status="timed_out",
                            execution_time_seconds=timeout_seconds,
                            output_files=[],
                            reason=reason,
                            details=[],
                            message=f"Workflow '{global_id}' timed out after {timeout_seconds:g} seconds",
                        )
                        await self._task_coordinator.update_metadata(
                            active_task_id,
                            {
                                "workflow_result": timeout_result.to_dict(),
                                "workflow_failure": _build_workflow_failure_metadata(
                                    global_id=global_id,
                                    vault_name=vault_name,
                                    workflow_name=workflow_name,
                                    workflow_path=workflow_path,
                                    step_name=step_name,
                                    source=source_value,
                                    status="timed_out",
                                    reason=reason,
                                    classification=timeout_classification,
                                    output_files=[],
                                    message=timeout_result.message,
                                ),
                            },
                        )
                        self._workflow_run_store.finalize_run(
                            workflow_run.run_id,
                            status="timed_out",
                            reason=reason,
                            message=timeout_result.message,
                            failure=_build_workflow_failure_metadata(
                                global_id=global_id,
                                vault_name=vault_name,
                                workflow_name=workflow_name,
                                workflow_path=workflow_path,
                                step_name=step_name,
                                source=source_value,
                                status="timed_out",
                                reason=reason,
                                classification=timeout_classification,
                                output_files=[],
                                message=timeout_result.message,
                            ),
                            output_files=[],
                            execution_time_seconds=timeout_seconds,
                        )
                        self._log_workflow_event(
                            "workflow_task_timed_out",
                            global_id=global_id,
                            vault_name=vault_name,
                            workflow_name=workflow_name,
                            source=source_value,
                            task_id=active_task_id,
                            workflow_path=workflow_path,
                            status="timed_out",
                            reason=reason,
                            step_name=step_name,
                            expect_failure=expect_failure,
                            include_load_errors=include_load_errors,
                            execution_time_seconds=timeout_seconds,
                            output_files=[],
                            message=timeout_result.message,
                            failure_kind=timeout_classification.failure_kind,
                            retryable=timeout_classification.retryable,
                        )
                        return timeout_result

                    async def _execute_selected_workflow() -> WorkflowExecutionResult:
                        if execution_target is not None:
                            return await execute_workflow_target(
                                execution_target,
                                step_name=step_name,
                                expect_failure=expect_failure,
                            )
                        return await execute_workflow_by_id(
                            global_id,
                            step_name=step_name,
                            expect_failure=expect_failure,
                            include_load_errors=include_load_errors,
                        )

                    result = await self._task_runner.run_with_timeout(
                        task,
                        ExecutionTaskSpec(
                            kind=ExecutionTaskKind.WORKFLOW,
                            scope=workflow_vault_scope(vault_name),
                            source=source,
                            label=global_id,
                            authority=authority,
                            metadata={},
                            timeout_seconds=timeout,
                            timeout_reason=f"workflow_task_timeout:{timeout:g}s",
                        ),
                        _execute_selected_workflow,
                        hooks=ExecutionTaskHooks(on_timed_out=_record_workflow_timeout),
                    )
                    if (
                        str(result.status or "").strip().lower()
                        == ExecutionTaskStatus.TIMED_OUT.value
                    ):
                        return cast(WorkflowExecutionResult, result)

                    result = replace(
                        result, details=[*result.details, f"task_id: {active_task_id}"]
                    )
                    await self._task_coordinator.update_metadata(
                        active_task_id,
                        {"workflow_result": result.to_dict()},
                    )
                    failure_metadata: dict[str, Any] | None = None
                    if (
                        str(result.status or "").strip().lower()
                        == ExecutionTaskStatus.FAILED.value
                    ):
                        failure_metadata = _build_workflow_failure_metadata(
                            global_id=global_id,
                            vault_name=vault_name,
                            workflow_name=workflow_name,
                            workflow_path=workflow_path,
                            step_name=step_name,
                            source=source_value,
                            status=result.status,
                            reason=result.reason,
                            classification=FailureClassification(
                                error_type="WorkflowFailed",
                                failure_kind="workflow_reported_failure",
                                retryable=False,
                                phase="workflow_execution",
                                message=result.reason or result.message,
                                suggested_action=(
                                    "Inspect the workflow result reason and any output artifacts, then resume "
                                    "only the unfinished items or adjust the workflow before rerunning."
                                ),
                            ),
                            output_files=result.output_files,
                            message=result.message,
                        )
                        await self._task_coordinator.update_metadata(
                            active_task_id,
                            {"workflow_failure": failure_metadata},
                        )
                    self._log_workflow_event(
                        _workflow_result_event(result),
                        global_id=global_id,
                        vault_name=vault_name,
                        workflow_name=workflow_name,
                        source=source_value,
                        task_id=active_task_id,
                        workflow_path=workflow_path,
                        status=result.status,
                        reason=result.reason,
                        step_name=step_name,
                        expect_failure=expect_failure,
                        include_load_errors=include_load_errors,
                        execution_time_seconds=result.execution_time_seconds,
                        output_files=result.output_files,
                        message=result.message,
                        failure_kind=(
                            failure_metadata.get("failure_kind")
                            if failure_metadata
                            else None
                        ),
                        retryable=(
                            failure_metadata.get("retryable")
                            if failure_metadata
                            else None
                        ),
                    )
                    self._workflow_run_store.finalize_run(
                        workflow_run.run_id,
                        status=_workflow_run_terminal_status(result),
                        reason=result.reason,
                        message=result.message,
                        failure=failure_metadata,
                        output_files=result.output_files,
                        execution_time_seconds=result.execution_time_seconds,
                    )
                    await self._mark_task_terminal_from_result(active_task_id, result)
                    return cast(WorkflowExecutionResult, result)
                except asyncio.CancelledError:
                    self._log_workflow_event(
                        "workflow_task_cancelled",
                        global_id=global_id,
                        vault_name=vault_name,
                        workflow_name=workflow_name,
                        source=source_value,
                        task_id=active_task_id,
                        workflow_path=workflow_path,
                        status="cancelled",
                        reason="cancelled",
                        step_name=step_name,
                        expect_failure=expect_failure,
                        include_load_errors=include_load_errors,
                    )
                    raise
                except Exception as exc:
                    reason = f"{type(exc).__name__}: {exc}"
                    classification = classify_exception(exc, phase="workflow_execution")
                    failure_metadata = _build_workflow_failure_metadata(
                        global_id=global_id,
                        vault_name=vault_name,
                        workflow_name=workflow_name,
                        workflow_path=workflow_path,
                        step_name=step_name,
                        source=source_value,
                        status="failed",
                        reason=reason,
                        classification=classification,
                        output_files=[],
                        message=str(exc),
                    )
                    await self._task_coordinator.update_metadata(
                        active_task_id,
                        {"workflow_failure": failure_metadata},
                    )
                    self._log_workflow_event(
                        "workflow_task_failed",
                        global_id=global_id,
                        vault_name=vault_name,
                        workflow_name=workflow_name,
                        source=source_value,
                        task_id=active_task_id,
                        workflow_path=workflow_path,
                        status="failed",
                        reason=reason,
                        step_name=step_name,
                        expect_failure=expect_failure,
                        include_load_errors=include_load_errors,
                        failure_kind=classification.failure_kind,
                        retryable=classification.retryable,
                    )
                    raise
                finally:
                    if global_permit_acquired and global_semaphore is not None:
                        global_semaphore.release()

            try:
                return cast(
                    WorkflowExecutionResult,
                    await self._task_runner.run_with_gate(
                        task,
                        ExecutionGatePolicy(
                            key=workflow_vault_scope(vault_name),
                            queued_status="queued_for_vault",
                            queued_metadata={
                                "workflow_queue_reason": f"workflow_vault_active:{vault_name}",
                            },
                            queue_position_key=None,
                            holder_task_id_key=None,
                            clear_metadata={"workflow_queue_reason": None},
                            on_queued=_log_vault_gate_queue,
                        ),
                        _run_in_vault_gate,
                    ),
                )
            except asyncio.CancelledError as exc:
                self._workflow_run_store.finalize_run(
                    workflow_run.run_id,
                    status="cancelled",
                    reason="cancelled",
                    message=f"Workflow '{global_id}' was cancelled",
                )
                _attach_workflow_run_id(exc, workflow_run.run_id)
                raise
            except Exception as exc:
                classification = classify_exception(exc, phase="workflow_execution")
                reason = f"{type(exc).__name__}: {exc}"
                self._workflow_run_store.finalize_run(
                    workflow_run.run_id,
                    status="failed",
                    reason=reason,
                    message=str(exc),
                    failure=_build_workflow_failure_metadata(
                        global_id=global_id,
                        vault_name=vault_name,
                        workflow_name=workflow_name,
                        workflow_path=workflow_path,
                        step_name=step_name,
                        source=source_value,
                        status="failed",
                        reason=reason,
                        classification=classification,
                        output_files=[],
                        message=str(exc),
                    ),
                )
                _attach_workflow_run_id(exc, workflow_run.run_id)
                raise

    async def start_workflow(
        self,
        *,
        global_id: str,
        source: WorkflowSource,
        step_name: str | None = None,
        expect_failure: bool = False,
        include_load_errors: bool = False,
        background_tasks: set[asyncio.Task] | None = None,
        authority: ExecutionAuthority | None = None,
        execution_target: WorkflowExecutionTarget | None = None,
    ) -> ExecutionTaskSnapshot:
        """Start one workflow in the background and return its execution task."""
        del background_tasks
        vault_name, _workflow_name = self._split_workflow_identity(global_id)
        authority = _authority_for_workflow_source(source, authority=authority)

        async def _run(task: ExecutionTaskSnapshot) -> None:
            try:
                await self.execute_workflow(
                    global_id=global_id,
                    source=source,
                    step_name=step_name,
                    expect_failure=expect_failure,
                    include_load_errors=include_load_errors,
                    task_id=task.task_id,
                    authority=authority,
                    execution_target=execution_target,
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                return

        return await self._task_runner.start_background(
            ExecutionTaskSpec(
                kind=ExecutionTaskKind.WORKFLOW,
                scope=workflow_vault_scope(vault_name),
                source=source,
                label=global_id,
                authority=authority,
                metadata={
                    "workflow_id": global_id,
                    "vault": vault_name,
                    "step_name": step_name,
                    "workflow_path": (
                        execution_target.vault_relative_path
                        if execution_target is not None
                        else None
                    ),
                },
            ),
            _run,
            start_immediately=False,
        )

    async def _get_global_semaphore(self) -> asyncio.Semaphore | None:
        limit = get_max_concurrent_workflows()
        if limit <= 0:
            return None
        async with self._global_limit_guard:
            if self._global_semaphore is None or self._global_limit != limit:
                self._global_limit = limit
                self._global_semaphore = asyncio.Semaphore(limit)
            return self._global_semaphore

    def _log_queue_event(
        self,
        event: str,
        *,
        global_id: str,
        vault_name: str,
        workflow_name: str,
        source: str,
        task_id: str,
        workflow_path: str | None,
        reason: str,
    ) -> None:
        self._logger.add_sink("validation").info(
            event,
            data={
                "event": event,
                "workflow_id": global_id,
                "workflow_name": workflow_name,
                "vault": vault_name,
                "source": source,
                "task_id": task_id,
                "workflow_path": workflow_path,
                "reason": reason,
            },
        )

    def _log_workflow_event(
        self,
        event: str,
        *,
        global_id: str,
        vault_name: str,
        workflow_name: str,
        source: str,
        task_id: str,
        workflow_path: str | None = None,
        status: str | None = None,
        reason: str | None = None,
        step_name: str | None = None,
        expect_failure: bool | None = None,
        include_load_errors: bool | None = None,
        execution_time_seconds: float | None = None,
        output_files: list[str] | None = None,
        message: str | None = None,
        failure_kind: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        self._logger.add_sink("validation").info(
            event,
            data={
                "event": event,
                "workflow_id": global_id,
                "workflow_name": workflow_name,
                "vault": vault_name,
                "source": source,
                "task_id": task_id,
                "workflow_path": workflow_path,
                "status": status,
                "reason": reason,
                "step_name": step_name,
                "expect_failure": expect_failure,
                "include_load_errors": include_load_errors,
                "execution_time_seconds": execution_time_seconds,
                "output_files": (
                    list(output_files) if output_files is not None else None
                ),
                "message": message,
                "failure_kind": failure_kind,
                "retryable": retryable,
            },
        )

    async def _mark_task_terminal_from_result(
        self,
        task_id: str,
        result: WorkflowExecutionResult,
    ) -> None:
        """Mirror a returned workflow status onto the execution task record."""
        status = str(result.status or "").strip().lower()
        reason = result.reason
        if status == ExecutionTaskStatus.SKIPPED.value:
            await self._task_coordinator.mark_skipped(task_id, reason=reason)
            return
        if status == ExecutionTaskStatus.FAILED.value:
            await self._task_coordinator.mark_failed(task_id, reason=reason)
            return
        if status == ExecutionTaskStatus.CANCELLED.value:
            await self._task_coordinator.mark_cancelled(task_id, reason=reason)
            return
        if status == ExecutionTaskStatus.TIMED_OUT.value:
            await self._task_coordinator.mark_timed_out(task_id, reason=reason)
            return
        await self._task_coordinator.mark_completed(task_id, reason=reason)

    @staticmethod
    def _split_workflow_identity(global_id: str) -> tuple[str, str]:
        if "/" not in global_id:
            raise ValueError(
                f"Invalid global_id format. Expected 'vault/name', got: {global_id}"
            )
        vault_name, workflow_name = global_id.split("/", 1)
        return vault_name, workflow_name


def _build_workflow_failure_metadata(
    *,
    global_id: str,
    vault_name: str,
    workflow_name: str,
    workflow_path: str | None,
    step_name: str | None,
    source: str,
    status: str,
    reason: str | None,
    classification: FailureClassification,
    output_files: list[str],
    message: str | None,
) -> dict[str, Any]:
    """Build stable task metadata that helps agents recover from workflow failures."""
    metadata = classification.to_metadata()
    metadata.update(
        {
            "workflow_id": global_id,
            "workflow_name": workflow_name,
            "workflow_path": workflow_path,
            "vault": vault_name,
            "source": source,
            "status": status,
            "phase": classification.phase,
            "reason": reason,
            "message": message or classification.message,
            "step_name": step_name,
            "output_files": list(output_files),
            "recovery_summary": {
                "completed_signal": "unknown",
                "remaining_signal": "unknown",
                "next_action": classification.suggested_action,
            },
        }
    )
    return metadata


def _workflow_result_event(result: WorkflowExecutionResult) -> str:
    status = str(result.status or "").strip().lower()
    return {
        ExecutionTaskStatus.FAILED.value: "workflow_task_failed",
        ExecutionTaskStatus.SKIPPED.value: "workflow_task_skipped",
        ExecutionTaskStatus.CANCELLED.value: "workflow_task_cancelled",
        ExecutionTaskStatus.TIMED_OUT.value: "workflow_task_timed_out",
    }.get(status, "workflow_task_completed")


def _workflow_run_terminal_status(result: WorkflowExecutionResult) -> str:
    """Normalize workflow results to the durable terminal status contract."""
    status = str(result.status or "").strip().lower()
    supported = {
        ExecutionTaskStatus.COMPLETED.value,
        ExecutionTaskStatus.FAILED.value,
        ExecutionTaskStatus.CANCELLED.value,
        ExecutionTaskStatus.TIMED_OUT.value,
        ExecutionTaskStatus.SKIPPED.value,
    }
    if status in supported:
        return status
    return (
        ExecutionTaskStatus.COMPLETED.value
        if result.success
        else ExecutionTaskStatus.FAILED.value
    )


def _attach_workflow_run_id(exc: BaseException, run_id: str) -> None:
    """Mark raised failures so the scheduler listener does not duplicate them."""
    try:
        cast(Any, exc).assistantmd_workflow_run_id = run_id
    except Exception:
        return
