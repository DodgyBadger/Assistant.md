"""Mutating vault file operations tool."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn
from pydantic_ai.tools import Tool

from core.logger import UnifiedLogger
from core.vault_state.file_mutations import VaultMutationRejected
from core.vault_state.file_operations import (
    VaultFileOperationRejected,
    VaultFileOperationResult,
    append_vault_file_operation,
    copy_vault_file_operation,
    delete_vault_path_operation,
    edit_vault_line_operation,
    make_vault_directory_operation,
    move_vault_path_operation,
    replace_text_vault_file_operation,
    write_vault_file_operation,
)
from core.vault_state.path_deletion import VaultDeletionFailed

from .base import BaseTool, ToolRecoveryPolicy

logger = UnifiedLogger(tag="file-write-tool")


class FileWrite(BaseTool):
    """Create, edit, copy, move, and delete vault files."""

    @classmethod
    def get_recovery_policy(cls) -> ToolRecoveryPolicy:
        return ToolRecoveryPolicy.VAULT_TRANSACTIONAL

    @classmethod
    def get_tool(cls, vault_path: str | None = None) -> Tool:
        """Return the Pydantic AI tool for mutating vault operations."""

        def file_write(
            ctx: RunContext,
            *,
            operation: str,
            path: str = "",
            paths: list[str] | None = None,
            content: str = "",
            destination: str = "",
            old_text: str = "",
            new_text: str = "",
            line_number: int = 0,
            count: int = 1,
            overwrite: bool = False,
        ) -> ToolReturn:
            """Create, edit, copy, move, or delete vault files.

            :param operation: One of write, append, edit_line, replace_text, copy, move, delete, mkdir. Write is create-only unless overwrite is true.
            :param path: Vault-relative file or directory path.
            :param paths: Nonempty list of targets for delete, mutually exclusive with path. Folder deletion includes all contents. Duplicate and overlapping targets are collapsed.
            :param content: Full content for write, or content to append.
            :param destination: Exact destination filename for copy or move. Copy supports files only.
            :param old_text: Exact text to replace for replace_text.
            :param new_text: Replacement text for replace_text.
            :param line_number: One-indexed target line for edit_line.
            :param count: Replacement count for replace_text.
            :param overwrite: For write, set true when updating or rewriting a known existing file. For copy or move, set true to replace an existing destination. False keeps both operations create-only/non-destructive.
            """
            try:
                logger.set_sinks(["validation"]).info(
                    "tool_invoked",
                    data={
                        "tool": "file_write",
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
                result = _execute_write_operation(
                    vault_path=vault_path,
                    operation=operation,
                    path=path,
                    paths=paths,
                    content=content,
                    destination=destination,
                    old_text=old_text,
                    new_text=new_text,
                    line_number=line_number,
                    count=count,
                    overwrite=overwrite,
                    tool_call_id=ctx.tool_call_id,
                )
                return _to_tool_return(result)
            except VaultDeletionFailed as exc:
                return _error_result(
                    str(exc),
                    operation=operation,
                    path=path,
                    error_type=exc.code,
                    metadata={
                        "activity_id": exc.activity_id,
                        "recovery_status": exc.recovery_status,
                    },
                )
            except VaultMutationRejected as exc:
                return _error_result(
                    str(exc), operation=operation, path=path, error_type=exc.code
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
                    destination=destination,
                    error_type=type(exc).__name__,
                )

        return Tool(
            file_write,
            name="file_write",
            description=(
                "Create and edit plain UTF-8 text, copy and move any file, delete, and create "
                "folders within the current vault. The write operation is create-only by "
                "default; when the user asks to update or rewrite a known existing file, "
                "call write with overwrite=true. Use exact filenames; no extension is inferred."
            ),
        )


def _execute_write_operation(
    *,
    vault_path: str,
    operation: str,
    path: str,
    paths: list[str] | None,
    content: str,
    destination: str,
    old_text: str,
    new_text: str,
    line_number: int,
    count: int,
    overwrite: bool,
    tool_call_id: str | None,
) -> VaultFileOperationResult:
    normalized = operation.strip().lower()
    if paths is not None and normalized != "delete":
        raise VaultMutationRejected(
            "invalid_parameters",
            "paths is only supported for delete. Use path for other operations.",
        )
    if normalized == "write":
        return write_vault_file_operation(
            vault_path=vault_path,
            path=path,
            content=content,
            overwrite=overwrite,
        )
    if normalized == "append":
        return append_vault_file_operation(
            vault_path=vault_path, path=path, content=content
        )
    if normalized == "edit_line":
        return edit_vault_line_operation(
            vault_path=vault_path,
            path=path,
            line_number=line_number,
            old_text=old_text,
            new_text=new_text,
        )
    if normalized == "replace_text":
        return replace_text_vault_file_operation(
            vault_path=vault_path,
            path=path,
            old_text=old_text,
            new_text=new_text,
            count=count,
        )
    if normalized == "copy":
        from core.chat.deferred_reviews import reviewed_copy_states

        return copy_vault_file_operation(
            vault_path=vault_path,
            path=path,
            destination=destination,
            overwrite=overwrite,
            reviewed_states=reviewed_copy_states(tool_call_id),
        )
    if normalized == "move":
        return move_vault_path_operation(
            vault_path=vault_path,
            path=path,
            destination=destination,
            overwrite=overwrite,
        )
    if normalized == "delete":
        from core.chat.deferred_reviews import reviewed_deletion_fingerprint

        return delete_vault_path_operation(
            vault_path=vault_path,
            path=path,
            paths=paths,
            expected_fingerprint=reviewed_deletion_fingerprint(tool_call_id),
        )
    if normalized == "mkdir":
        return make_vault_directory_operation(vault_path=vault_path, path=path)
    return VaultFileOperationResult(
        return_value=(
            f"Unknown operation '{operation}'. Available: write, append, edit_line, "
            "replace_text, copy, move, delete, mkdir"
        ),
        metadata={
            "status": "error",
            "operation": operation,
            "path": path,
            "destination": destination,
            "error_type": "unknown_operation",
        },
    )


def _to_tool_return(result: VaultFileOperationResult) -> ToolReturn:
    metadata = {"tool_name": "file_write", **result.metadata}
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
    destination: str = "",
    error_type: str,
    metadata: dict[str, Any] | None = None,
) -> ToolReturn:
    payload = {
        "tool_name": "file_write",
        "status": "error",
        "operation": operation,
        "error_type": error_type,
    }
    if path:
        payload["path"] = path
    if destination:
        payload["destination"] = destination
    if metadata:
        payload.update(metadata)
    return ToolReturn(return_value=message, metadata=payload)
