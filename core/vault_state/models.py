"""Database models for vault-state manifests and change events."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base


class VaultRecord(Base):
    """Stable vault identity registry local to vault state."""

    __tablename__ = "vaults"

    vault_id: Mapped[str] = mapped_column(String, primary_key=True, nullable=False)
    current_name: Mapped[str] = mapped_column(String, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    missing_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class VaultFavorite(Base):
    """Durable principal-owned bookmarks without manifest foreign keys."""

    __tablename__ = "vault_favorites"

    principal_id: Mapped[str] = mapped_column(String, primary_key=True)
    vault_id: Mapped[str] = mapped_column(String, primary_key=True)
    path: Mapped[str] = mapped_column(String, primary_key=True)


class VaultFile(Base):
    """Current observed state for one vault file."""

    __tablename__ = "vault_files"

    vault_id: Mapped[str] = mapped_column(String, primary_key=True, nullable=False)
    path: Mapped[str] = mapped_column(String, primary_key=True, nullable=False)
    vault_name: Mapped[str] = mapped_column(String, nullable=False)
    artifact_class: Mapped[str] = mapped_column(String, nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    mtime_ns: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    change_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class VaultFileEvent(Base):
    """Monotonic change-feed event for vault artifacts."""

    __tablename__ = "vault_file_events"

    sequence: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, nullable=False
    )
    vault_id: Mapped[str] = mapped_column(String, nullable=False)
    vault_name: Mapped[str] = mapped_column(String, nullable=False)
    path: Mapped[str] = mapped_column(String, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    artifact_class: Mapped[str | None] = mapped_column(String, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class VaultActivity(Base):
    """One durable attributed unit of vault work."""

    __tablename__ = "vault_activities"

    activity_id: Mapped[str] = mapped_column(String, primary_key=True, nullable=False)
    vault_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    vault_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    scope: Mapped[str | None] = mapped_column(String, nullable=True)
    label: Mapped[str] = mapped_column(String, nullable=False)
    task_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    goal_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    step_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    rollback_status: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class VaultMutation(Base):
    """One path-level mutation linked to a durable vault activity."""

    __tablename__ = "vault_mutations"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, nullable=False
    )
    activity_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    operation_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    path: Mapped[str] = mapped_column(String, nullable=False)
    related_path: Mapped[str | None] = mapped_column(String, nullable=True)
    target_kind: Mapped[str] = mapped_column(String, nullable=False)
    operation: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    event_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    before_exists: Mapped[bool] = mapped_column(Boolean, nullable=False)
    before_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    before_snapshot_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    after_exists: Mapped[bool] = mapped_column(Boolean, nullable=False)
    after_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    after_snapshot_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snapshot_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class SnapshotSet(Base):
    """An activity- or task-scoped file snapshot capture point."""

    __tablename__ = "snapshot_sets"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, nullable=False
    )
    activity_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String, nullable=True)
    task_kind: Mapped[str | None] = mapped_column(String, nullable=True)
    task_source: Mapped[str | None] = mapped_column(String, nullable=True)
    task_scope: Mapped[str | None] = mapped_column(String, nullable=True)
    task_label: Mapped[str | None] = mapped_column(String, nullable=True)
    vault_id: Mapped[str] = mapped_column(String, nullable=False)
    vault_name: Mapped[str] = mapped_column(String, nullable=False)
    purpose: Mapped[str] = mapped_column(String, nullable=False)
    scope_kind: Mapped[str | None] = mapped_column(String, nullable=True)
    scope_id: Mapped[str | None] = mapped_column(String, nullable=True)
    snapshot_root: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rolled_back_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class FileSnapshot(Base):
    """One captured file state within a snapshot set."""

    __tablename__ = "file_snapshots"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, nullable=False
    )
    snapshot_set_id: Mapped[int] = mapped_column(Integer, nullable=False)
    activity_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String, nullable=True)
    vault_id: Mapped[str] = mapped_column(String, nullable=False)
    vault_name: Mapped[str] = mapped_column(String, nullable=False)
    path: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    exists: Mapped[bool] = mapped_column("file_exists", Boolean, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    snapshot_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
