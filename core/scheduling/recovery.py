"""Recover scheduler polling and settle post-dispatch writes before dispatch resumes."""

from __future__ import annotations

import errno
import sqlite3
from datetime import UTC, datetime
from typing import Any, cast

from apscheduler.job import Job
from apscheduler.jobstores.base import JobLookupError
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.schedulers.base import STATE_RUNNING
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from core.logger import UnifiedLogger

logger = UnifiedLogger("scheduler")


class RecoveringJobStore(SQLAlchemyJobStore):
    """Remember one failed dispatch write until storage permits settlement.

    The scheduler stops its round on the first write failure. That leaves at
    most one update/removal to settle, and no further due-job reads may occur
    before it settles. This is process-local recovery, not a durable outbox.
    """

    def __init__(self, **options: Any) -> None:
        super().__init__(**options)
        self.processing = False
        self.read_error: SQLAlchemyError | None = None
        self._pending_update: Job | None = None
        self._pending_remove: str | None = None

    @property
    def pending_job_id(self) -> str | None:
        return self._pending_update.id if self._pending_update else self._pending_remove

    def settle_pending(self) -> None:
        try:
            if self._pending_update is not None:
                super().update_job(self._pending_update)
            elif self._pending_remove is not None:
                super().remove_job(self._pending_remove)
        except JobLookupError:
            # A different process may already have removed the job. Never
            # recreate it merely to settle a previously dispatched occurrence.
            pass
        self._pending_update = None
        self._pending_remove = None

    def get_due_jobs(self, now: datetime) -> list[Job]:
        try:
            self.settle_pending()
            return cast(list[Job], super().get_due_jobs(now))
        except SQLAlchemyError as exc:
            self.read_error = exc
            raise

    def update_job(self, job: Job) -> None:
        if not self.processing:
            self.settle_pending()
        try:
            super().update_job(job)
        except SQLAlchemyError:
            if self.processing:
                self._pending_update = job
            raise

    def remove_job(self, job_id: str) -> None:
        if not self.processing:
            self.settle_pending()
        try:
            super().remove_job(job_id)
        except SQLAlchemyError:
            if self.processing:
                self._pending_remove = job_id
            raise

    def add_job(self, job: Job) -> None:
        self.settle_pending()
        super().add_job(job)

    def remove_all_jobs(self) -> None:
        self.settle_pending()
        super().remove_all_jobs()


class RecoveringScheduler(AsyncIOScheduler):
    """Keep the async wakeup timer alive across temporary storage failures."""

    polling_healthy = True
    polling_retry_count = 0
    _failure_started_at: datetime | None = None
    _failure_job_id: str | None = None

    def _process_jobs(self) -> float | None:
        if self.state != STATE_RUNNING:
            return cast(float | None, super()._process_jobs())
        with self._jobstores_lock:
            stores = [
                store
                for store in self._jobstores.values()
                if isinstance(store, RecoveringJobStore)
            ]
            try:
                for store in stores:
                    store.settle_pending()
                    store.read_error = None
                    store.processing = True
                delay = cast(float | None, super()._process_jobs())
                for store in stores:
                    if store.read_error is not None:
                        raise store.read_error
            except (SQLAlchemyError, OSError) as exc:
                self.polling_retry_count += 1
                if self.polling_healthy:
                    self.polling_healthy = False
                    self._failure_started_at = datetime.now(UTC)
                    self._failure_job_id = next(
                        (
                            store.pending_job_id
                            for store in stores
                            if store.pending_job_id
                        ),
                        None,
                    )
                    logger.add_sink("validation").error(
                        "Scheduler storage unavailable; polling will retry",
                        data={
                            "event": "scheduler_polling_failed",
                            "status": "retrying",
                            "job_id": self._failure_job_id,
                            "error_type": type(exc).__name__,
                            "error": _storage_error_summary(exc),
                            "retry_interval_seconds": self.jobstore_retry_interval,
                        },
                    )
                return cast(float, self.jobstore_retry_interval)
            finally:
                for store in stores:
                    store.processing = False
            if not self.polling_healthy:
                retry_count = self.polling_retry_count
                self.polling_healthy = True
                self.polling_retry_count = 0
                logger.add_sink("validation").info(
                    "Scheduler polling recovered",
                    data={
                        "event": "scheduler_polling_recovered",
                        "status": "recovered",
                        "job_id": self._failure_job_id,
                        "retry_count": retry_count,
                        "failed_at": (
                            self._failure_started_at.isoformat()
                            if self._failure_started_at
                            else None
                        ),
                    },
                )
                self._failure_started_at = None
                self._failure_job_id = None
            return delay


def _storage_error_summary(error: SQLAlchemyError | OSError) -> str:
    """Classify storage errors without retaining SQL parameters or job state."""
    underlying = error.orig if isinstance(error, DBAPIError) else error
    code = getattr(underlying, "sqlite_errorcode", 0) or 0
    if (code & 255) == sqlite3.SQLITE_FULL or getattr(
        underlying, "errno", None
    ) == errno.ENOSPC:
        return "Scheduler storage is full"
    if (code & 255) == sqlite3.SQLITE_READONLY:
        return "Scheduler storage is read-only"
    if (code & 255) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
        return "Scheduler storage is locked"
    return "Scheduler job-store access failed"
