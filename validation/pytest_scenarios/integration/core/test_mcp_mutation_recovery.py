"""Real MCP access transactions roll back atomically and report post-commit failures."""

from __future__ import annotations

import json
import sqlite3

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.mcp_mutation_inputs import (
    FailingInvalidation,
    exit_during_mutation,
    reject_access_write,
)

pytestmark = pytest.mark.scenario


async def test_mcp_metadata_and_ciphertext_share_atomic_rollback(
    app: Application,
) -> None:
    await app.start()
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.mcp import (
        MCPAuthMode,
        MCPConnectionCreate,
        MCPConnectionService,
        MCPConnectionUpdate,
    )
    from core.secrets import get_encrypted_secrets_service

    app.evidence.register_secret("first-secret", "second-secret")
    changed = []
    service = MCPConnectionService(
        system_root=str(app.root / "system"),
        secrets=get_encrypted_secrets_service(),
        on_change=lambda _principal, connection_id: changed.append(connection_id),
    )
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        connection = service.create_connection(
            MCPConnectionCreate(
                display_name="Atomic MCP",
                url="https://mcp.example.test",
                auth_mode=MCPAuthMode.BEARER,
                credential="first-secret",
            )
        )
        with reject_access_write(app):
            with pytest.raises(sqlite3.IntegrityError):
                service.set_credential(connection.connection_id, "second-secret")
        current, credential = service.get_connection_test_material(
            connection.connection_id
        )
        assert (current.config_version, credential) == (
            connection.config_version,
            "first-secret",
        )
        assert changed == [connection.connection_id]
        with reject_access_write(app, deletion=True):
            with pytest.raises(sqlite3.IntegrityError):
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
        restored, credential = service.get_connection_test_material(
            connection.connection_id
        )
        assert (restored.display_name, restored.config_version, credential) == (
            connection.display_name,
            connection.config_version,
            "first-secret",
        )
    with sqlite3.connect(app.root / "system/access.db") as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "mcp_connection_mutations" not in tables
    assert changed == [connection.connection_id]


async def test_mcp_oauth_adapters_and_stale_snapshots_are_fenced(
    app: Application,
) -> None:
    await app.start()
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.mcp import MCPAuthMode, MCPConnectionCreate, MCPConnectionUpdate
    from core.runtime.state import get_runtime_context
    from core.secrets import SecretGuardMismatchError

    app.evidence.register_secret("client-b-token", "late-client-b", "late-secret")
    service = get_runtime_context().mcp_connections
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        original = service.create_connection(
            MCPConnectionCreate(
                display_name="OAuth race",
                url="https://oauth.example.test/mcp",
                auth_mode=MCPAuthMode.OAUTH,
                oauth_client_id="client-a",
            )
        )
        # These actual sequential updates establish the stale-adapter input.
        # They do not reproduce the source's patched interleaving before lock.
        for client_id in ("client-b", "client-a"):
            service.update_connection(
                original.connection_id,
                MCPConnectionUpdate(
                    display_name="OAuth race",
                    url=original.url,
                    transport=original.transport,
                    header_name=None,
                    enabled=True,
                    allow_private_http=False,
                    allowed_tools=None,
                    auth_mode=MCPAuthMode.OAUTH,
                    oauth_client_id=client_id,
                ),
            )
            if client_id == "client-b":
                stale = service.oauth_storage(
                    LOCAL_USER_AUTHORITY, original.connection_id
                )
                stale.put_sync("grant", {"access_token": "client-b-token"})
        with pytest.raises(SecretGuardMismatchError):
            stale.put_sync("grant", {"access_token": "late-client-b"})
        assert (
            service.oauth_storage(
                LOCAL_USER_AUTHORITY, original.connection_id
            ).get_sync("grant")
            is None
        )
        for resolver in (
            service.resolve_credential,
            service.resolve_oauth_client_secret,
            service.oauth_storage,
        ):
            with pytest.raises(ValueError):
                resolver(
                    LOCAL_USER_AUTHORITY,
                    original.connection_id,
                    expected_connection=original,
                )
        service.update_connection(
            original.connection_id,
            MCPConnectionUpdate(
                display_name="OAuth race",
                url=original.url,
                transport=original.transport,
                header_name=None,
                enabled=True,
                allow_private_http=False,
                allowed_tools=None,
                auth_mode=MCPAuthMode.NONE,
            ),
        )
        with pytest.raises(ValueError):
            service.set_oauth_client_secret(original.connection_id, "late-secret")
        assert (
            service.resolve_oauth_client_secret(
                LOCAL_USER_AUTHORITY, original.connection_id
            )
            is None
        )


