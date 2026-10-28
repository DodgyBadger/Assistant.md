"""Real file-operation actions and concurrent worker coordination."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from validation.scenarios.application import Application

if TYPE_CHECKING:
    from core.vault_state.file_operations import VaultFileOperationResult


def record_result(
    app: Application, operation: str, result: VaultFileOperationResult
) -> VaultFileOperationResult:
    app.evidence.append(
        "file-results.jsonl",
        {
            "operation": operation,
            "metadata": result.metadata,
            "result": result.return_value,
        },
    )
    return result


def list_paths(
    app: Application, vault: Path, *, path: str, max_results: int
) -> VaultFileOperationResult:
    from core.vault_state.file_operations import list_vault_paths_operation

    return record_result(
        app,
        "list",
        list_vault_paths_operation(
            vault_path=str(vault), path=path, recursive=True, max_results=max_results
        ),
    )


def search(
    app: Application, vault: Path, *, path: str, search_term: str
) -> VaultFileOperationResult:
    from core.vault_state.file_operations import search_vault_files_operation

    return record_result(
        app,
        "search",
        search_vault_files_operation(
            vault_path=str(vault), path=path, search_term=search_term
        ),
    )


async def replace_concurrently(
    app: Application, vault: Path, *, path: str, replacements: list[tuple[str, str]]
) -> list[VaultFileOperationResult]:
    from core.vault_state.file_operations import replace_text_vault_file_operation

    barrier = threading.Barrier(len(replacements), timeout=5)

    def replace(old_text: str, new_text: str) -> VaultFileOperationResult:
        barrier.wait()
        result = replace_text_vault_file_operation(
            vault_path=vault, path=path, old_text=old_text, new_text=new_text, count=1
        )
        return record_result(app, "replace_text", result)

    return list(
        await asyncio.gather(
            *(asyncio.to_thread(replace, old, new) for old, new in replacements)
        )
    )


def move_directory(
    app: Application, vault: Path, *, path: str, destination: str
) -> None:
    from core.vault_state.file_mutations import move_vault_directory

    result = move_vault_directory(vault_path=vault, path=path, destination=destination)
    app.evidence.append(
        "directory-moves.jsonl",
        {"source": path, "destination": destination, "result": asdict(result)},
    )


def replace_stale_child(
    app: Application, vault: Path, *, path: str, content: str
) -> Exception | None:
    from core.vault_state.file_mutations import (
        VaultMutationRejected,
        replace_vault_file_content,
    )

    try:
        replace_vault_file_content(
            vault_path=vault, path=path, content=content, operation="replace_text"
        )
    except (FileNotFoundError, VaultMutationRejected) as exc:
        app.evidence.append(
            "stale-child-results.jsonl",
            {
                "path": path,
                "exception": type(exc).__name__,
                "code": getattr(exc, "code", None),
                "message": str(exc),
            },
        )
        return exc
    return None
