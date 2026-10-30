"""Controlled external SSH workloads and original application activity reads."""

import json

HOLD = "fixture-held-session"
FLOOD = "fixture-large-output"
DISCONNECT = "fixture-disconnect"
PRIVATE_COMMAND = "fixture-sensitive-command-marker"
PRIVATE_STDIN = "fixture-sensitive-stdin-marker\n"


def install_shell_workloads(endpoint):
    async def hold(process):
        # Hold the actual remote channel until the native SSH client disconnects.
        # This observes channel lifetime; it does not simulate remote descendants.
        await process.wait_closed()

    async def flood(process):
        process.stdout.write(b"a" * 4096)
        process.stderr.write(b"b" * 4096)
        process.exit(0)

    async def disconnect(process):
        process.channel.get_connection().abort()

    async def private(process):
        await process.stdin.readline()
        process.stdout.write(b"controlled result\n")
        process.exit(0)

    endpoint.actions.update(
        {HOLD: hold, FLOOD: flood, DISCONNECT: disconnect, PRIVATE_COMMAND: private}
    )
    for command in endpoint.actions:
        endpoint.admit(command)


def shell_activity(app):
    rows = [
        json.loads(line)
        for line in (app.root / "system/activity.log").read_text().splitlines()
    ]
    return [
        row["data"]
        for row in rows
        if row.get("data", {}).get("event", "").startswith("advanced_shell_")
    ]


def ssh_diagnostic_path(app, kind):
    import sys

    path = app.root / ("external-ssh-" + kind)
    path.mkdir()
    if kind != "missing":
        diagnostic = {
            "dns": "Could not resolve hostname PRIVATE_SSH_DIAGNOSTIC: Name or service not known",
            "other": "PRIVATE_SSH_DIAGNOSTIC: external command rejection",
        }[kind]
        # Controlled external executable only, for diagnostic projection without
        # live DNS/environment dependence. Native SSH authentication and channel
        # behavior are proved by the other cases. Executor/preflight/logger run
        # unchanged; this case makes no claim that an actual DNS lookup failed.
        executable = path / "ssh"
        executable.write_text(
            f"#!{sys.executable}\nimport sys\nsys.stderr.write({diagnostic!r})\nsys.exit(255)\n"
        )
        executable.chmod(0o700)
    return path


def missing_ssh_snapshot(app, endpoint, path):
    from validation.scenarios.support.settings import run_settings_process

    # The core audit guard rejects unresolvable executables before OS dispatch.
    # A declared isolated interpreter owns this empty PATH, allowing the real
    # OS FileNotFoundError to reach real preflight code without weakening guards.
    root = app.root / "missing-ssh-child"
    root.mkdir()
    run_settings_process(
        app,
        root,
        f"""
import asyncio,json,os
from pathlib import Path
from core.advanced_shell import AdvancedShellConfig,ExecutionMode
from core.advanced_shell.preflight import AdvancedShellPreflightService
config=AdvancedShellConfig(ExecutionMode.ADVANCED,'127.0.0.1',{endpoint.config.port},'fixture-user',None)
os.environ['PATH']={str(path)!r}
status=asyncio.run(AdvancedShellPreflightService(config,Path({str(endpoint.system_root)!r})).status())
Path({str(root / 'snapshot.json')!r}).write_text(json.dumps({{'state':status.state.value,'message':status.message}}))
""",
    )
    return json.loads((root / "snapshot.json").read_text())
