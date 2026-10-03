"""Vector storage abstractions with a plain SQLite implementation."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Protocol

from core.database import connect_sqlite_from_system_db
from core.vector.service import EmbeddingVector, cosine_similarity, fingerprint_text


@dataclass(frozen=True)
class StoredVector:
    """One vector row stored behind the vector-store abstraction."""

    namespace: str
    item_id: str
    text: str
    vector: tuple[float, ...]
    embedding_space_id: str
    dimensions: int
    model_alias: str
    provider_name: str
    model_name: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VectorSearchResult:
    """One similarity search result."""

    namespace: str
    item_id: str
    text: str
    score: float
    embedding_space_id: str
    dimensions: int
    model_alias: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VectorStoreItem:
    """One vector and its stable item identity for a store mutation."""

    item_id: str
    embedding: EmbeddingVector
    metadata: dict[str, Any] = field(default_factory=dict)


class VectorStore(Protocol):
    """Storage contract that can later be backed by sqlite-vec or another store."""

    def upsert(
        self,
        *,
        namespace: str,
        item_id: str,
        embedding: EmbeddingVector,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Insert or replace one stored vector."""
        ...

    def search_similar(
        self,
        *,
        namespace: str,
        query: EmbeddingVector,
        limit: int = 10,
        min_score: float | None = None,
    ) -> tuple[VectorSearchResult, ...]:
        """Search for similar vectors in the same namespace and embedding space."""
        ...

    def delete_items(
        self,
        *,
        namespace: str,
        item_ids: tuple[str, ...],
    ) -> int:
        """Delete stored vectors for item ids in one namespace."""
        ...

    def replace_items(
        self,
        *,
        namespace: str,
        item_ids: tuple[str, ...],
        items: tuple[VectorStoreItem, ...],
    ) -> None:
        """Atomically replace the selected item ids."""
        ...


