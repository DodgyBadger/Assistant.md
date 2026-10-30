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
