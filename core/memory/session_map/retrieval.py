"""Authorized, chronological map browsing and complete-claim retrieval."""

from __future__ import annotations

import base64
import binascii
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.chat.chat_store import ChatStore, StoredContextCheckpoint
from core.chat.session_access import ChatSessionAccessService
from core.memory.session_map.checkpoints import load_session_map_checkpoint
from core.memory.session_map.models import SessionMapEntryKind
from core.settings.store import get_general_settings


class SessionMapRequestError(ValueError):
    """A recoverable map-tool request error, without private resource details."""


@dataclass(frozen=True)
class SessionMapRetrievalLimits:
    page_size: int
    max_page_size: int
    trajectory_max_maps: int
    entries_max_maps: int

    @classmethod
    def configured(cls) -> SessionMapRetrievalLimits:
        settings = get_general_settings()
        names = (
            "session_map_history_page_size",
            "session_map_history_max_page_size",
            "session_map_trajectory_max_maps",
            "session_map_entries_max_maps",
        )
        values: list[int] = []
        for name in names:
            value = settings[name].value
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer.")
            values.append(value)
        limits = cls(*values)
        if limits.page_size > limits.max_page_size:
            raise ValueError(
                "session_map_history_page_size must be at most session_map_history_max_page_size."
            )
        return limits


