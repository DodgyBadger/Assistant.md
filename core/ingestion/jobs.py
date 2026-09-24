"""
Persistence for ingestion jobs.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import JSON, DateTime, Integer, String, Table, Text, func
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from core.database import (
    Base,
    create_engine_from_system_db,
    create_session_factory,
    create_tables,
)
from core.ingestion.models import JobStatus
from core.ingestion.schema import ensure_ingestion_jobs_schema


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_uri: Mapped[str] = mapped_column(String, nullable=False)
    vault: Mapped[str | None] = mapped_column(String, nullable=True)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    mime_hint: Mapped[str | None] = mapped_column(String, nullable=True)
    options: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default=JobStatus.QUEUED.value
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    outputs: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    selected_strategy: Mapped[str | None] = mapped_column(String, nullable=True)
    selected_provider: Mapped[str | None] = mapped_column(String, nullable=True)
    selected_model: Mapped[str | None] = mapped_column(String, nullable=True)
    strategy_attempts: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    fallback_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


@dataclass(frozen=True)
class IngestionJobCreate:
    """Values required to create one durable ingestion job."""

    source_uri: str
    vault: str
    source_type: str
    mime_hint: str | None
    options: dict[str, Any] | None


def _get_engine() -> Engine:
    # Uses the centralized declared system DB registry.
    return create_engine_from_system_db("ingestion_jobs")


def _get_session_factory() -> sessionmaker[Session]:
    return create_session_factory(_get_engine())


def init_db() -> None:
    """Create tables if they do not exist."""
    ensure_ingestion_jobs_schema()
    engine = _get_engine()
    create_tables(engine, cast(Table, IngestionJob.__table__))


def create_job(
    source_uri: str,
    vault: str,
    source_type: str,
    mime_hint: str | None,
    options: dict[str, Any] | None,
) -> IngestionJob:
    return create_jobs(
        [
            IngestionJobCreate(
                source_uri=source_uri,
                vault=vault,
                source_type=source_type,
                mime_hint=mime_hint,
                options=options,
            )
        ]
    )[0]


def create_jobs(requests: list[IngestionJobCreate]) -> list[IngestionJob]:
    """Create a validated batch atomically in one database transaction."""
    if not requests:
        return []
    session_factory = _get_session_factory()
    try:
        with session_factory() as session:
            jobs = [
                IngestionJob(
                    source_uri=request.source_uri,
                    vault=request.vault,
                    source_type=request.source_type,
                    mime_hint=request.mime_hint,
                    options=request.options or {},
                    status=JobStatus.QUEUED.value,
                )
                for request in requests
            ]
            session.add_all(jobs)
            session.flush()
            for job in jobs:
                session.refresh(job)
            session.expunge_all()
            session.commit()
            return jobs
    except SQLAlchemyError as exc:
        raise RuntimeError(f"Failed to create ingestion job batch: {exc}") from exc


def update_job_status(job_id: int, status: JobStatus, error: str | None = None) -> None:
    session_factory = _get_session_factory()
    try:
        with session_factory() as session:
            job: IngestionJob | None = session.get(IngestionJob, job_id)
            if job is None:
                raise ValueError(f"Job {job_id} not found")

            job.status = status.value
            if error:
                job.error = error
            session.commit()
    except SQLAlchemyError as exc:
        raise RuntimeError(f"Failed to update job {job_id}: {exc}") from exc


def update_job_outputs(job_id: int, outputs: list[str]) -> None:
    session_factory = _get_session_factory()
    try:
        with session_factory() as session:
            job: IngestionJob | None = session.get(IngestionJob, job_id)
            if job is None:
                raise ValueError(f"Job {job_id} not found")

            job.outputs = outputs
            session.commit()
    except SQLAlchemyError as exc:
        raise RuntimeError(f"Failed to update outputs for job {job_id}: {exc}") from exc


def complete_job(job_id: int, outputs: list[str]) -> None:
    """Commit one job's output manifest and completed status atomically."""
    session_factory = _get_session_factory()
    try:
        with session_factory() as session:
            job: IngestionJob | None = session.get(IngestionJob, job_id)
            if job is None:
                raise ValueError(f"Job {job_id} not found")
            job.outputs = outputs
            job.status = JobStatus.COMPLETED.value
            job.error = None
            session.commit()
    except SQLAlchemyError as exc:
        raise RuntimeError(f"Failed to complete job {job_id}: {exc}") from exc


def update_job_provenance(
    job_id: int,
    *,
    selected_strategy: str | None,
    selected_provider: str | None,
    selected_model: str | None,
    strategy_attempts: list[str],
    fallback_reason: str | None,
) -> None:
    """Persist the extraction decision for one ingestion job."""
    session_factory = _get_session_factory()
    try:
        with session_factory() as session:
            job: IngestionJob | None = session.get(IngestionJob, job_id)
            if job is None:
                raise ValueError(f"Job {job_id} not found")
            job.selected_strategy = selected_strategy
            job.selected_provider = selected_provider
            job.selected_model = selected_model
            job.strategy_attempts = list(strategy_attempts)
            job.fallback_reason = fallback_reason
            session.commit()
    except SQLAlchemyError as exc:
        raise RuntimeError(
            f"Failed to update provenance for job {job_id}: {exc}"
        ) from exc


