"""Shared embedding/vector services."""

from core.vector.service import (
    EmbeddingRequestResult,
    EmbeddingVector,
    VectorService,
    build_embedding_space_id,
    cosine_similarity,
    fingerprint_text,
)
from core.vector.store import (
    SQLitePythonVectorStore,
    StoredVector,
    VectorSearchResult,
    VectorStore,
    VectorStoreItem,
)

__all__ = [
    "EmbeddingRequestResult",
    "EmbeddingVector",
    "SQLitePythonVectorStore",
    "StoredVector",
    "VectorService",
    "VectorSearchResult",
    "VectorStore",
    "VectorStoreItem",
    "build_embedding_space_id",
    "cosine_similarity",
    "fingerprint_text",
]