class SQLitePythonVectorStore:
    """Plain SQLite vector store that computes similarity in Python."""

    def __init__(
        self,
        *,
        db_name: str,
        table_name: str = "vectors",
        system_root: str | None = None,
    ):
        self.db_name = db_name
        self.table_name = table_name
        self.system_root = system_root
        self._ensure_schema()

    def upsert(
        self,
        *,
        namespace: str,
        item_id: str,
        embedding: EmbeddingVector,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Insert or replace one stored vector."""
        with self._connect() as conn:
            self._upsert_with_connection(
                conn,
                namespace=namespace,
                item_id=item_id,
                embedding=embedding,
                metadata=metadata,
            )

    def search_similar(
        self,
        *,
        namespace: str,
        query: EmbeddingVector,
        limit: int = 10,
        min_score: float | None = None,
    ) -> tuple[VectorSearchResult, ...]:
        """Search for similar vectors in the same namespace and embedding space."""
        if limit <= 0:
            return ()
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT namespace, item_id, input_text, embedding_space_id,
                       dimensions, model_alias, vector_json, metadata_json
                FROM {self.table_name}
                WHERE namespace = ?
                  AND embedding_space_id = ?
                  AND dimensions = ?
                """,
                (namespace, query.embedding_space_id, query.dimensions),
            ).fetchall()

        results: list[VectorSearchResult] = []
        for row in rows:
            vector = _load_vector(row["vector_json"])
            score = cosine_similarity(query.vector, vector)
            if min_score is not None and score < min_score:
                continue
            results.append(
                VectorSearchResult(
                    namespace=str(row["namespace"]),
                    item_id=str(row["item_id"]),
                    text=str(row["input_text"]),
                    score=score,
                    embedding_space_id=str(row["embedding_space_id"]),
                    dimensions=int(row["dimensions"]),
                    model_alias=str(row["model_alias"]),
                    metadata=_load_json(row["metadata_json"]),
                )
            )
        results.sort(key=lambda item: item.score, reverse=True)
        return tuple(results[:limit])

    def delete_items(
        self,
        *,
        namespace: str,
        item_ids: tuple[str, ...],
    ) -> int:
        """Delete stored vectors for item ids in one namespace."""
        if not item_ids:
            return 0
        placeholders = ", ".join("?" for _ in item_ids)
        with self._connect() as conn:
            cursor = conn.execute(
                f"""
                DELETE FROM {self.table_name}
                WHERE namespace = ?
                  AND item_id IN ({placeholders})
                """,
                (namespace, *item_ids),
            )
            return cursor.rowcount

    def replace_items(
        self,
        *,
        namespace: str,
        item_ids: tuple[str, ...],
        items: tuple[VectorStoreItem, ...],
    ) -> None:
        """Replace selected vectors in one SQLite transaction."""
        selected_item_ids = set(item_ids)
        replacement_item_ids = [item.item_id for item in items]
        if len(replacement_item_ids) != len(set(replacement_item_ids)):
            raise ValueError("Replacement vector item ids must be unique")
        if not set(replacement_item_ids).issubset(selected_item_ids):
            raise ValueError("Replacement vectors must belong to the selected item ids")

        with self._connect() as conn:
            if item_ids:
                placeholders = ", ".join("?" for _ in item_ids)
                conn.execute(
                    f"""
                    DELETE FROM {self.table_name}
                    WHERE namespace = ?
                      AND item_id IN ({placeholders})
                    """,
                    (namespace, *item_ids),
                )
            for item in items:
                self._upsert_with_connection(
                    conn,
                    namespace=namespace,
                    item_id=item.item_id,
                    embedding=item.embedding,
                    metadata=item.metadata,
                )

    def _upsert_with_connection(
        self,
        conn: sqlite3.Connection,
        *,
        namespace: str,
        item_id: str,
        embedding: EmbeddingVector,
        metadata: dict[str, Any] | None,
    ) -> None:
        """Insert or replace one vector inside the caller-owned transaction."""
        row_metadata = {
            "input_type": embedding.input_type,
            "provider": embedding.provider,
            "model_string": embedding.model_string,
        }
        row_metadata.update(metadata or {})
        conn.execute(
            f"""
            INSERT INTO {self.table_name} (
                namespace, item_id, input_text, input_fingerprint,
                embedding_space_id, dimensions, model_alias, provider_name,
                model_name, vector_json, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(namespace, item_id, embedding_space_id)
            DO UPDATE SET
                input_text = excluded.input_text,
                input_fingerprint = excluded.input_fingerprint,
                dimensions = excluded.dimensions,
                model_alias = excluded.model_alias,
                provider_name = excluded.provider_name,
                model_name = excluded.model_name,
                vector_json = excluded.vector_json,
                metadata_json = excluded.metadata_json
            """,
            (
                namespace,
                item_id,
                embedding.text,
                fingerprint_text(embedding.text),
                embedding.embedding_space_id,
                embedding.dimensions,
                embedding.model_alias,
                embedding.provider_name,
                embedding.model_name,
                json.dumps(list(embedding.vector), separators=(",", ":")),
                json.dumps(row_metadata, sort_keys=True),
            ),
        )

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table_name} (
                    namespace TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    input_text TEXT NOT NULL,
                    input_fingerprint TEXT NOT NULL,
                    embedding_space_id TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    model_alias TEXT NOT NULL,
                    provider_name TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    vector_json TEXT NOT NULL,
                    metadata_json TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (namespace, item_id, embedding_space_id)
                )
                """
            )
            conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{self.table_name}_space
                ON {self.table_name}(namespace, embedding_space_id, dimensions)
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = connect_sqlite_from_system_db(self.db_name, self.system_root)
        conn.row_factory = sqlite3.Row
        return conn


def _load_vector(raw: str) -> tuple[float, ...]:
    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        raise ValueError("Stored vector must be a JSON list")
    return tuple(float(value) for value in parsed)


def _load_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else {}