def get_job(job_id: int) -> IngestionJob | None:
    session_factory = _get_session_factory()
    with session_factory() as session:
        return cast(IngestionJob | None, session.get(IngestionJob, job_id))


def find_job_for_source(
    source_uri: str, vault: str, statuses: list[str] | None = None
) -> IngestionJob | None:
    """
    Find the most recent job for a source/vault matching optional statuses.
    """
    session_factory = _get_session_factory()
    with session_factory() as session:
        query = (
            session.query(IngestionJob)
            .filter(IngestionJob.source_uri == source_uri, IngestionJob.vault == vault)
            .order_by(IngestionJob.created_at.desc())
        )
        if statuses:
            query = query.filter(IngestionJob.status.in_(statuses))
        return cast(IngestionJob | None, query.first())


def list_jobs(
    limit: int = 50,
    *,
    vault: str | None = None,
    statuses: list[JobStatus] | None = None,
    cursor: str | None = None,
) -> list[IngestionJob]:
    session_factory = _get_session_factory()
    with session_factory() as session:
        query = session.query(IngestionJob)
        if vault:
            query = query.filter(IngestionJob.vault == vault)
        if statuses:
            query = query.filter(
                IngestionJob.status.in_([status.value for status in statuses])
            )
        if cursor:
            query = query.filter(IngestionJob.id < _decode_job_cursor(cursor))
        return cast(
            list[IngestionJob],
            query.order_by(IngestionJob.id.desc()).limit(limit).all(),
        )


def count_jobs_by_status(*, vault: str | None = None) -> dict[str, int]:
    """Count durable ingestion jobs by status for one optional vault."""
    session_factory = _get_session_factory()
    with session_factory() as session:
        query = session.query(IngestionJob.status, func.count(IngestionJob.id))
        if vault:
            query = query.filter(IngestionJob.vault == vault)
        rows = query.group_by(IngestionJob.status).all()
        return {str(status): int(count) for status, count in rows}


def encode_job_cursor(job_id: int) -> str:
    """Encode the exclusive job-id boundary used for older pages."""
    payload = json.dumps({"before_id": job_id}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_job_cursor(value: str) -> int:
    try:
        padding = "=" * (-len(value) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(value + padding))
        job_id = int(decoded["before_id"])
        if job_id < 1:
            raise ValueError
        return job_id
    except (
        binascii.Error,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError("Invalid import job cursor") from exc


def cancel_queued_job(job_id: int) -> IngestionJob:
    """Atomically mark one queued ingestion job cancelled."""
    session_factory = _get_session_factory()
    try:
        with session_factory() as session:
            updated = (
                session.query(IngestionJob)
                .filter(
                    IngestionJob.id == job_id,
                    IngestionJob.status == JobStatus.QUEUED.value,
                )
                .update(
                    {
                        IngestionJob.status: JobStatus.CANCELLED.value,
                        IngestionJob.updated_at: datetime.utcnow(),
                    },
                    synchronize_session=False,
                )
            )
            if not updated:
                job = session.get(IngestionJob, job_id)
                if job is None:
                    raise ValueError(f"Job {job_id} not found")
                raise ValueError(
                    f"Job {job_id} cannot be cancelled from status {job.status}"
                )
            session.commit()
            job = session.get(IngestionJob, job_id)
            if job is None:  # pragma: no cover - defensive
                raise RuntimeError(f"Cancelled ingestion job disappeared: {job_id}")
            return job
    except SQLAlchemyError as exc:
        raise RuntimeError(f"Failed to cancel job {job_id}: {exc}") from exc


def claim_queued_job(job_id: int) -> bool:
    """Atomically transition one queued job to processing."""
    session_factory = _get_session_factory()
    try:
        with session_factory() as session:
            updated = (
                session.query(IngestionJob)
                .filter(
                    IngestionJob.id == job_id,
                    IngestionJob.status == JobStatus.QUEUED.value,
                )
                .update(
                    {
                        IngestionJob.status: JobStatus.PROCESSING.value,
                        IngestionJob.updated_at: datetime.utcnow(),
                    },
                    synchronize_session=False,
                )
            )
            session.commit()
            return bool(updated)
    except SQLAlchemyError as exc:
        raise RuntimeError(f"Failed to claim job {job_id}: {exc}") from exc


def fail_processing_jobs(reason: str) -> list[int]:
    """Mark processing jobs interrupted by a previous runtime as failed."""
    session_factory = _get_session_factory()
    try:
        with session_factory() as session:
            jobs = (
                session.query(IngestionJob)
                .filter(IngestionJob.status == JobStatus.PROCESSING.value)
                .all()
            )
            job_ids = [job.id for job in jobs]
            for job in jobs:
                job.status = JobStatus.FAILED.value
                job.error = reason
                job.updated_at = datetime.utcnow()
            session.commit()
            return job_ids
    except SQLAlchemyError as exc:
        raise RuntimeError(f"Failed to reconcile processing jobs: {exc}") from exc


def count_jobs(*, status: JobStatus | None = None) -> int:
    """Count durable ingestion jobs, optionally limited to one status."""
    session_factory = _get_session_factory()
    with session_factory() as session:
        query = session.query(IngestionJob)
        if status is not None:
            query = query.filter(IngestionJob.status == status.value)
        return int(query.count())
