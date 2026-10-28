"""Legacy ownership input and real task callbacks for authority journeys."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any


def create_legacy_chat_database(system_root: Path) -> None:
    system_root.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(system_root / "chat_sessions.db") as connection:
        connection.execute("""
            CREATE TABLE chat_sessions (
                session_id TEXT NOT NULL,
                vault_name TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_activity_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                title TEXT,
                metadata_json TEXT,
                PRIMARY KEY (session_id, vault_name)
            )
        """)
        connection.execute(
            "INSERT INTO chat_sessions (session_id, vault_name) VALUES ('legacy-session', 'PrincipalVault')"
        )


def current_principal_id() -> str | None:
    from core.identity import get_current_execution_authority

    authority = get_current_execution_authority()
    return authority.principal_id if authority else None


async def capture_authority(observed: list[str | None]) -> None:
    observed.append(current_principal_id())


async def wait_terminal(task_id: str) -> Any:
    from core.runtime.state import get_runtime_context

    for _ in range(100):
        task = await get_runtime_context().task_coordinator.get_task(task_id)
        if task is not None and task.is_terminal:
            return task
        await asyncio.sleep(0.02)
    raise TimeoutError(f"Execution task did not finish: {task_id}")


AUTHORITY_WORKFLOW = """---
run_type: workflow
enabled: false
description: Authority propagation input
---
```python
await finish(status="completed", reason="authority-observed")
```
"""
