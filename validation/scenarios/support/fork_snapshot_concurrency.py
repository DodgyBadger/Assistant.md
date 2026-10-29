"""Real concurrent SQLite snapshots with native provider-message mutations."""

from __future__ import annotations

import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from pydantic import TypeAdapter
from pydantic_ai.messages import ModelMessage


def map_context(text):
    from core.memory.session_map.checkpoints import build_session_map_context_message
    from core.memory.session_map.models import (
        SessionMapDraft,
        SessionMapTrajectory,
        SourceRange,
    )

    draft = SessionMapDraft(
        trajectory=SessionMapTrajectory(
            text=text, sources=(SourceRange(start=0, end=1),)
        )
    )
    return draft, build_session_map_context_message(draft)


def concurrent_fork_and_rewrite(root, store, vault, source, child, replacement):

    adapter = TypeAdapter(ModelMessage)
    draft, context = map_context(
        "The replacement canonical question and answer are current."
    )
    database = root / "chat_sessions.db"
    with sqlite3.connect(database) as conn:
        conn.execute(
            """CREATE TRIGGER fork_snapshot_workload BEFORE INSERT ON chat_sessions
            WHEN NEW.session_id='coherent-first-child'
            BEGIN SELECT sum(x) FROM (WITH RECURSIVE work(x) AS
                (VALUES(0) UNION ALL SELECT x+1 FROM work WHERE x<5000000)
                SELECT x FROM work); END"""
        )
    attempted = Event()

    def rewrite():
        with sqlite3.connect(database, timeout=10) as conn:
            attempted.set()
            started = time.monotonic()
            conn.execute("BEGIN IMMEDIATE")
            waited = time.monotonic() - started
            # One actual independent writer transaction changes both canonical
            # fixture input and its matching context. No intermediate revision
            # exists for a fork to observe, and no production method is replaced.
            for index, message in enumerate(replacement):
                content = message.parts[0].content
                conn.execute(
                    "UPDATE chat_messages SET message_json=?, content_text=? WHERE session_id=? AND vault_name=? AND sequence_index=?",
                    (
                        adapter.dump_json(message).decode(),
                        content,
                        source,
                        vault,
                        index,
                    ),
                )
            conn.execute(
                "UPDATE chat_compaction_checkpoints SET summary_message_json=?, replacement_history_json=?, metadata_json=? WHERE session_id=? AND vault_name=?",
                (
                    adapter.dump_json(context).decode(),
                    TypeAdapter(list[ModelMessage]).dump_json([context]).decode(),
                    json.dumps(
                        {
                            "map": draft.model_dump(mode="json"),
                            "map_observed_through_sequence_index": 1,
                        }
                    ),
                    source,
                    vault,
                ),
            )
            conn.execute(
                "UPDATE chat_sessions SET metadata_json=json_set(COALESCE(metadata_json,'{}'),'$.history_revision',COALESCE(json_extract(metadata_json,'$.history_revision'),0)+1) WHERE session_id=? AND vault_name=?",
                (source, vault),
            )
            return waited

    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            forked = workers.submit(
                store.fork_session,
                source_session_id=source,
                new_session_id=child,
                vault_name=vault,
                through_sequence_index=1,
                title="Coherent concurrent snapshot",
            )
            deadline = time.monotonic() + 10
            locked_since = None
            while True:
                assert (
                    not forked.done()
                ), "Fork finished before concurrent writer admission"
                with sqlite3.connect(database, timeout=0) as probe:
                    try:
                        probe.execute("BEGIN IMMEDIATE")
                    except sqlite3.OperationalError as error:
                        assert "locked" in str(error)
                        if locked_since is None:
                            locked_since = time.monotonic()
                        if time.monotonic() - locked_since >= 0.05:
                            break
                    else:
                        probe.rollback()
                        locked_since = None
                assert time.monotonic() < deadline
                time.sleep(0.002)
            writer = workers.submit(rewrite)
            assert attempted.wait(5)
            copied = forked.result(timeout=15)
            waited = writer.result(timeout=15)
            return copied, waited, context
    finally:
        with sqlite3.connect(database) as conn:
            conn.execute("DROP TRIGGER fork_snapshot_workload")
