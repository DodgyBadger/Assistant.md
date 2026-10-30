"""Fixture-owned external SSH endpoint; application SSH execution stays real."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import asyncssh

from validation.support.core_processes import declare_core_command

if TYPE_CHECKING:
    from core.advanced_shell import AdvancedShellConfig

PILOT_COMMAND = "pilot --literal 'spaces ; $HOME'"
PILOT_STDERR = b"external fixture stderr\n"


@dataclass
class SSHWorkload:
    config: AdvancedShellConfig | None
    system_root: Path
    connections: set = field(default_factory=set)
    executions: list = field(default_factory=list)
    authentications: list = field(default_factory=list)
    handlers: set = field(default_factory=set)
    cleanup_completed: bool = False

    def transport(self):
        from core.tools.advanced_shell import ShellTransportConfig

        assert self.config is not None, "SSH endpoint has not started"
        return ShellTransportConfig.from_infrastructure(self.config, self.system_root)

    def admit(self, command):
        from core.tools.advanced_shell import build_fixed_ssh_command

        declare_core_command(build_fixed_ssh_command(self.transport(), command))


@asynccontextmanager
async def ssh_endpoint(app, *, mismatched_host_key=False):
    from core.advanced_shell import AdvancedShellConfig, ExecutionMode

    # This substitutes only the external SSH service. The application invokes
    # its real native OpenSSH client, pinned trust policy, executor and preflight.
    # Controlled replies do not prove the deployed forced-command wrapper,
    # remote descendant cleanup, filesystem restrictions or container isolation.
    root = app.root / "local-ssh-endpoint"
    root.mkdir()
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    app.evidence.register_secret(
        client_key.export_private_key().decode(), host_key.export_private_key().decode()
    )
    workload = SSHWorkload(None, root)

    class Server(asyncssh.SSHServer):
        def connection_made(self, connection):
            self.connection = connection
            workload.connections.add(connection)

        def connection_lost(self, exc):
            workload.connections.discard(self.connection)

        def begin_auth(self, username):
            return True

        def public_key_auth_supported(self):
            return True

        def validate_public_key(self, username, key):
            accepted = (
                username == "fixture-user"
                and key.export_public_key() == client_key.export_public_key()
            )
            workload.authentications.append(
                {"username": username, "accepted": accepted}
            )
            return accepted

    async def handle(process):
        task = asyncio.current_task()
        workload.handlers.add(task)
        try:
            row = {"command": process.command, "stdin": ""}
            workload.executions.append(row)
            if process.command == "true":
                process.exit(0)
            elif process.command == PILOT_COMMAND:
                # The real executor keeps stdin open as a lifetime signal. This
                # external line protocol responds without waiting for stream EOF.
                received = await process.stdin.readline()
                row["stdin"] = received.decode()
                process.stdout.write(b"received:\n" + received)
                process.stderr.write(PILOT_STDERR)
                process.exit(17)
            else:
                process.stderr.write(b"Unexpected fixture command\n")
                process.exit(64)
        finally:
            workload.handlers.discard(task)

    listener = await asyncssh.listen(
        "127.0.0.1",
        0,
        server_factory=Server,
        server_host_keys=[host_key],
        process_factory=handle,
        encoding=None,
    )
    try:
        workload.config = AdvancedShellConfig(
            ExecutionMode.ADVANCED,
            "127.0.0.1",
            listener.get_port(),
            "fixture-user",
            None,
        )
        paths = workload.config.state_paths(root)
        paths.directory.mkdir()
        client_key.write_private_key(paths.client_identity)
        paths.client_identity.chmod(0o600)
        trusted_key = (
            asyncssh.generate_private_key("ssh-ed25519")
            if mismatched_host_key
            else host_key
        )
        paths.known_hosts.write_text(
            f"[127.0.0.1]:{listener.get_port()} "
            + trusted_key.export_public_key().decode()
        )
        yield workload
    finally:
        listener.close()
        await listener.wait_closed()
        connections = tuple(workload.connections)
        for connection in connections:
            connection.close()
        async with asyncio.timeout(5):
            await asyncio.gather(
                *(connection.wait_closed() for connection in connections)
            )
            if workload.handlers:
                await asyncio.gather(*tuple(workload.handlers))
        assert not workload.connections and not workload.handlers
        workload.cleanup_completed = True
        app.evidence.save_json(
            "local-ssh-endpoint.json",
            {
                "executions": workload.executions,
                "authentications": workload.authentications,
                "cleanup_completed": workload.cleanup_completed,
                "scope": "Real SSH transport; controlled external replies; no container or remote process-tree guarantee",
            },
        )