class _MapCursor(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal["session_maps_v1"] = "session_maps_v1"
    session_id: str
    vault_name: str
    snapshot_id: int = Field(gt=0)
    snapshot_checkpoint_id: str
    after_revision: int = Field(gt=0)

    def encode(self) -> str:
        return base64.urlsafe_b64encode(self.model_dump_json().encode()).decode()


def _selected_values(
    name: str, values: list[str] | None, allowed: tuple[str, ...]
) -> set[str] | None:
    if values is None:
        return None
    invalid = [value for value in values if value not in allowed]
    if not values or invalid:
        raise SessionMapRequestError(
            f"Invalid {name}: {values!r}. Supported values: {', '.join(allowed)}. "
            f"Supply a nonempty list, or omit {name} to use defaults."
        )
    return set(values)


class SessionMapRetrievalService:
    """Browse checkpoint snapshots and project sections without losing provenance."""

    def __init__(self, store: ChatStore, access: ChatSessionAccessService) -> None:
        self._store = store
        self._access = access

    def _authorize(self, vault_name: str, session_id: str) -> None:
        session = self._access.get_session_by_id(session_id)
        if session is None or session.vault_name != vault_name:
            raise SessionMapRequestError("Session is unavailable in the current vault.")

    @staticmethod
    def _metadata(
        checkpoint: StoredContextCheckpoint,
        revision: int,
        current_id: str | None,
    ) -> dict[str, object]:
        current = checkpoint.checkpoint_id == current_id
        return {
            "session_id": checkpoint.session_id,
            "checkpoint_id": checkpoint.checkpoint_id,
            "revision": revision,
            "created_at": checkpoint.created_at,
            "is_current": current,
            "historical": not current,
            "observed_through_sequence_index": checkpoint.observed_through_sequence_index,
            "compacted_through_sequence_index": checkpoint.last_message_sequence_index,
        }

    def list_maps(
        self,
        *,
        vault_name: str,
        session_id: str,
        limit: int | None = None,
        cursor: str = "",
    ) -> dict[str, object]:
        self._authorize(vault_name, session_id)
        limits = SessionMapRetrievalLimits.configured()
        page_size = limits.page_size if limit is None else limit
        if not 1 <= page_size <= limits.max_page_size:
            raise SessionMapRequestError(
                f"list_session_maps limit must be between 1 and {limits.max_page_size}. "
                "Omit limit to use the configured default."
            )
        position = 0
        snapshot_id: int | None = None
        if cursor:
            try:
                if len(cursor) > 4096:
                    raise ValueError("Oversized cursor")
                decoded = _MapCursor.model_validate_json(
                    base64.b64decode(cursor, altchars=b"-_", validate=True)
                )
            except (ValueError, ValidationError, binascii.Error) as exc:
                raise SessionMapRequestError(
                    "Invalid list_session_maps cursor. Use next_cursor from the previous "
                    "page for the same session, or omit cursor to start again."
                ) from exc
            anchor = self._store.get_context_checkpoint(
                session_id, vault_name, decoded.snapshot_checkpoint_id
            )
            if (
                decoded.session_id != session_id
                or decoded.vault_name != vault_name
                or anchor is None
                or anchor.id != decoded.snapshot_id
                or anchor.checkpoint_kind != "session_map"
            ):
                raise SessionMapRequestError(
                    "Stale or mismatched list_session_maps cursor. Omit cursor to start again."
                )
            snapshot_id = decoded.snapshot_id
            position = decoded.after_revision
        snapshot_id, total = self._store.session_map_history_bounds(
            session_id, vault_name, through_id=snapshot_id
        )
        if position > total:
            raise SessionMapRequestError(
                "Invalid cursor position. Omit cursor to start again."
            )
        latest = self._store.get_latest_context_checkpoint(session_id, vault_name)
        rows = self._store.list_session_map_revisions(
            session_id,
            vault_name,
            through_id=snapshot_id,
            after_revision=position,
            limit=page_size,
        )
        maps = []
        for revision, checkpoint in rows:
            draft = load_session_map_checkpoint(checkpoint)
            maps.append(
                {
                    **self._metadata(
                        checkpoint, revision, latest.checkpoint_id if latest else None
                    ),
                    "schema_version": draft.schema_version,
                    "trajectory_excerpt": (
                        draft.trajectory.text[:240] if draft.trajectory else None
                    ),
                    "entry_counts": dict(
                        Counter(entry.kind.value for entry in draft.entries)
                    ),
                }
            )
        next_cursor = None
        if rows and rows[-1][0] < total:
            snapshot = self._store.list_session_map_revisions(
                session_id,
                vault_name,
                through_id=snapshot_id,
                after_revision=total - 1,
                limit=1,
            )[0][1]
            next_cursor = _MapCursor(
                session_id=session_id,
                vault_name=vault_name,
                snapshot_id=snapshot_id,
                snapshot_checkpoint_id=snapshot.checkpoint_id,
                after_revision=rows[-1][0],
            ).encode()
        return {
            "session_maps": maps,
            "order": "oldest_first",
            "total_count": total,
            "returned_count": len(maps),
            "limit": page_size,
            "max_limit": limits.max_page_size,
            "next_cursor": next_cursor,
            "snapshot_through_revision": total,
        }

    def get_maps(
        self,
        *,
        vault_name: str,
        session_id: str,
        checkpoint_id: str = "",
        checkpoint_ids: list[str] | None = None,
        fields: list[str] | None = None,
        entry_kinds: list[str] | None = None,
    ) -> dict[str, object]:
        self._authorize(vault_name, session_id)
        sections = _selected_values("fields", fields, ("trajectory", "entries"))
        sections = {"trajectory", "entries"} if sections is None else sections
        kinds = _selected_values(
            "entry_kinds",
            entry_kinds,
            tuple(kind.value for kind in SessionMapEntryKind),
        )
        if kinds is not None and "entries" not in sections:
            raise SessionMapRequestError(
                "entry_kinds requires entries in fields. Include entries or omit entry_kinds."
            )
        if checkpoint_id and checkpoint_ids is not None:
            raise SessionMapRequestError(
                "Supply either checkpoint_id or checkpoint_ids, not both."
            )
        if checkpoint_ids is not None and (
            not checkpoint_ids or any(not value.strip() for value in checkpoint_ids)
        ):
            raise SessionMapRequestError(
                "checkpoint_ids must be a nonempty list of checkpoint IDs; omit it for the latest map."
            )
        requested = [checkpoint_id.strip()] if checkpoint_id else checkpoint_ids
        if requested is not None and len(set(requested)) != len(requested):
            raise SessionMapRequestError(
                "checkpoint_ids contains duplicate IDs. Supply each checkpoint once."
            )
        limits = SessionMapRetrievalLimits.configured()
        maximum = (
            limits.entries_max_maps
            if "entries" in sections
            else limits.trajectory_max_maps
        )
        if requested is not None and len(requested) > maximum:
            raise SessionMapRequestError(
                f"get_session_map allows at most {maximum} checkpoint_ids for the selected fields. "
                "Request fewer maps per call or adjust the installation limit."
            )
        latest = self._store.get_latest_context_checkpoint(session_id, vault_name)
        if requested is None:
            requested = (
                [latest.checkpoint_id]
                if latest and latest.checkpoint_kind == "session_map"
                else []
            )
        bound, _ = self._store.session_map_history_bounds(session_id, vault_name)
        rows = self._store.list_session_map_revisions(
            session_id,
            vault_name,
            through_id=bound,
            limit=maximum,
            checkpoint_ids=requested,
        )
        if len(rows) != len(requested):
            raise SessionMapRequestError(
                "One or more requested checkpoints are unavailable in this session. "
                "Use list_session_maps to obtain eligible checkpoint IDs; no maps were returned."
            )
        maps = []
        for revision, checkpoint in rows:
            draft = load_session_map_checkpoint(checkpoint)
            selected: dict[str, object] = {"schema_version": draft.schema_version}
            if "trajectory" in sections:
                selected["trajectory"] = (
                    draft.trajectory.model_dump(mode="json")
                    if draft.trajectory
                    else None
                )
            if "entries" in sections:
                selected["entries"] = [
                    entry.model_dump(mode="json")
                    for entry in draft.entries
                    if kinds is None or entry.kind.value in kinds
                ]
            maps.append(
                {
                    **self._metadata(
                        checkpoint, revision, latest.checkpoint_id if latest else None
                    ),
                    "map": selected,
                }
            )
        return {
            "session_maps": maps,
            "order": "oldest_first",
            "returned_count": len(maps),
            "max_maps": maximum,
        }
