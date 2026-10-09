"""Bounded lexical session discovery; canonical history remains authoritative."""

from __future__ import annotations

import json
from typing import Any, Literal

from core.chat.chat_store import ChatStore, StoredChatSession
from core.chat.session_access import ChatSessionAccessService
from core.chat.transcript_retrieval import TranscriptRetrievalService
from core.logger import UnifiedLogger
from core.utils.fts import build_fts_query

logger = UnifiedLogger(tag="session-discovery")
MAX_DISCOVERY_LIMIT = 20


def session_workspace(session: StoredChatSession) -> str:
    """Read canonical workspace metadata without hydrating messages or summaries."""
    metadata = json.loads(session.metadata_json or "{}")
    workspace = metadata.get("workspace", {})
    return str(workspace.get("path") or "") if isinstance(workspace, dict) else ""


class SessionDiscoveryService:
    """Compose authorized metadata, map, and transcript hits without model calls."""

    def __init__(self, store: ChatStore, access: ChatSessionAccessService) -> None:
        self._store = store
        self._access = access
        self._transcripts = TranscriptRetrievalService(store, access)

    def search(
        self,
        *,
        vault_name: str,
        query: str,
        limit: int = 5,
        workspace: str = "",
        workspace_prefix: bool = False,
        active_workspace: str = "",
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= MAX_DISCOVERY_LIMIT:
            raise ValueError("Session discovery limit must be between 1 and 20")
        if not build_fts_query(query):
            raise ValueError("Session discovery requires searchable text")
        sessions = {
            session.session_id: session
            for session in self._access.list_sessions(vault_name)
            if not workspace
            or (
                session_workspace(session).startswith(workspace + "/")
                if workspace_prefix
                else session_workspace(session) == workspace
            )
        }
        ids = set(sessions)
        candidates: dict[str, dict[str, Any]] = {}

        def admit(session_id: str, evidence: dict[str, Any], rank: int) -> None:
            session = sessions[session_id]
            candidate = candidates.setdefault(
                session_id,
                {
                    "session_id": session_id,
                    "vault_name": vault_name,
                    "title": session.title,
                    "workspace_path": session_workspace(session) or None,
                    "last_activity_at": session.last_activity_at,
                    "score": 0.0,
                    "evidence": [],
                },
            )
            evidence["rank"] = rank
            evidence["match_type"] = "lexical"
            candidate["evidence"].append(evidence)
            # At most one contribution per source/session; revisions are not votes.
            candidate["score"] += 1.0 / (60 + rank)

        fetch_limit = min(max(limit * 10, 50), 200)
        sources: tuple[Literal["session_metadata", "session_map"], ...] = (
            "session_metadata",
            "session_map",
        )
        for source in sources:
            hits = self._store.search_discovery_text(
                vault_name=vault_name,
                session_ids=ids,
                query=query,
                source=source,
                limit=fetch_limit,
            )
            for rank, hit in enumerate(hits, start=1):
                evidence: dict[str, Any] = {
                    "source": hit.source,
                    "excerpt": hit.excerpt,
                }
                if hit.checkpoint_id:
                    evidence.update(
                        checkpoint_id=hit.checkpoint_id, historical=hit.historical
                    )
                admit(hit.session_id, evidence, rank)
        for transcript_hit in self._transcripts.search_vault(
            vault_name=vault_name, query=query, limit=fetch_limit, session_ids=ids
        ):
            admit(
                transcript_hit.anchor.session_id,
                {
                    "source": "transcript",
                    "session_id": transcript_hit.anchor.session_id,
                    "sequence_index": transcript_hit.anchor.sequence_index,
                    "role": transcript_hit.role,
                    "excerpt": transcript_hit.excerpt,
                    "created_at": transcript_hit.created_at,
                    "source_kind": transcript_hit.source_kind,
                    "tool_names": list(transcript_hit.tool_names),
                },
                transcript_hit.rank,
            )
        for candidate in candidates.values():
            if (
                not workspace
                and active_workspace
                and candidate["workspace_path"] == active_workspace
            ):
                candidate["score"] += 0.002
            candidate["score"] = round(candidate["score"], 6)
        results = sorted(
            candidates.values(), key=lambda row: (-row["score"], row["session_id"])
        )[:limit]
        logger.add_sink("validation").info(
            "session_discovery_completed",
            data={
                "event": "session_discovery_completed",
                "status": "completed",
                "vault_name": vault_name,
                "candidate_count": len(candidates),
                "returned_count": len(results),
                "source_counts": {
                    source: sum(
                        any(e["source"] == source for e in row["evidence"])
                        for row in results
                    )
                    for source in ("session_metadata", "session_map", "transcript")
                },
            },
        )
        return results
