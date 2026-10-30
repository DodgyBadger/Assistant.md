"""Explicit stdio connection and loopback SSH preflight inputs."""

from contextlib import contextmanager
from socket import AF_INET, SOCK_STREAM, socket

IMPORT = """name: Filesystem
transport: advanced_shell_stdio
executable: /home/advanced-shell/.local/bin/mcp-server-filesystem
working_directory: /workspace
arguments: []
environment: {}
roots: [/workspace]
allowed_tools: [list_allowed_directories]
enabled: true
"""


def connection_request():
    from core.mcp import MCPConnectionCreate, MCPStdioConfig, MCPTransport

    return MCPConnectionCreate(
        display_name="Filesystem",
        transport=MCPTransport.ADVANCED_SHELL_STDIO,
        stdio=MCPStdioConfig(
            executable="/home/advanced-shell/.local/bin/mcp-server-filesystem",
            arguments=(),
            working_directory="/workspace",
            environment=(("LOG_LEVEL", "warn"),),
            roots=("/workspace",),
        ),
    )


@contextmanager
def refused_loopback_port():
    with socket(AF_INET, SOCK_STREAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        yield reservation.getsockname()[1]


def admit_loopback_preflight(config, system_root):
    """Admit the fixture's exact real SSH probe; never bypass app execution."""
    from core.tools.advanced_shell import (
        ShellTransportConfig,
        build_fixed_ssh_command,
    )
    from validation.support.core_processes import declare_core_command

    # The reserved local port cannot accept connections. This permits only the
    # actual deployment-owned argv with fixture-owned identity/trust paths and
    # the fixed readiness command; it does not admit arbitrary SSH destinations.
    assert config.host == "127.0.0.1"
    transport = ShellTransportConfig.from_infrastructure(config, system_root)
    declare_core_command(build_fixed_ssh_command(transport, "true"))
