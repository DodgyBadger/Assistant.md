"""Read-only vault file operations tool."""

from __future__ import annotations

from typing import Any

from pydantic_ai.messages import ToolReturn
from pydantic_ai.tools import Tool

from core.logger import UnifiedLogger
from core.vault_state.file_operations import (
    VaultFileOperationRejected,
    VaultFileOperationResult,
    frontmatter_vault_files_operation,
    list_vault_paths_operation,
    read_vault_file_operation,
    search_vault_files_operation,
)

from .base import BaseTool, ToolRecoveryPolicy

logger = UnifiedLogger(tag="file-read-tool")


class FileRead(BaseTool):
    """Read, list, and search vault files."""

    @classmethod
    def get_recovery_policy(cls) -> ToolRecoveryPolicy:
        return ToolRecoveryPolicy.REPLAY_SAFE

    @classmethod
    def get_tool(cls, vault_path: str | None = None) -> Tool:
        """Return the Pydantic AI tool for read-only vault operations."""

        def file_read(
            *,
            operation: str,
            path: str = "",
            recursive: bool = False,
            search_term: str = "",
            keys: str = "",
            start_line: int = 0,
            line_count: int = 0,
        ) -> ToolReturn:
            """Read plain UTF-8 text or images, list or search files, and inspect Markdown frontmatter.

            :param operation: One of read, list, search, frontmatter.
            :param path: Exact vault-relative filename or directory path; no extension is inferred.
            :param recursive: Recurse through subdirectories for list.
            :param search_term: Text to search for.
            :param keys: Optional comma-separated frontmatter keys to return.
            :param start_line: Optional 1-indexed first line for read.
            :param line_count: Optional number of lines for read.
            """
            try:
                logger.set_sinks(["validation"]).info(
                    "tool_invoked",
                    data={
                        "tool": "file_read",
                        "operation": operation,
                        "vault": (
                            vault_path.rstrip("/").split("/")[-1]
                            if vault_path
                            else None
                        ),
                    },
                )
                if not vault_path:
                    raise ValueError("vault_path is required for file operations")
                normalized = operation.strip().lower()
                if normalized == "read":
                    return _to_tool_return(
                        read_vault_file_operation(
                            vault_path=vault_path,
                            path=path,
                            start_line=start_line,
                            line_count=line_count,
                        )
                    )
                if normalized == "list":
                    return _to_tool_return(
                        list_vault_paths_operation(
                            vault_path=vault_path,
                            path=path,
                            recursive=recursive,
                        )
                    )
                if normalized == "search":
                    return _to_tool_return(
                        search_vault_files_operation(
                            vault_path=vault_path,
                            path=path,
                            search_term=search_term,
                        )
                    )
                if normalized == "frontmatter":
                    return _to_tool_return(
                        frontmatter_vault_files_operation(
                            vault_path=vault_path,
                            path=path,
                            keys=keys,
                        )
                    )
                return _error_result(
                    f"Unknown operation '{operation}'. Available: read, list, search, frontmatter",
                    operation=operation,
                    path=path,
                    error_type="unknown_operation",
                )
            except VaultFileOperationRejected as exc:
                return _error_result(
                    str(exc),
                    operation=operation,
                    path=str(exc.details.get("path") or path),
                    error_type=exc.code,
                    metadata=exc.details,
                )
            except Exception as exc:
                return _error_result(
                    f"Error performing '{operation}' operation: {exc}",
                    operation=operation,
                    path=path,
                    error_type=type(exc).__name__,
                )

        return Tool(
            file_read,
            name="file_read",
            description="Read plain UTF-8 text and images, list, search, and inspect frontmatter within the current vault.",
        )


def _to_tool_return(result: VaultFileOperationResult) -> ToolReturn:
    metadata = {"tool_name": "file_read", **result.metadata}
    return ToolReturn(
        return_value=result.return_value,
        content=result.content,
        metadata=metadata,
    )


def _error_result(
    message: str,
    *,
    operation: str,
    path: str = "",
    error_type: str,
    metadata: dict[str, Any] | None = None,
) -> ToolReturn:
    payload = {
        "tool_name": "file_read",
        "status": "error",
        "operation": operation,
        "error_type": error_type,
    }
    if path:
        payload["path"] = path
    if metadata:
        payload.update(metadata)
    return ToolReturn(return_value=message, metadata=payload)
