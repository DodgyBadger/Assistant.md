"""
Job-specific database operations for APScheduler SQLAlchemy job store.

Simple utilities for setting up the SQLAlchemy job store.
"""

import os

from core.database import get_system_database_definition, get_system_database_path
from core.scheduling.recovery import RecoveringJobStore


def create_job_store(
    system_root: str | None = None,
    wipe: bool = False,
) -> RecoveringJobStore:
    """Create APScheduler SQLAlchemy job store for persistent job storage.

    Args:
        system_root: Optional override for system data directory
        wipe: If True, delete the existing job store file before creating a new one
    Returns:
        Configured SQLAlchemyJobStore instance
    """
    get_system_database_definition("scheduler_jobs")
    database_path = get_system_database_path("scheduler_jobs", system_root)
    if wipe and os.path.exists(database_path):
        os.remove(database_path)
    database_url = f"sqlite:///{database_path}"

    return RecoveringJobStore(url=database_url)
