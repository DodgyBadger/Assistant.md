"""Advanced-shell stdio creation, authority, import and readiness contracts."""

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.mcp_stdio_inputs import (
    IMPORT,
    connection_request,
    refused_loopback_port,
)

pytestmark = pytest.mark.scenario


async def test_stdio_restricted_authority_import_and_readiness(
    app: Application,
) -> None:
    app.vault("MCPStdioVault")
    await app.start()
    from core.advanced_shell.config import AdvancedShellConfig, ExecutionMode
    from core.advanced_shell.preflight import (
        AdvancedShellPreflightService,
        AdvancedShellReadiness,
    )
    from core.identity import LOCAL_USER_AUTHORITY, ExecutionAuthority
    from core.mcp import MCPConnectionManager, MCPConnectionService
    from core.secrets import get_encrypted_secrets_service
    from core.tools.advanced_shell import ShellTransportConfig

    parsed = app.request(
        "/api/system/mcp/connections/import/parse",
        method="POST",
        data={"configuration": IMPORT},
    )
    assert parsed.status_code == 200, parsed.text
    imported = parsed.json()
    assert (
        imported["display_name"],
        imported["transport"],
        imported["stdio"]["executable"],
    ) == (
        "Filesystem",
        "advanced_shell_stdio",
        "/home/advanced-shell/.local/bin/mcp-server-filesystem",
    )
    restricted = app.request(
        "/api/system/mcp/connections", method="POST", data=imported
    )
    assert (
        restricted.status_code == 400 and "advanced execution mode" in restricted.text
    )
    request = connection_request()
    service = MCPConnectionService(
        system_root=str(app.root / "system"),
        secrets=get_encrypted_secrets_service(),
        advanced_shell_stdio_enabled=True,
    )
    with pytest.raises(PermissionError, match="unavailable for this principal"):
        service.create_connection_for_authority(
            ExecutionAuthority("stdio-owner"), request
        )
    connection = service.create_connection_for_authority(LOCAL_USER_AUTHORITY, request)
    assert (connection.url, connection.auth_mode.value, connection.stdio) == (
        None,
        "none",
        request.stdio,
    )
    database = (app.root / "system/access.db").read_bytes()
    assert b"LOG_LEVEL" in database and b"warn" in database
    listing = app.request("/api/system/mcp/connections")
    assert listing.status_code == 200
    info = next(
        row
        for row in listing.json()
        if row["connection_id"] == connection.connection_id
    )
    assert info["url"] is None and info["transport"] == "advanced_shell_stdio"
    assert info["stdio"]["environment"] == {"LOG_LEVEL": "warn"}
    assert info["stdio"]["roots"] == ["/workspace"]
    with refused_loopback_port() as port:
        config = AdvancedShellConfig(
            execution_mode=ExecutionMode.ADVANCED,
            host="127.0.0.1",
            port=port,
            user="advanced-shell",
            host_key_alias=None,
        )
        paths = config.state_paths(app.root / "system")
        paths.directory.mkdir(parents=True, exist_ok=True)
        paths.client_identity.write_text("validation identity", encoding="utf-8")
        paths.known_hosts.write_text("validation host trust", encoding="utf-8")
        preflight = AdvancedShellPreflightService(config, app.root / "system")
        snapshot = await preflight.status()
        assert snapshot.state == AdvancedShellReadiness.CONNECTION_FAILURE
        manager = MCPConnectionManager(
            connections=service,
            advanced_shell_stdio=ShellTransportConfig.from_infrastructure(
                config, app.root / "system"
            ),
            advanced_shell_readiness=preflight.status,
        )
        try:
            result = await manager.test_connection(LOCAL_USER_AUTHORITY, connection)
            assert (result.status, result.ready) == (
                "advanced_shell_unavailable",
                False,
            )
            assert "127.0.0.1" not in result.message
            assert "validation identity" not in result.message
        finally:
            await manager.shutdown()
