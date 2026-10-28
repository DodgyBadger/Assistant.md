"""Legacy summary database inputs and preserved canonical session snapshots."""

import sqlite3
from pathlib import Path


def seed_legacy_summaries(root: Path) -> Path:
    root.mkdir()
    db = root / "session_summaries.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE session_summaries (summary TEXT)")
        conn.execute("INSERT INTO session_summaries VALUES ('retirement fixture')")
        conn.execute("CREATE TABLE session_summary_artifacts (path TEXT)")
        conn.execute("CREATE TABLE session_summary_field_vectors (vector TEXT)")
        conn.execute("CREATE VIRTUAL TABLE session_summaries_fts USING fts5(summary)")
        conn.execute("INSERT INTO session_summaries_fts VALUES ('retirement fixture')")
        conn.execute("CREATE TABLE user_extension (value TEXT)")
        conn.execute("INSERT INTO user_extension VALUES ('preserve unknown state')")
        conn.execute(
            "CREATE TABLE schema_migrations (namespace TEXT, version INTEGER, name TEXT, PRIMARY KEY(namespace, version))"
        )
        conn.executemany(
            "INSERT INTO schema_migrations VALUES ('session_summaries', ?, 'fixture')",
            [(1,), (2,), (3,)],
        )
    return db


def seed_canonical_history(root: Path) -> dict[str, list]:
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        UserPromptPart,
    )

    from core.chat.chat_store import ChatStore
    from core.memory.session_map.checkpoints import build_session_map_context_message
    from core.memory.session_map.models import (
        SessionMapDraft,
        SessionMapTrajectory,
        SourceRange,
    )

    store = ChatStore(str(root))
    store.ensure_session("canonical", "Vault", owner_principal_id="local-user")
    store.add_messages(
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
    store.add_context_checkpoint(
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
    return canonical_snapshot(root)


def canonical_snapshot(root: Path) -> dict[str, list]:
    with sqlite3.connect(root / "chat_sessions.db") as conn:
        return {
            name: conn.execute(f"SELECT * FROM {name}").fetchall()
            for name in (
                "chat_sessions",
                "chat_messages",
                "chat_compaction_checkpoints",
            )
        }


def seed_dependent(root: Path, policy: str, target: str) -> Path:
    root.mkdir()
    db = root / "session_summaries.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE session_summaries (id INTEGER PRIMARY KEY, summary TEXT)"
        )
        conn.execute("INSERT INTO session_summaries VALUES (1, 'owned summary')")
        if target == "session_summaries_fts_data":
            conn.execute(
                "CREATE VIRTUAL TABLE session_summaries_fts USING fts5(summary)"
            )
        conn.execute(
            f"CREATE TABLE user_extension (summary_id INTEGER REFERENCES {target}(id) ON DELETE {policy}, value TEXT)"
        )
        conn.execute(
            "INSERT INTO user_extension VALUES (1, 'preserve dependent state')"
        )
    return db
