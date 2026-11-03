"""Run only the local client-key bootstrap with isolated deployment roots."""

import os
import shutil
import subprocess
from pathlib import Path

from validation.support.core_processes import declare_core_command


def bootstrap_client(app, mode, profiles):
    root = app.root / f"bootstrap-{mode}-{profiles.replace(',', '_')}"
    root.mkdir()
    identity, public, host = (root / name for name in ("identity", "public", "host"))
    host.mkdir()
    keygen = shutil.which("ssh-keygen")
    assert keygen, "The local client bootstrap requires the shipped ssh-keygen utility"
    environment = {
        **os.environ,
        "COMPOSE_PROFILES": profiles,
        "ASSISTANTMD_EXECUTION_MODE": mode,
        "ASSISTANTMD_SHELL_HOST": "local-bootstrap-fixture",
        "ASSISTANTMD_SHELL_PORT": "2222",
        "ASSISTANTMD_SHELL_HOST_KEY_ALIAS": "fixture-host-alias",
        "ASSISTANTMD_SHELL_CLIENT_IDENTITY_ROOT": str(identity),
        "ASSISTANTMD_SHELL_CLIENT_PUBLIC_ROOT": str(public),
        "ASSISTANTMD_SHELL_HOST_PUBLIC_ROOT": str(host),
    }

    def run(command):
        process = subprocess.run(
            declare_core_command(command),
            cwd=Path(__file__).resolve().parents[3],
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
        )
        app.evidence.append(
            "client-bootstrap.jsonl",
            {
                "mode": mode,
                "profiles": profiles,
                "returncode": process.returncode,
                "stderr": process.stderr,
            },
        )
        return process

    generated = run(
        [
            keygen,
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-f",
            str(host / "ssh_host_ed25519_key"),
        ]
    )
    assert generated.returncode == 0, generated.stderr
    command = ["sh", "docker/bootstrap-advanced-shell-client.sh", "true"]
    return identity, public, host, lambda: run(command)
