"""Durable import-to-markdown tool for URLs and vault files."""

from __future__ import annotations

import json
from typing import Any

from pydantic_ai.messages import ToolReturn
from pydantic_ai.tools import Tool

from core.identity import require_current_execution_authority
from core.ingestion.import_service import ContentImportResult, ContentImportService
from core.ingestion.models import JobStatus
from core.ingestion.task_execution import process_ingestion_job_now
from core.logger import UnifiedLogger
from core.runtime.execution_tasks import ExecutionTaskSource
from core.runtime.state import get_runtime_context
from core.tools.base import BaseTool, ToolRecoveryPolicy
from core.tools.failures import FailureClassification, tool_failure_return

logger = UnifiedLogger(tag="content-import-tool")


class ContentImport(BaseTool):
    """Submit and inspect durable ingestion jobs in the current vault."""

    @classmethod
    def get_recovery_policy(cls) -> ToolRecoveryPolicy:
        return ToolRecoveryPolicy.MANUAL_REQUIRED

    @classmethod
    def get_tool(cls, vault_path: str | None = None) -> Tool:
        async def content_import(
            *,
            operation: str,
            sources: str | list[str] | None = None,
            job_ids: int | list[int] | None = None,
            options: dict[str, Any] | None = None,
            queue_only: bool = False,
        ) -> ToolReturn:
            """Import URLs or vault files to Markdown, or inspect import jobs.

            :param operation: One of submit or status.
            :param sources: One source or a list of sources for submit.
            :param job_ids: One job id or a list of job ids for status.
            :param options: Optional destination and validated extraction options for submit.
            :param queue_only: Queue without waiting; use only for large multi-file submissions.
            """
            op = (operation or "").strip().lower()
            try:
                if not vault_path:
                    raise ValueError("vault_path is required for content_import")
                service = ContentImportService(vault_path)
                if op == "submit":
                    if sources is None or job_ids is not None:
                        raise ValueError(
                            "submit requires sources and does not accept job_ids"
                        )
                    runtime = get_runtime_context() if not queue_only else None
                    authority = (
                        require_current_execution_authority()
                        if not queue_only
                        else None
                    )
                    results = service.submit(sources=sources, options=options)
                    if runtime is not None and authority is not None:
                        for result in results:
                            await process_ingestion_job_now(
                                ingestion=runtime.ingestion,
                                task_coordinator=runtime.task_coordinator,
                                job_id=result.job_id,
                                source=ExecutionTaskSource.TOOL,
                                authority=authority,
                            )
                        results = service.status(
                            job_ids=[result.job_id for result in results]
                        )
                    logger.set_sinks(["validation"]).info(
                        "content_import_submitted",
                        data={
                            "event": "content_import_submitted",
                            "vault_name": service.vault_name,
                            "accepted_count": len(results),
                            "url_count": sum(
                                item.source_kind == "url" for item in results
                            ),
                            "vault_file_count": sum(
                                item.source_kind == "vault_file" for item in results
                            ),
                            "job_ids": [item.job_id for item in results],
                            "queue_only": queue_only,
                            "terminal_count": sum(
                                item.status
                                in {
                                    JobStatus.COMPLETED.value,
                                    JobStatus.FAILED.value,
                                    JobStatus.CANCELLED.value,
                                }
                                for item in results
                            ),
                            "failed_count": sum(
                                item.status == JobStatus.FAILED.value
                                for item in results
                            ),
                        },
                    )
                    return _success_result(op, results)
                if op == "status":
                    if (
                        job_ids is None
                        or sources is not None
                        or options is not None
                        or queue_only
                    ):
                        raise ValueError(
                            "status requires job_ids and does not accept sources, "
                            "options, or queue_only=true"
                        )
                    results = service.status(job_ids=job_ids)
                    logger.set_sinks(["validation"]).info(
                        "content_import_status_read",
                        data={
                            "event": "content_import_status_read",
                            "vault_name": service.vault_name,
                            "requested_count": len(results),
                            "returned_count": len(results),
                        },
                    )
                    return _success_result(op, results)
                raise ValueError("operation must be submit or status")
            except Exception as exc:  # noqa: BLE001 - tool boundary
                return tool_failure_return(
                    tool_name="content_import",
                    message="Content import request failed",
                    classification=FailureClassification(
                        error_type=type(exc).__name__,
                        failure_kind="bad_request",
                        retryable=False,
                        phase="content_import",
                        message=str(exc),
                        suggested_action="Correct the import request before retrying.",
                    ),
                    metadata={"operation": op},
                )

        return Tool(
            content_import,
            name="content_import",
            description=(
                "Import URLs or vault files to durable Markdown and wait for results "
                "by default. Use queue_only=true for large multi-file submissions, "
                "then inspect those jobs with status."
            ),
        )


def _success_result(
    operation: str,
    results: list[ContentImportResult],
) -> ToolReturn:
    items = [item.to_dict() for item in results]
    payload = {
        "status": "ok",
        "operation": operation,
        "items": items,
    }
    return ToolReturn(
        return_value=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        metadata={
            "tool_name": "content_import",
            "status": "success",
            "operation": operation,
            "items": items,
        },
    )
