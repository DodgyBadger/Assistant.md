"""Real MCP SDK servers on fixture-owned SSH stdio channels."""

import asyncio
import base64
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool


@dataclass
class SSHMCPWorkload:
    requests: list = field(default_factory=list)
    launches: list = field(default_factory=list)
    calls: list = field(default_factory=list)
    roots: list = field(default_factory=list)
    list_entered: asyncio.Event = field(default_factory=asyncio.Event)
    list_release: asyncio.Event = field(default_factory=asyncio.Event)
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    command: str = ""

    async def serve(self, process):
        # This external endpoint captures the actual launch protocol; it does
        # not run the deployed wrapper or certify its execution restrictions.
        prefix, encoded = process.command.split(":", 1)
        assert prefix == "assistantmd-stdio-v1"
        self.launches.append(json.loads(base64.urlsafe_b64decode(encoded)))
        server = Server("local-ssh-mcp-fixture")

        @server.list_tools()
        async def tools():
            self.list_entered.set()
            await self.list_release.wait()
            return [
                Tool(
                    name="echo",
                    description="Echo controlled input",
                    inputSchema={
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                )
            ]

        @server.call_tool()
        async def call(name, arguments):
            self.calls.append({"name": name, "arguments": arguments})
            roots = await server.request_context.session.list_roots()
            self.roots.extend(str(root.uri) for root in roots.roots)
            return [TextContent(type="text", text="echo:" + arguments["text"])]

        try:
            # The installed SDK owns JSON-RPC initialization, dispatch and
            # framing. These adapters connect its public stdio transport to
            # actual SSH streams without changing process-global stdin/stdout.
            async with stdio_server(
                SSHInput(process.stdin, self.requests), SSHOutput(process.stdout)
            ) as streams:
                try:
                    await server.run(*streams, server.create_initialization_options())
                finally:
                    # The supplied stream belongs to this endpoint. Closing its
                    # send side lets the SDK's stdout pump finish after input EOF.
                    await streams[1].aclose()
        finally:
            self.closed.set()
            process.exit(0)


class SSHInput:
    def __init__(self, stream, requests):
        self.stream, self.requests = stream, requests

    async def __aiter__(self):
        async for line in self.stream:
            self.requests.append(json.loads(line))
            yield line.decode()


class SSHOutput:
    def __init__(self, stream):
        self.stream = stream

    async def write(self, text):
        self.stream.write(text.encode())

    async def flush(self):
        await self.stream.drain()


def add_ssh_mcp_connection(endpoint, service, name, *, held=False):
    from core.advanced_shell.stdio import encode_structured_launch
    from core.identity import LOCAL_USER_AUTHORITY
    from core.mcp import MCPConnectionCreate, MCPStdioConfig, MCPTransport

    launch = MCPStdioConfig(
        executable="/usr/bin/fixture-mcp",
        arguments=(name, "literal ; $HOME"),
        working_directory="/workspace",
        environment=(("FIXTURE_LABEL", name),),
        roots=("/workspace", "/home/advanced-shell"),
    )
    connection = service.create_connection_for_authority(
        LOCAL_USER_AUTHORITY,
        MCPConnectionCreate(
            display_name=name,
            transport=MCPTransport.ADVANCED_SHELL_STDIO,
            stdio=launch,
        ),
    )
    workload = SSHMCPWorkload()
    if not held:
        workload.list_release.set()
    workload.command = encode_structured_launch(
        executable=launch.executable,
        arguments=launch.arguments,
        working_directory=launch.working_directory,
        environment=launch.environment,
    )
    endpoint.actions[workload.command] = workload.serve
    endpoint.admit(workload.command)
    return connection, workload


@asynccontextmanager
async def ssh_mcp_manager(app, endpoint, *, launch_limit=1):
    from core.advanced_shell.preflight import AdvancedShellPreflightService
    from core.mcp import MCPConnectionManager, MCPConnectionService
    from core.secrets import get_encrypted_secrets_service

    endpoint.admit("true")
    service = MCPConnectionService(
        system_root=str(app.root / "system"),
        secrets=get_encrypted_secrets_service(),
        advanced_shell_stdio_enabled=True,
    )
    preflight = AdvancedShellPreflightService(endpoint.config, endpoint.system_root)
    manager = MCPConnectionManager(
        connections=service,
        advanced_shell_stdio=endpoint.transport(),
        advanced_shell_readiness=preflight.status,
        idle_timeout_seconds=0,
        max_concurrent_stdio_launches=launch_limit,
    )
    try:
        yield service, manager
    finally:
        await manager.shutdown()


def save_mcp_observations(app, workloads):
    app.evidence.save_json(
        "ssh-mcp-observations.json",
        [
            {
                "requests": item.requests,
                "launches": item.launches,
                "calls": item.calls,
                "roots": item.roots,
                "closed": item.closed.is_set(),
            }
            for item in workloads
        ],
    )
