"""Cache semantics and artifact store for authoring context templates."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.database import connect_sqlite_from_system_db, get_system_database_path
from core.logger import UnifiedLogger

if TYPE_CHECKING:
    from core.authoring.template_discovery import TemplateRecord

logger = UnifiedLogger(tag="context-store")

DB_NAME = "cache"

# ---------------------------------------------------------------------------
# Cache semantics
# ---------------------------------------------------------------------------

_DURATION_PATTERN = re.compile(r"^(?P<amount>\d+)\s*(?P<unit>[smhd])$")
_UNIT_SECONDS = {
    "s": 1,
    "m": 60,
    "h": 60 * 60,
    "d": 60 * 60 * 24,
}
_NAMED_MODES = {"daily", "weekly", "session"}


def parse_cache_mode_value(value: str) -> dict[str, Any]:
    normalized = value.strip().lower()
    if not normalized:
        raise ValueError("Cache mode cannot be empty")
    if normalized in _NAMED_MODES:
        return {"mode": normalized, "ttl_seconds": None}

    match = _DURATION_PATTERN.match(normalized)
    if not match:
        raise ValueError(
            "Expected cache ttl like 10m/24h/1d or one of: daily, weekly, session"
        )

    amount = int(match.group("amount"))
    if amount <= 0:
        raise ValueError("Cache duration must be greater than 0")

    ttl_seconds = amount * _UNIT_SECONDS[match.group("unit")]
    return {"mode": "duration", "ttl_seconds": ttl_seconds}


def parse_db_timestamp(raw_value: str | None) -> datetime | None:
    if not raw_value:
        return None
    try:
        return datetime.fromisoformat(raw_value)
    except ValueError:
        try:
            return datetime.strptime(raw_value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


def start_of_week(value: datetime, week_start_day: int) -> datetime:
    delta_days = (value.weekday() - week_start_day) % 7
    return (value - timedelta(days=delta_days)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )


def cache_entry_is_valid(
    *,
    created_at: str | None,
    cache_mode: str,
    ttl_seconds: int | None,
    now: datetime,
    week_start_day: int,
) -> bool:
    created_dt = parse_db_timestamp(created_at)
    if created_dt is None:
        return False
    if created_dt.tzinfo is None and now.tzinfo is not None:
        created_dt = created_dt.replace(tzinfo=UTC)
    elif created_dt.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    if cache_mode == "duration":
        if ttl_seconds is None:
            return False
        return now - created_dt < timedelta(seconds=ttl_seconds)
    if cache_mode == "daily":
        return created_dt.date() == now.date()
    if cache_mode == "weekly":
        return start_of_week(created_dt, week_start_day) == start_of_week(
            now, week_start_day
        )
    if cache_mode == "session":
        return True
    return False


def compute_cache_expiration(
    *,
    created_at: datetime,
    cache_mode: str,
    ttl_seconds: int | None,
    week_start_day: int,
) -> datetime | None:
    if cache_mode == "session":
        return None
    if cache_mode == "duration":
        if ttl_seconds is None:
            raise ValueError("Duration cache entries require ttl_seconds")
        return created_at + timedelta(seconds=ttl_seconds)
    if cache_mode == "daily":
        return (created_at + timedelta(days=1)).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
    if cache_mode == "weekly":
        return start_of_week(created_at, week_start_day) + timedelta(days=7)
    raise ValueError(f"Unsupported cache mode '{cache_mode}'")


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _get_db_path(system_root: Path | None = None) -> str:
    return get_system_database_path(DB_NAME, str(system_root) if system_root else None)


def _ensure_db(system_root: Path | None = None) -> str:
    """Create database file and tables if missing."""
    db_path = _get_db_path(system_root)
    conn = connect_sqlite_from_system_db(
        DB_NAME, str(system_root) if system_root else None
    )
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS context_step_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                vault_name TEXT NOT NULL,
                template_name TEXT NOT NULL,
                template_hash TEXT NOT NULL,
                section_key TEXT NOT NULL,
                cache_mode TEXT NOT NULL,
                ttl_seconds INTEGER,
                raw_output TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_context_step_cache_lookup
            ON context_step_cache(vault_name, template_name, section_key)
            """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_context_step_cache_run
            ON context_step_cache(run_id)
            """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_context_step_cache_session
            ON context_step_cache(session_id)
            """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                vault_name TEXT NOT NULL,
                metadata TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(session_id, vault_name)
            )
            """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS context_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                vault_name TEXT NOT NULL,
                turn_index INTEGER,
                template_name TEXT,
                template_source TEXT,
                template_hash TEXT,
                model_alias TEXT,
                raw_output TEXT,
                budget_used INTEGER,
                sections_included TEXT,
                compiled_prompt TEXT,
                input_payload TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_context_summaries_session ON context_summaries(session_id, vault_name)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cache_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id TEXT NOT NULL,
                session_key TEXT,
                artifact_ref TEXT NOT NULL,
                cache_mode TEXT NOT NULL,
                ttl_seconds INTEGER,
                raw_content TEXT NOT NULL,
                metadata TEXT,
                origin TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_accessed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                expires_at DATETIME
            )
            """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_cache_artifacts_owner_ref
            ON cache_artifacts(owner_id, artifact_ref)
            """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_cache_artifacts_expires
            ON cache_artifacts(expires_at)
            """)
        conn.commit()
    finally:
        conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Cache artifact store
