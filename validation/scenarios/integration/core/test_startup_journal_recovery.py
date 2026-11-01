"""Normal application startup recovers an interrupted database transaction."""

import hashlib
import sqlite3

import pytest

from validation.support.application import Application
from validation.support.runtime.sqlite_recovery import (
    committed_probe_values,
    interrupt_database_transaction,
)

pytestmark = pytest.mark.scenario


async def test_startup_recovers_hot_journal_without_losing_committed_data(
    app: Application,
) -> None:
    from core.system_migrations import get_system_migration_status

    root = app.root / "system"
    database = interrupt_database_transaction(root)
    journal = database.with_name(database.name + "-journal")
    before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (database, journal)]
    with pytest.raises(sqlite3.OperationalError) as error:
        get_system_migration_status(root)
    assert error.value.sqlite_errorcode == sqlite3.SQLITE_READONLY_ROLLBACK
    assert [
        hashlib.sha256(p.read_bytes()).hexdigest() for p in (database, journal)
    ] == before

    checkpoint = app.events.checkpoint()
    await app.start()
    assert app.request("/api/health").status_code == 200
    assert committed_probe_values(database) == ["committed"] * 32
    app.events.require(
        "system_database_recovery_completed",
        after=checkpoint,
        database_name="vault_state.db",
        status="completed",
    )