async def test_mcp_abrupt_process_exit_preserves_one_complete_access_state(
    app: Application,
) -> None:
    await app.start()
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.mcp import MCPAuthMode, MCPConnectionCreate
    from core.runtime.state import get_runtime_context

    app.evidence.register_secret(
        "first-secret", "uncommitted-secret", "committed-secret"
    )
    service = get_runtime_context().mcp_connections
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        connection = service.create_connection(
            MCPConnectionCreate(
                display_name="Atomic process",
                url="https://mcp.example.test",
                auth_mode=MCPAuthMode.BEARER,
                credential="first-secret",
            )
        )
    for phase, code, version, expected in (
        ("before", 73, 1, "first-secret"),
        ("after", 74, 2, "committed-secret"),
    ):
        assert await exit_during_mutation(app, connection.connection_id, phase) == code
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            current, credential = service.get_connection_test_material(
                connection.connection_id
            )
        assert (current.config_version, credential) == (version, expected)


async def test_mcp_committed_invalidation_failure_is_not_retryable_and_keeps_saved_secret(
    app: Application,
) -> None:
    await app.start()
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.mcp import MCPAuthMode, MCPConnectionCreate, MCPConnectionService
    from core.runtime.state import get_runtime_context
    from core.secrets import get_encrypted_secrets_service

    app.evidence.register_secret(
        "first-secret", "committed-secret", "saved-despite-notification"
    )
    runtime = get_runtime_context()
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        connection = runtime.mcp_connections.create_connection(
            MCPConnectionCreate(
                display_name="Commit notification",
                url="https://mcp.example.test",
                auth_mode=MCPAuthMode.BEARER,
                credential="first-secret",
            )
        )
        runtime.mcp_connections.set_credential(
            connection.connection_id, "committed-secret"
        )
    callback = FailingInvalidation(app.root / "system")
    # Public constructor composition supplies a real failing domain callback;
    # the API resolves the actual runtime service without accessor patches.
    runtime.mcp_connections = MCPConnectionService(
        system_root=str(app.root / "system"),
        secrets=get_encrypted_secrets_service(),
        on_change=callback,
    )
    response = app.request(
        f"/api/system/mcp/connections/{connection.connection_id}/credential",
        method="PUT",
        data={"credential": "saved-despite-notification"},
    )
    assert response.status_code == 503, response.text
    details = response.json()["details"]
    assert details["committed"] is True and details["retry_safe"] is False
    assert details["retryable"] is False
    assert "committed" in details["suggested_action"]
    assert "saved" in response.json()["message"]
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        current, credential = runtime.mcp_connections.get_connection_test_material(
            connection.connection_id
        )
    assert (current.config_version, credential, callback.observed_versions) == (
        3,
        "saved-despite-notification",
        [3],
    )
    activity = app.request(
        "/api/system/activity-log",
        params={"limit": 200, "tag": "mcp-connection-mutations"},
    )
    assert activity.status_code == 200
    records = [entry["data"] for entry in activity.json()["entries"]]
    failure = next(
        row
        for row in records
        if row.get("event") == "connection_mutation_failed"
        and row.get("phase") == "runtime_invalidation"
    )
    assert (failure["status"], failure["committed"], failure["error_type"]) == (
        "failed",
        True,
        "RuntimeError",
    )
    lifecycle = [
        row for row in records if row.get("operation_id") == failure["operation_id"]
    ]
    assert {row["event"] for row in lifecycle} == {
        "mcp_connection_mutation_started",
        "connection_mutation_failed",
    }
    assert (
        next(
            row
            for row in lifecycle
            if row["event"] == "mcp_connection_mutation_started"
        )["status"]
        == "started"
    )
    assert failure["error"]
    assert "saved-despite-notification" not in json.dumps(lifecycle)