# ---------------------------------------------------------------------------


def purge_expired_cache_artifacts(
    *,
    now: datetime,
    system_root: Path | None = None,
) -> int:
    """Delete cache artifacts whose expiration timestamp has passed."""
    _ensure_db(system_root)
    conn = connect_sqlite_from_system_db(
        DB_NAME, str(system_root) if system_root else None
    )
    try:
        cursor = conn.execute(
            """
            DELETE FROM cache_artifacts
            WHERE expires_at IS NOT NULL
              AND expires_at <= ?
            """,
            (now.isoformat(sep=" "),),
        )
        conn.commit()
        return int(cursor.rowcount or 0)
    finally:
        conn.close()


def upsert_cache_artifact(
    *,
    owner_id: str,
    session_key: str | None,
    artifact_ref: str,
    cache_mode: str,
    ttl_seconds: int | None,
    raw_content: str,
    metadata: dict[str, Any] | None = None,
    origin: str | None = None,
    now: datetime,
    week_start_day: int,
    system_root: Path | None = None,
) -> None:
    """Insert or replace one named cache artifact for an owner/ref pair."""
    _ensure_db(system_root)
    expires_at = compute_cache_expiration(
        created_at=now,
        cache_mode=cache_mode,
        ttl_seconds=ttl_seconds,
        week_start_day=week_start_day,
    )
    metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
    conn = connect_sqlite_from_system_db(
        DB_NAME, str(system_root) if system_root else None
    )
    try:
        conn.execute(
            "DELETE FROM cache_artifacts WHERE owner_id = ? AND artifact_ref = ?",
            (owner_id, artifact_ref),
        )
        conn.execute(
            """
            INSERT INTO cache_artifacts (
                owner_id, session_key, artifact_ref, cache_mode, ttl_seconds,
                raw_content, metadata, origin, created_at, last_accessed_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                owner_id,
                session_key,
                artifact_ref,
                cache_mode,
                ttl_seconds,
                raw_content,
                metadata_json,
                origin,
                now.isoformat(sep=" "),
                now.isoformat(sep=" "),
                None if expires_at is None else expires_at.isoformat(sep=" "),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_cache_artifact(
    *,
    owner_id: str,
    session_key: str | None,
    artifact_ref: str,
    now: datetime,
    week_start_day: int,
    system_root: Path | None = None,
) -> dict[str, Any] | None:
    """Fetch one cache artifact if it exists and is still valid."""
    _ensure_db(system_root)
    conn = connect_sqlite_from_system_db(
        DB_NAME, str(system_root) if system_root else None
    )
    try:
        row = conn.execute(
            """
            SELECT id, owner_id, session_key, artifact_ref, cache_mode, ttl_seconds,
                   raw_content, metadata, origin, created_at, last_accessed_at, expires_at
            FROM cache_artifacts
            WHERE owner_id = ? AND artifact_ref = ?
            LIMIT 1
            """,
            (owner_id, artifact_ref),
        ).fetchone()
        if row is None:
            return None

        cache_mode = row[4]
        ttl_seconds = row[5]
        created_at = row[9]
        if cache_mode == "session" and row[2] != session_key:
            return None
        if not cache_entry_is_valid(
            created_at=created_at,
            cache_mode=cache_mode,
            ttl_seconds=ttl_seconds,
            now=now,
            week_start_day=week_start_day,
        ):
            conn.execute("DELETE FROM cache_artifacts WHERE id = ?", (row[0],))
            conn.commit()
            return None

        conn.execute(
            "UPDATE cache_artifacts SET last_accessed_at = ? WHERE id = ?",
            (now.isoformat(sep=" "), row[0]),
        )
        conn.commit()
    finally:
        conn.close()

    metadata: dict[str, Any]
    try:
        metadata = json.loads(row[7]) if row[7] else {}
    except json.JSONDecodeError:
        metadata = {}

    return {
        "owner_id": row[1],
        "session_key": row[2],
        "artifact_ref": row[3],
        "cache_mode": cache_mode,
        "ttl_seconds": ttl_seconds,
        "raw_content": row[6],
        "metadata": metadata,
        "origin": row[8],
        "created_at": created_at,
        "last_accessed_at": row[10],
        "expires_at": row[11],
    }


def upsert_cached_step_output(
    *,
    run_id: str,
    session_id: str,
    vault_name: str,
    template_name: str,
    template_hash: str,
    section_key: str,
    cache_mode: str,
    ttl_seconds: int | None,
    raw_output: str,
    system_root: Path | None = None,
) -> None:
    """Insert or update a cached step output."""
    _ensure_db(system_root)
    conn = connect_sqlite_from_system_db(
        DB_NAME, str(system_root) if system_root else None
    )
    try:
        if cache_mode == "session":
            conn.execute(
                """
                DELETE FROM context_step_cache
                WHERE vault_name = ? AND template_name = ? AND section_key = ?
                  AND cache_mode = ? AND session_id = ?
                """,
                (vault_name, template_name, section_key, cache_mode, session_id),
            )
        else:
            conn.execute(
                """
                DELETE FROM context_step_cache
                WHERE vault_name = ? AND template_name = ? AND section_key = ?
                  AND cache_mode = ?
                """,
                (vault_name, template_name, section_key, cache_mode),
            )
        conn.execute(
            """
            INSERT INTO context_step_cache (
                run_id, session_id, vault_name, template_name, template_hash,
                section_key, cache_mode, ttl_seconds, raw_output
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                session_id,
                vault_name,
                template_name,
                template_hash,
                section_key,
                cache_mode,
                ttl_seconds,
                raw_output,
            ),
        )
        conn.commit()
    except Exception as exc:  # pragma: no cover - defensive
        logger.error(f"Failed to upsert cached step output for {section_key}: {exc}")
        raise
    finally:
        conn.close()


