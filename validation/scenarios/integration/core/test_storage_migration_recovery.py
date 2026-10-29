"""Oldest transcript backfill and interrupted destructive migration recovery."""

from __future__ import annotations

import sqlite3

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.session_summary_retirement import (
    canonical_snapshot,
    seed_canonical_history,
    seed_legacy_summaries,
)
from validation.scenarios.support.storage_migration_recovery import (
    canonical_message_rows,
    reject_retirement_bookkeeping,
    seed_pre_migration_transcript,
    summary_snapshot,
)

pytestmark = pytest.mark.scenario


async def test_oldest_supported_transcript_schema_backfills_on_startup(
    app: Application,
):
    vault = app.vault("OldestTranscriptVault")
    root = app.root / "system"
    before = seed_pre_migration_transcript(root, vault.name)
    with sqlite3.connect(root / "chat_sessions.db") as conn:
        assert {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        } == {"chat_sessions", "chat_messages", "sqlite_sequence"}
        assert "owner_principal_id" not in {
            row[1] for row in conn.execute("PRAGMA table_info(chat_sessions)")
        }
        assert conn.execute("SELECT content_text FROM chat_messages").fetchall() == [
            (None,),
            (None,),
        ]
    await app.start()
    from core.chat.schema import ensure_chat_sessions_schema
    from core.chat.transcript_retrieval import TranscriptRetrievalService
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.runtime.state import get_runtime_context

    runtime = get_runtime_context()
    retrieval = TranscriptRetrievalService(
        runtime.chat_store, runtime.chat_session_access, excerpt_chars=80
    )
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        hits = retrieval.search(
            vault_name=vault.name,
            session_id="oldest-transcript",
            query="cobalt lantern",
        )
    assert [
        (hit.anchor.session_id, hit.anchor.sequence_index, hit.vault_name)
        for hit in hits
    ] == [("oldest-transcript", 0, vault.name)]
    assert len(hits[0].excerpt) <= 80
    assert canonical_message_rows(root) == before
    with sqlite3.connect(root / "chat_sessions.db") as conn:
        versions = conn.execute(
            "SELECT version FROM schema_migrations WHERE namespace='chat_sessions' ORDER BY version"
        ).fetchall()
        assert versions == [(version,) for version in range(1, 11)]
        assert conn.execute(
            "SELECT owner_principal_id FROM chat_sessions WHERE session_id='oldest-transcript'"
        ).fetchone() == (LOCAL_USER_AUTHORITY.principal_id,)
    # Repeat the actual startup schema/migration boundary, not modern writes.
    ensure_chat_sessions_schema(str(root), apply_migrations=True)
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        repeated = retrieval.search(
            vault_name=vault.name,
            session_id="oldest-transcript",
            query="cobalt lantern",
        )
    assert repeated == hits
    assert canonical_message_rows(root) == before
    app.evidence.save_json(
        "oldest-transcript-migration.json",
        {
            "canonical_before": before,
            "canonical_after": canonical_message_rows(root),
            "versions": versions,
        },
    )


async def test_summary_retirement_bookkeeping_failure_rolls_back_and_retries(
    app: Application,
):
    await app.start()
    from core.system_migrations import (
        get_system_migration_status,
        run_system_migrations,
    )

    root = app.root / "interrupted-retirement"
    database = seed_legacy_summaries(root)
    canonical = seed_canonical_history(root)
    before = summary_snapshot(database)
    with reject_retirement_bookkeeping(database):
        with pytest.raises(
            sqlite3.IntegrityError,
            match="retirement bookkeeping rejected after table drops",
        ):
            run_system_migrations(root)
        assert summary_snapshot(database) == before
        with sqlite3.connect(database) as conn:
            assert conn.execute(
                "SELECT summary FROM session_summaries_fts WHERE session_summaries_fts MATCH 'retirement'"
            ).fetchall() == [("retirement fixture",)]
        assert canonical_snapshot(root) == canonical
        pending = get_system_migration_status(root)
        assert next(
            target
            for target in pending.targets
            if target.db_name == "session_summaries"
        ).pending_versions == (4,)
    backups = list((root / "migration_backups").glob("session_summaries.db.backup-*"))
    assert len(backups) == 1
    assert summary_snapshot(backups[0]) == before
    migrated = run_system_migrations(root)
    target = next(
        target for target in migrated.targets if target.db_name == "session_summaries"
    )
    assert target.applied_versions == (1, 2, 3, 4) and not target.pending_versions
    assert summary_snapshot(database) == {
        "schema_migrations": [
            ("session_summaries", 1, "fixture"),
            ("session_summaries", 2, "fixture"),
            ("session_summaries", 3, "fixture"),
            ("session_summaries", 4, "retire_session_summary_data"),
        ],
        "user_extension": [("preserve unknown state",)],
    }
    assert canonical_snapshot(root) == canonical
    repeated = run_system_migrations(root)
    assert repeated.pending_count == 0
    assert canonical_snapshot(root) == canonical
    app.evidence.save_json(
        "summary-retirement-recovery.json",
        {
            "before": before,
            "after": summary_snapshot(database),
            "canonical": canonical,
            "failure_backups": [str(path) for path in backups],
        },
    )
