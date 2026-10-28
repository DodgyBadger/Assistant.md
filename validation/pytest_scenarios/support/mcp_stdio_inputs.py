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