def get_cached_step_output(
    *,
    session_id: str,
    vault_name: str,
    template_name: str,
    section_key: str,
    cache_mode: str,
    system_root: Path | None = None,
) -> dict[str, Any] | None:
    """Fetch the most recent cached step output for a key."""
    _ensure_db(system_root)
    conn = connect_sqlite_from_system_db(
        DB_NAME, str(system_root) if system_root else None
    )
    try:
        if cache_mode == "session":
            rows = conn.execute(
                """
                SELECT run_id, session_id, vault_name, template_name, template_hash,
                       section_key, cache_mode, ttl_seconds, raw_output, created_at
                FROM context_step_cache
                WHERE vault_name = ? AND template_name = ? AND section_key = ?
                  AND cache_mode = ? AND session_id = ?
                ORDER BY id DESC LIMIT 1
                """,
                (vault_name, template_name, section_key, cache_mode, session_id),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT run_id, session_id, vault_name, template_name, template_hash,
                       section_key, cache_mode, ttl_seconds, raw_output, created_at
                FROM context_step_cache
                WHERE vault_name = ? AND template_name = ? AND section_key = ?
                  AND cache_mode = ?
                ORDER BY id DESC LIMIT 1
                """,
                (vault_name, template_name, section_key, cache_mode),
            ).fetchall()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"Failed to fetch cached step output for {section_key}: {exc}")
        return None
    finally:
        conn.close()

    if not rows:
        return None
    row = rows[0]
    return {
        "run_id": row[0],
        "session_id": row[1],
        "vault_name": row[2],
        "template_name": row[3],
        "template_hash": row[4],
        "section_key": row[5],
        "cache_mode": row[6],
        "ttl_seconds": row[7],
        "raw_output": row[8],
        "created_at": row[9],
    }


def upsert_session(
    session_id: str,
    vault_name: str,
    metadata: dict[str, Any] | None = None,
    system_root: Path | None = None,
) -> None:
    """Insert session row if missing; ignore duplicates."""
    _ensure_db(system_root)
    meta_json = json.dumps(metadata or {}, ensure_ascii=False)
    conn = connect_sqlite_from_system_db(
        DB_NAME, str(system_root) if system_root else None
    )
    try:
        conn.execute(
            "INSERT OR IGNORE INTO sessions (session_id, vault_name, metadata) VALUES (?, ?, ?)",
            (session_id, vault_name, meta_json),
        )
        conn.commit()
    except Exception as exc:  # pragma: no cover - defensive
        logger.error(f"Failed to upsert session {session_id}: {exc}")
        raise
    finally:
        conn.close()


def add_context_summary(
    session_id: str,
    vault_name: str,
    turn_index: int | None,
    template: TemplateRecord,
    model_alias: str,
    raw_output: str | None,
    budget_used: int | None = None,
    sections_included: dict[str, Any] | None = None,
    compiled_prompt: str | None = None,
    input_payload: dict[str, Any] | None = None,
    system_root: Path | None = None,
) -> int:
    """Persist a compiled context summary and return its row id."""
    _ensure_db(system_root)
    sections_str = (
        json.dumps(sections_included, ensure_ascii=False)
        if sections_included is not None
        else None
    )
    payload_str = (
        json.dumps(input_payload, ensure_ascii=False)
        if input_payload is not None
        else None
    )

    conn = connect_sqlite_from_system_db(
        DB_NAME, str(system_root) if system_root else None
    )
    try:
        conn.execute(
            """
            INSERT INTO context_summaries (
                session_id, vault_name, turn_index, template_name, template_source,
                template_hash, model_alias, raw_output, budget_used,
                sections_included, compiled_prompt, input_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                vault_name,
                turn_index,
                template.name,
                template.source,
                template.sha256,
                model_alias,
                raw_output,
                budget_used,
                sections_str,
                compiled_prompt,
                payload_str,
            ),
        )
        conn.commit()
        return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    except Exception as exc:  # pragma: no cover - defensive
        logger.error(
            f"Failed to insert context summary for session {session_id}: {exc}"
        )
        raise
    finally:
        conn.close()


