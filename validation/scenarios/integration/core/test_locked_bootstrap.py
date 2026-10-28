"""Real diagnostic startup keeps unavailable encrypted storage untouched."""

from __future__ import annotations

import base64

import pytest

from core.connections import BuiltInConnectionService
from core.identity import LOCAL_USER_AUTHORITY
from validation.scenarios.application import Application
from validation.scenarios.support.security_migrations import (
    isolated_process,
    prepare_locked_database,
    register_synthetic_secrets,
)

pytestmark = pytest.mark.scenario


@pytest.mark.parametrize(
    "kind", ["missing-key", "wrong-key", "corrupt-file", "malformed-schema"]
)
async def test_locked_runtime_preserves_access_storage(
    app: Application, kind: str
) -> None:
    register_synthetic_secrets(app)
    app.vault("LockedRuntime")
    await app.start()
    root = app.root / kind
    system = root / "system"
    before = prepare_locked_database(system, kind)
    key = "" if kind == "missing-key" else base64.urlsafe_b64encode(b"x" * 32).decode()
    isolated_process(
        app,
        """
import asyncio, sys
from pathlib import Path
from core.runtime.paths import set_bootstrap_roots
root = Path(sys.argv[1])
# Match normal application imports before selecting the installation under test.
set_bootstrap_roots(root / 'bootstrap-data', root / 'bootstrap-system')
import core.runtime.bootstrap
import api.services.google_connections
import api.services.maintenance
from validation.scenarios.support.security_migrations import exercise_locked_startup
set_bootstrap_roots(root / 'data', root / 'system')
asyncio.run(exercise_locked_startup(root, sys.argv[2]))
""",
        str(root),
        kind,
        key=key,
    )
    access = system / "access.db"
    after = access.read_bytes() if access.exists() else None
    assert after == before
    assert not (system / "migration_backups").exists()


async def test_existing_connection_schema_remains_usable(app: Application) -> None:
    app.vault("ReadyConnectionSchema")
    await app.start()
    root = app.root / "ready-schema"
    BuiltInConnectionService(system_root=str(root))
    ready = BuiltInConnectionService(system_root=str(root), initialize_schema=False)
    assert ready.list_google_connections_for_authority(LOCAL_USER_AUTHORITY) == []
