"""Atomic access-state mutation contracts for MCP connections."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

_direct_run_root: tempfile.TemporaryDirectory[str] | None = None
if __name__ == "__main__":
    from core.runtime.paths import set_bootstrap_roots

    _direct_run_root = tempfile.TemporaryDirectory(prefix="assistantmd-access-atomic-")
    direct_root = Path(_direct_run_root.name)
    (direct_root / "data").mkdir()
    (direct_root / "system").mkdir()
    set_bootstrap_roots(direct_root / "data", direct_root / "system")

import api.services.mcp as mcp_api  # noqa: E402
import core.mcp.service as service_module  # noqa: E402
from api.exceptions import APIException  # noqa: E402
from api.models import MCPCredentialUpdateRequest  # noqa: E402
from api.utils import create_error_response  # noqa: E402
from core.access_store import write_transaction  # noqa: E402
from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority  # noqa: E402
from core.mcp import (  # noqa: E402
    MCPAuthMode,
    MCPConnectionCreate,
    MCPConnectionService,
    MCPConnectionUpdate,
    MCPTransport,
)
from core.runtime.paths import set_bootstrap_roots  # noqa: E402
from core.secrets import SecretGuardMismatchError  # noqa: E402
from core.secrets.crypto import SecretKeyring  # noqa: E402
from core.secrets.service import EncryptedSecretsService  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class MCPMutationRecoveryScenario(BaseScenario):
    """Prove metadata and ciphertext commit or roll back together."""

    async def test_scenario(self) -> None:
        system_root = self.run_path / "system"
        data_root = self.run_path / "data"
        system_root.mkdir(parents=True)
        data_root.mkdir(parents=True)
        set_bootstrap_roots(data_root, system_root)
        os.environ["ASSISTANTMD_SECRETS_KEY"] = base64.urlsafe_b64encode(
            b"a" * 32
        ).decode()
        secrets = EncryptedSecretsService(
            system_root=str(system_root), keyring=SecretKeyring.from_environment()
        )
        changed: list[str] = []
        service = MCPConnectionService(
            system_root=str(system_root),
            secrets=secrets,
            on_change=lambda _principal, connection_id: changed.append(connection_id),
        )
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            connection = service.create_connection(
                MCPConnectionCreate(
                    display_name="Atomic MCP",
                    url="https://mcp.example.test",
                    transport=MCPTransport.STREAMABLE_HTTP,
                    auth_mode=MCPAuthMode.BEARER,
                    credential="first-secret",
                )
            )
            with sqlite3.connect(system_root / "access.db") as conn:
                conn.execute(
                    """CREATE TRIGGER reject_metadata_update BEFORE UPDATE ON mcp_connections
                    BEGIN SELECT RAISE(ABORT, 'injected metadata failure'); END"""
                )
            try:
                service.set_credential(connection.connection_id, "second-secret")
            except sqlite3.IntegrityError:
                pass
            else:
                self.soft_assert(
                    False, "Failure after the encrypted write should propagate"
                )
            current, credential = service.get_connection_test_material(
                connection.connection_id
            )
            self.soft_assert_equal(
                (current.config_version, credential),
                (connection.config_version, "first-secret"),
                "Failed composed mutation must roll back metadata and ciphertext",
            )
        with sqlite3.connect(system_root / "access.db") as conn:
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.soft_assert(
            "mcp_connection_mutations" not in tables,
            "Final access schema must not retain the cross-database saga",
        )
        self.soft_assert_equal(
            changed,
            [connection.connection_id],
            "Runtime invalidation must run only after committed mutations",
        )
        with sqlite3.connect(system_root / "access.db") as conn:
            conn.execute("DROP TRIGGER reject_metadata_update")
            conn.execute(
                """CREATE TRIGGER reject_credential_delete BEFORE DELETE ON encrypted_secrets
                WHEN OLD.name = 'credential' BEGIN
                SELECT RAISE(ABORT, 'injected deletion failure'); END"""
            )
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            try:
                service.update_connection(
                    connection.connection_id,
                    MCPConnectionUpdate(
                        display_name="Should roll back",
                        url=connection.url,
                        transport=connection.transport,
                        auth_mode=MCPAuthMode.NONE,
                        header_name=None,
                        enabled=True,
                        allow_private_http=False,
                        allowed_tools=None,
                    ),
                )
            except sqlite3.IntegrityError:
                pass
            else:
                self.soft_assert(False, "Failure after metadata write must propagate")
            restored, credential = service.get_connection_test_material(
                connection.connection_id
            )
            self.soft_assert_equal(
                (restored.display_name, restored.config_version, credential),
                (connection.display_name, connection.config_version, "first-secret"),
                "Metadata written before encrypted deletion must roll back on failure",
            )
        with sqlite3.connect(system_root / "access.db") as conn:
            conn.execute("DROP TRIGGER reject_credential_delete")
        await self._assert_process_exit(
            system_root, data_root, connection.connection_id
        )
        await self._assert_authoritative_mutations(service)
        self._assert_committed_failure(system_root, secrets, connection.connection_id)
        self.assert_no_failures()

    def _assert_committed_failure(self, system_root, secrets, connection_id) -> None:
        observed = []

        def reject_notification(_principal, target):
            # A separate writer can enter here only after the mutation released
            # its lock; committed state is already independently visible.
            with write_transaction(str(system_root)) as conn:
                row = conn.execute(
                    "SELECT config_version FROM mcp_connections WHERE connection_id=?",
                    (target,),
                ).fetchone()
                observed.append(row[0])
            raise RuntimeError("fixture notification failure")

        service = MCPConnectionService(
            system_root=str(system_root), secrets=secrets, on_change=reject_notification
        )
        with (
            use_execution_authority(LOCAL_USER_AUTHORITY),
            patch.object(service_module.logger, "info") as info,
            patch.object(service_module.logger, "error") as error,
            patch.object(mcp_api, "_service", return_value=service),
        ):
            try:
                mcp_api.set_mcp_credential(
                    connection_id,
                    MCPCredentialUpdateRequest(credential="saved-despite-notification"),
                )
            except APIException as exc:
                self.soft_assert_equal(
                    (exc.status_code, exc.details),
                    (503, {"committed": True, "retry_safe": False}),
                    "API must identify a committed change and prevent blind retry",
                )
                self.soft_assert(
                    "saved" in str(exc.detail), "API must explain the durable outcome"
                )
                rendered = json.loads(create_error_response(exc).body)
                self.soft_assert_equal(
                    rendered["details"]["retryable"],
                    False,
                    "Rendered committed mutation errors must prohibit automatic retry",
                )
                self.soft_assert(
                    "committed" in rendered["details"]["suggested_action"],
                    "Rendered guidance must preserve the committed outcome",
                )
            else:
                self.soft_assert(
                    False, "Notification failure must not report normal success"
                )
            current, credential = service.get_connection_test_material(connection_id)
        self.soft_assert_equal(
            (current.config_version, credential, observed),
            (3, "saved-despite-notification", [3]),
            "Post-commit notification failure must preserve committed data without replay",
        )
        events = [call.kwargs["data"] for call in info.call_args_list]
        self.soft_assert_equal(
            [item["event"] for item in events],
            ["mcp_connection_mutation_started"],
            "Completion event must wait for acknowledged invalidation",
        )
        failure = error.call_args.kwargs["data"]
        self.soft_assert_equal(
            (failure["event"], failure["committed"], failure["phase"]),
            ("connection_mutation_failed", True, "runtime_invalidation"),
            "Failure diagnostics must distinguish committed changes from rollback",
        )
        self.soft_assert(
            "saved-despite-notification" not in repr(events + [failure]),
            "Events must not expose credentials",
        )

    async def _assert_process_exit(
        self, system_root: Path, data_root: Path, connection_id: str
    ) -> None:
        script = """