def get_recent_summaries(
    session_id: str,
    vault_name: str,
    limit: int | None = 5,
    system_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Fetch recent compiled summaries for a session/vault."""
    _ensure_db(system_root)
    conn = connect_sqlite_from_system_db(
        DB_NAME, str(system_root) if system_root else None
    )
    try:
        if limit is None:
            rows = conn.execute(
                """
                SELECT turn_index, template_name, template_hash, model_alias,
                       raw_output, compiled_prompt, input_payload, created_at
                FROM context_summaries
                WHERE session_id = ? AND vault_name = ?
                ORDER BY id DESC
                """,
                (session_id, vault_name),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT turn_index, template_name, template_hash, model_alias,
                       raw_output, compiled_prompt, input_payload, created_at
                FROM context_summaries
                WHERE session_id = ? AND vault_name = ?
                ORDER BY id DESC LIMIT ?
                """,
                (session_id, vault_name, limit),
            ).fetchall()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            f"Failed to fetch recent context summaries for session {session_id}: {exc}"
        )
        return []
    finally:
        conn.close()

    snapshots: list[dict[str, Any]] = []
    for row in rows:
        try:
            input_payload = json.loads(row[6]) if row[6] else None
        except Exception:
            input_payload = None
        snapshots.append(
            {
                "turn_index": row[0],
                "template_name": row[1],
                "template_hash": row[2],
                "model_alias": row[3],
                "raw_output": row[4],
                "compiled_prompt": row[5],
                "input_payload": input_payload,
                "created_at": row[7],
            }
        )

    return snapshots
