"""Locked access storage stays untouched throughout diagnostic runtime startup."""

from __future__ import annotations

import asyncio
import base64
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

_direct_run_root: tempfile.TemporaryDirectory[str] | None = None
if __name__ == "__main__":
    from core.runtime.paths import set_bootstrap_roots

    _direct_run_root = tempfile.TemporaryDirectory(prefix="assistantmd-locked-access-")
    direct_root = Path(_direct_run_root.name)
    set_bootstrap_roots(direct_root / "data", direct_root / "system")

from api.exceptions import APIException  # noqa: E402
from api.models import GoogleConnectionCreateRequest  # noqa: E402
from api.services.google_connections import (  # noqa: E402
    create_google_connection,
    list_google_connections,
)
from api.services.maintenance import (  # noqa: E402
    _build_system_migration_status_response,
)
from core.connections import (  # noqa: E402
    BuiltInConnectionService,
    GmailPreferences,
    GoogleConnection,
    GoogleConnectionCreate,
    GoogleConnectionUpdate,
)
from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority  # noqa: E402
from core.runtime.bootstrap import bootstrap_runtime  # noqa: E402
from core.runtime.config import RuntimeConfig  # noqa: E402
from core.runtime.paths import (  # noqa: E402
    get_data_root,
    get_system_root,
    set_bootstrap_roots,
)
from core.secrets import (  # noqa: E402
    EncryptedSecretsService,
    SecretIntegrityError,
    SecretKeyring,
    get_secrets_bootstrap_status,
    reset_secrets_bootstrap_status,
)
from core.system_migrations import get_system_migration_status  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class LockedAccessBootstrapScenario(BaseScenario):
    """Exercise real bootstrap/services with unavailable or damaged credentials."""

    async def test_scenario(self) -> None:
        original_data, original_system = get_data_root(), get_system_root()
        try:
            for kind in (
                "missing-key",
                "wrong-key",
                "corrupt-file",
                "malformed-schema",
            ):
                await self._assert_locked_startup(kind)
            ready_root = self.run_path / "ready-schema"
            BuiltInConnectionService(system_root=str(ready_root))
            ready = BuiltInConnectionService(
                system_root=str(ready_root), initialize_schema=False
            )
            self.soft_assert_equal(
                ready.list_google_connections_for_authority(LOCAL_USER_AUTHORITY),
                [],
                "Skipping schema initialization alone must not disable a ready service",
            )
        finally:
            reset_secrets_bootstrap_status()
            set_bootstrap_roots(original_data, original_system)
        self.assert_no_failures()
        self.teardown_scenario()

    async def _assert_locked_startup(self, kind: str) -> None:
        root = self.run_path / kind
        config = RuntimeConfig.for_validation(root, root / "data")
        path = config.system_root / "access.db"
        if kind == "wrong-key":
            service = EncryptedSecretsService(
                system_root=str(config.system_root),
                keyring=SecretKeyring(keys={1: bytes(range(32))}, active_version=1),
            )
            service.set_for_authority(
                LOCAL_USER_AUTHORITY,
                "configuration",
                "OPENAI_API_KEY",
                "fixture-secret",
            )
        elif kind == "corrupt-file":
            path.write_bytes(b"not a sqlite database")
        elif kind == "malformed-schema":
            with sqlite3.connect(path) as conn:
                conn.execute("CREATE TABLE encrypted_secrets (unrelated TEXT)")
        before = path.read_bytes() if path.exists() else None
        env = {
            "ASSISTANTMD_SECRETS_KEY": (
                ""
                if kind == "missing-key"
                else base64.urlsafe_b64encode(b"x" * 32).decode()
            ),
            "ASSISTANTMD_SECRETS_KEYS": "",
            "ASSISTANTMD_SECRETS_ACTIVE_KEY_VERSION": "",
        }
        runtime = None
        with (
            patch.dict("os.environ", env),
            patch(
                "core.runtime.bootstrap.validate_settings",
                return_value=SimpleNamespace(is_healthy=True),
            ),
            patch(
                "core.runtime.context.RuntimeContext.reload_workflows",
                new_callable=AsyncMock,
            ),
            patch(
                "core.runtime.context.RuntimeContext.start_background_vault_state_refresh"
            ),
        ):
            try:
                runtime = await bootstrap_runtime(config)
                status = get_secrets_bootstrap_status()
                self.soft_assert(
                    status is not None and not status.ready, f"{kind} must start locked"
                )
                self.soft_assert(
                    runtime.google_connection is None
                    and runtime.mcp_connections is None,
                    f"{kind} must not enable credential services",
                )
                response = _build_system_migration_status_response(
                    get_system_migration_status(config.system_root)
                )
                access = next(
                    target for target in response.targets if target.db_name == "access"
                )
                self.soft_assert(
                    bool(access.inspection_error),
                    f"{kind} diagnostics must explain unavailable migration inspection",
                )
                with use_execution_authority(LOCAL_USER_AUTHORITY):
                    self._assert_locked_object(runtime.built_in_connections, kind)
                    for action in (
                        list_google_connections,
                        lambda: create_google_connection(
                            GoogleConnectionCreateRequest(
                                display_name="Blocked", client_id="blocked-client"
                            )
                        ),
                    ):
                        try:
                            action()
                        except APIException:
                            pass
                        else:
                            self.soft_assert(
                                False,
                                f"{kind} must reject Google API access before touching storage",
                            )
            finally:
                if runtime is not None:
                    await runtime.shutdown()
                reset_secrets_bootstrap_status()
        after = path.read_bytes() if path.exists() else None
        self.soft_assert_equal(
            after, before, f"{kind} startup/API must preserve locked storage exactly"
        )
        self.soft_assert(
            not (config.system_root / "migration_backups").exists(),
            f"{kind} must not back up/migrate locked access state",
        )

    def _assert_locked_object(
        self, service: BuiltInConnectionService, kind: str
    ) -> None:
        authority = LOCAL_USER_AUTHORITY
        request = GoogleConnectionCreate(display_name="Blocked", client_id="blocked")
        existing = GoogleConnection(
            connection_id="blocked",
            slug="blocked",
            display_name="Blocked",
            client_id="blocked",
            is_default=True,
            gmail=GmailPreferences(),
            config_version=1,
            oauth_generation=1,
            created_at="",
            updated_at="",
        )
        # An empty unrelated connection also proves transaction-bound helpers
        # reject before executing SQL, rather than failing on missing tables.
        with closing(sqlite3.connect(":memory:")) as conn:
            actions = (
                service.list_google_connections,
                lambda: service.list_google_connections_for_authority(authority),
                service.get_google_connection,
                lambda: service.get_google_connection_for_authority(authority),
                lambda: service.get_google_connection_by_slug_for_authority(
                    authority, "blocked"
                ),
                lambda: service.create_google_connection(request),
                lambda: service.create_google_connection_for_authority(
                    authority, request
                ),
                lambda: service.require_google_connection_on_connection(
                    conn, authority, "blocked"
                ),
                lambda: service.update_google_connection_on_connection(
                    conn,
                    authority,
                    existing,
                    GoogleConnectionUpdate(client_id="changed"),
                ),
            )
            for action in actions:
                try:
                    action()
                except SecretIntegrityError:
                    pass
                else:
                    self.soft_assert(
                        False,
                        f"{kind} direct metadata entrypoints must reject while locked",
                    )


if __name__ == "__main__":
    asyncio.run(LockedAccessBootstrapScenario().test_scenario())