import os, sys
from pathlib import Path
from contextlib import contextmanager
from core.runtime.paths import set_bootstrap_roots
set_bootstrap_roots(Path(sys.argv[1]), Path(sys.argv[2]))
from core.access_store import write_transaction
from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
from core.secrets import EncryptedSecretsService, SecretKeyring
import core.mcp.service as module
secrets = EncryptedSecretsService(system_root=sys.argv[2], keyring=SecretKeyring.from_environment())
@contextmanager
def interrupted(root):
    with write_transaction(root) as conn:
        yield conn
        os._exit(73)
service = module.MCPConnectionService(system_root=sys.argv[2], secrets=secrets)
if sys.argv[4] == 'before':
    module.write_transaction = interrupted
with use_execution_authority(LOCAL_USER_AUTHORITY):
    service.set_credential(sys.argv[3], 'committed-secret')
os._exit(74)
"""
        for phase, expected_code, expected_version, expected_secret in (
            ("before", 73, 1, "first-secret"),
            ("after", 74, 2, "committed-secret"),
        ):
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                script,
                str(data_root),
                str(system_root),
                connection_id,
                phase,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
            except TimeoutError:
                process.kill()
                await process.communicate()
                raise
            self.soft_assert_equal(
                process.returncode,
                expected_code,
                f"Child must exit {phase} commit: {stderr.decode()}",
            )
            reopened_secrets = EncryptedSecretsService(
                system_root=str(system_root), keyring=SecretKeyring.from_environment()
            )
            reopened = MCPConnectionService(
                system_root=str(system_root), secrets=reopened_secrets
            )
            with use_execution_authority(LOCAL_USER_AUTHORITY):
                current, credential = reopened.get_connection_test_material(
                    connection_id
                )
            self.soft_assert_equal(
                (current.config_version, credential),
                (expected_version, expected_secret),
                f"Process exit {phase} commit must preserve one complete durable state",
            )

    async def _assert_authoritative_mutations(
        self, service: MCPConnectionService
    ) -> None:
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            connection = service.create_connection(
                MCPConnectionCreate(
                    display_name="OAuth race",
                    url="https://oauth.example.test/mcp",
                    transport=MCPTransport.STREAMABLE_HTTP,
                    auth_mode=MCPAuthMode.OAUTH,
                    oauth_client_id="client-a",
                )
            )
            request = MCPConnectionUpdate(
                display_name="OAuth race",
                url=connection.url,
                transport=connection.transport,
                header_name=None,
                enabled=True,
                allow_private_http=False,
                allowed_tools=None,
                auth_mode=MCPAuthMode.OAUTH,
                oauth_client_id="client-a",
            )
            stale_storage = []

            @contextmanager
            def interleaved_transaction(root):
                with patch.object(
                    service_module, "write_transaction", write_transaction
                ):
                    service.update_connection(
                        connection.connection_id,
                        MCPConnectionUpdate(
                            display_name="OAuth race",
                            url=connection.url,
                            transport=connection.transport,
                            header_name=None,
                            enabled=True,
                            allow_private_http=False,
                            allowed_tools=None,
                            auth_mode=MCPAuthMode.OAUTH,
                            oauth_client_id="client-b",
                        ),
                    )
                    storage = service.oauth_storage(
                        LOCAL_USER_AUTHORITY, connection.connection_id
                    )
                    storage.put_sync("grant", {"access_token": "client-b-token"})
                    stale_storage.append(storage)
                with write_transaction(root) as conn:
                    yield conn

            with patch.object(
                service_module, "write_transaction", interleaved_transaction
            ):
                service.update_connection(connection.connection_id, request)
            try:
                stale_storage[0].put_sync("grant", {"access_token": "late-client-b"})
            except SecretGuardMismatchError:
                pass
            else:
                self.soft_assert(
                    False, "The superseded client-b adapter must be fenced"
                )
            current_storage = service.oauth_storage(
                LOCAL_USER_AUTHORITY, connection.connection_id
            )
            self.soft_assert_equal(
                current_storage.get_sync("grant"),
                None,
                "Returning to client-a must remove the client-b grant",
            )
            for resolver in (
                service.resolve_credential,
                service.resolve_oauth_client_secret,
                service.oauth_storage,
            ):
                try:
                    resolver(
                        LOCAL_USER_AUTHORITY,
                        connection.connection_id,
                        expected_connection=connection,
                    )
                except ValueError:
                    pass
                else:
                    self.soft_assert(
                        False,
                        "A stale endpoint snapshot must not resolve current credentials or OAuth authority",
                    )

            @contextmanager
            def changed_auth_transaction(root):
                with patch.object(
                    service_module, "write_transaction", write_transaction
                ):
                    service.update_connection(
                        connection.connection_id,
                        MCPConnectionUpdate(
                            display_name="OAuth race",
                            url=connection.url,
                            transport=connection.transport,
                            header_name=None,
                            enabled=True,
                            allow_private_http=False,
                            allowed_tools=None,
                            auth_mode=MCPAuthMode.NONE,
                        ),
                    )
                with write_transaction(root) as conn:
                    yield conn

            with patch.object(
                service_module, "write_transaction", changed_auth_transaction
            ):
                try:
                    service.set_oauth_client_secret(
                        connection.connection_id, "late-secret"
                    )
                except ValueError:
                    pass
                else:
                    self.soft_assert(
                        False, "Credential admission must use locked current metadata"
                    )
            self.soft_assert_equal(
                service.resolve_oauth_client_secret(
                    LOCAL_USER_AUTHORITY, connection.connection_id
                ),
                None,
                "No OAuth client secret may be stored after auth mode changes",
            )


if __name__ == "__main__":
    asyncio.run(MCPMutationRecoveryScenario().test_scenario())
