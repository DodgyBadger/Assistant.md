"""Real vault-state actions, durable row observations, and event synchronization."""

from __future__ import annotations

import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from validation.pytest_scenarios.application import Application

if TYPE_CHECKING:
    from core.vault_state import VaultStateService
    from core.vault_state.file_operations import VaultFileOperationResult
    from core.vault_state.models import VaultFileEvent
    from core.vault_state.service import VaultStateRefreshResult


def concurrent_identities(app: Application, vault: Path) -> list[str]:
    from core.vault_state.identity import resolve_or_create_vault_identity

    vault.mkdir()

    def resolve(_: int) -> str:
        return resolve_or_create_vault_identity(vault).vault_id

    with ThreadPoolExecutor(max_workers=8) as executor:
        identities = list(executor.map(resolve, range(16)))
    app.evidence.save_json("concurrent-identities.json", identities)
    return identities


def refresh_vault(
    app: Application, service: VaultStateService, vault: Path
) -> VaultStateRefreshResult:
    result = service.refresh_vault(vault)
    app.evidence.append("vault-refreshes.jsonl", asdict(result))
    return result


def changes_since(
    app: Application, service: VaultStateService, vault_id: str
) -> list[VaultFileEvent]:
    changes = service.changes_since(0, vault_id=vault_id)
    app.evidence.save_json(
        "change-feed.json",
        [
            {
                "sequence": event.sequence,
                "vault_id": event.vault_id,
                "vault_name": event.vault_name,
                "path": event.path,
                "event_type": event.event_type,
            }
            for event in changes
        ],
    )
    return changes


def manifest_rows(app: Application) -> list[dict[str, Any]]:
    connection = sqlite3.connect(app.root / "system" / "vault_state.db")
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("""
            SELECT vault_id, vault_name, path, artifact_class, content_hash,
                   change_sequence, deleted_at
            FROM vault_files
            ORDER BY path
            """).fetchall()
        result = [dict(row) for row in rows]
    finally:
        connection.close()
    app.evidence.append("manifest-snapshots.jsonl", result)
    return result


def manifest_row_exists(
    app: Application, vault_id: str, path: str, *, deleted: bool
) -> bool:
    rows = manifest_rows(app)
    return any(
        row["vault_id"] == vault_id
        and row["path"] == path
        and (row["deleted_at"] is not None) is deleted
        for row in rows
    )


async def wait_for_event(
    app: Application,
    checkpoint: int,
    *,
    name: str,
    expected: dict[str, Any],
    timeout_seconds: float = 5.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while not app.events.contains(name, fields=expected, after=checkpoint):
        if asyncio.get_running_loop().time() >= deadline:
            break
        await asyncio.sleep(0.05)
    app.events.require(name, fields=expected, after=checkpoint)


async def restart_application(app: Application) -> None:
    await app.controller.restart_system()
    app.evidence.step("application_restarted")


def search_content(
    app: Application, vault: Path, *, path: str, search_term: str
) -> VaultFileOperationResult:
    from core.vault_state.file_operations import search_vault_files_operation

    result = search_vault_files_operation(
        vault_path=vault, path=path, search_term=search_term
    )
    app.evidence.append(
        "search-tool-results.jsonl",
        {
            "path": path,
            "search_term": search_term,
            "metadata": result.metadata,
            "result": result.return_value,
        },
    )
    return result
