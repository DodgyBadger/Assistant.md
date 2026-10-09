"""Backed-up retirement is scoped, repeatable, and absent on fresh installations."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.chat.chat_store import ChatStore
from core.database_migrations import SQLiteMigration
from core.memory import retirement
from core.memory.session_map.checkpoints import build_session_map_context_message
from core.memory.session_map.models import (
    SessionMapDraft,
    SessionMapTrajectory,
    SourceRange,
)
from core.system_migrations import get_system_migration_status, run_system_migrations
from validation.core.base_scenario import BaseScenario


class SessionSummaryDataRetirementScenario(BaseScenario):
    async def test_scenario(self):
        root = self.artifacts_dir / "populated-system"
        root.mkdir()
        db = root / "session_summaries.db"
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE session_summaries (summary TEXT)")
            conn.execute("INSERT INTO session_summaries VALUES ('retirement fixture')")
            conn.execute("CREATE TABLE session_summary_artifacts (path TEXT)")
            conn.execute("CREATE TABLE session_summary_field_vectors (vector TEXT)")
            conn.execute(
                "CREATE VIRTUAL TABLE session_summaries_fts USING fts5(summary)"
            )
            conn.execute(
                "INSERT INTO session_summaries_fts VALUES ('retirement fixture')"
            )
            conn.execute("CREATE TABLE user_extension (value TEXT)")
            conn.execute("INSERT INTO user_extension VALUES ('preserve unknown state')")
            conn.execute(
                "CREATE TABLE schema_migrations "
                "(namespace TEXT, version INTEGER, name TEXT, "
                "PRIMARY KEY(namespace, version))"
            )
            conn.executemany(
                "INSERT INTO schema_migrations VALUES ('session_summaries', ?, 'fixture')",
                [(1,), (2,), (3,)],
            )
        chats = ChatStore(str(root))
        chats.ensure_session("canonical", "Vault", owner_principal_id="local-user")
        chats.add_messages(
            "canonical",
            "Vault",
            [
                ModelRequest(
                    parts=[UserPromptPart(content="Canonical decision evidence.")]
                ),
                ModelResponse(parts=[TextPart(content="Preserve the source.")]),
            ],
        )
        draft = SessionMapDraft(
            trajectory=SessionMapTrajectory(
                text="Canonical continuity remains source-linked.",
                sources=(SourceRange(start=0, end=1),),
            )
        )
        message = build_session_map_context_message(draft)
        chats.add_context_checkpoint(
            session_id="canonical",
            vault_name="Vault",
            checkpoint_id="preserved-map",
            checkpoint_kind="session_map",
            source="validation",
            message_count_before=2,
            last_message_sequence_index=1,
            summary_message=message,
            replacement_history=[message],
            metadata={
                "map": draft.model_dump(mode="json"),
                "map_observed_through_sequence_index": 1,
            },
        )
        with sqlite3.connect(root / "chat_sessions.db") as conn:
            canonical_before = conn.execute("SELECT * FROM chat_sessions").fetchall()
            messages_before = conn.execute("SELECT * FROM chat_messages").fetchall()
            maps_before = conn.execute(
                "SELECT * FROM chat_compaction_checkpoints"
            ).fetchall()
        before = get_system_migration_status(root)
        summary_before = next(
            t for t in before.targets if t.db_name == "session_summaries"
        )
        assert summary_before.pending_versions == (4,)
        try:
            run_system_migrations(root, backup=False)
        except ValueError:
            pass
        else:
            raise AssertionError("Destructive summary retirement requires a backup")
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT summary FROM session_summaries").fetchone()[0]

        after = run_system_migrations(root)
        target = next(t for t in after.targets if t.db_name == "session_summaries")
        assert target.applied_versions == (1, 2, 3, 4)
        assert not target.pending_versions and target.backup_path
        backup = Path(target.backup_path)
        with sqlite3.connect(backup) as conn:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert (
                conn.execute("SELECT summary FROM session_summaries").fetchone()[0]
                == "retirement fixture"
            )
            assert (
                conn.execute("SELECT count(*) FROM session_summaries_fts").fetchone()[0]
                == 1
            )
        with sqlite3.connect(db) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            assert tables == {"schema_migrations", "user_extension"}
            assert (
                conn.execute("SELECT value FROM user_extension").fetchone()[0]
                == "preserve unknown state"
            )
        with sqlite3.connect(root / "chat_sessions.db") as conn:
            assert (
                conn.execute("SELECT * FROM chat_sessions").fetchall()
                == canonical_before
            )
            assert (
                conn.execute("SELECT * FROM chat_messages").fetchall()
                == messages_before
            )
            assert (
                conn.execute("SELECT * FROM chat_compaction_checkpoints").fetchall()
                == maps_before
            )
        repeated = run_system_migrations(root)
        assert repeated.pending_count == 0
        assert all(t.backup_path is None for t in repeated.targets)
        assert backup.exists()

        failure_root = self.artifacts_dir / "interrupted-system"
        failure_root.mkdir()
        failure_db = failure_root / "session_summaries.db"
        with (
            sqlite3.connect(backup) as source,
            sqlite3.connect(failure_db) as destination,
        ):
            source.backup(destination)

        def interrupted(conn):
            retirement._retire_tables(conn)
            raise RuntimeError("Injected interruption before migration bookkeeping")

        with patch.object(
            retirement,
            "SESSION_SUMMARY_RETIREMENT_MIGRATIONS",
            (
                SQLiteMigration(
                    version=4, name="fixture_interruption", apply=interrupted
                ),
            ),
        ):
            try:
                run_system_migrations(failure_root)
            except RuntimeError:
                pass
            else:
                raise AssertionError("An interrupted retirement must fail visibly")
        with sqlite3.connect(failure_db) as conn:
            assert (
                conn.execute("SELECT summary FROM session_summaries").fetchone()[0]
                == "retirement fixture"
            )
            assert (
                conn.execute("SELECT count(*) FROM session_summaries_fts").fetchone()[0]
                == 1
            )
            assert conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall() == [(1,), (2,), (3,)]
        assert run_system_migrations(failure_root).pending_count == 0

        fresh = self.artifacts_dir / "fresh-system"
        fresh.mkdir()
        assert run_system_migrations(fresh).pending_count == 0
        assert not (fresh / "session_summaries.db").exists()
