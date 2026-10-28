"""Caller contexts, retention fixtures and public task inputs for goal/job journeys."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any


def tool_context(vault_name: str, **deps: Any) -> SimpleNamespace:
    return SimpleNamespace(deps=SimpleNamespace(vault_name=vault_name, **deps))


def tool_result(raw: Any) -> Any:
    payload = json.loads(getattr(raw, "return_value", raw))
    assert payload["status"] == "ok"
    return payload["result"]


def age_goals(app: Any, goal_ids: list[str]) -> None:
    timestamp = (
        datetime.now(UTC).replace(microsecond=0) - timedelta(days=45)
    ).isoformat()
    with sqlite3.connect(app.root / "system/goal_ops.db") as conn:
        conn.executemany(
            "UPDATE goals SET updated_at = ? WHERE goal_id = ?",
            [(timestamp, goal_id) for goal_id in goal_ids],
        )


def goal_rows(app: Any, goal_id: str) -> tuple[int, int, int]:
    with sqlite3.connect(app.root / "system/goal_ops.db") as conn:
        return tuple(
            conn.execute(
                f"SELECT count(*) FROM {table} WHERE goal_id = ?", (goal_id,)
            ).fetchone()[0]
            for table in ("goals", "goal_events", "goal_checkpoints")
        )
