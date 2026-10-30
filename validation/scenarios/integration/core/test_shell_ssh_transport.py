"""AssistantMD's native SSH client interoperates with a local external endpoint."""

import pytest

from validation.scenarios.support.ssh_endpoint import (
    PILOT_COMMAND,
    PILOT_STDERR,
    ssh_endpoint,
)

pytestmark = pytest.mark.scenario


async def test_authenticated_ssh_preserves_command_streams_and_exit_status(app):
    await app.start()
    from core.advanced_shell.preflight import (
        AdvancedShellPreflightService,
        AdvancedShellReadiness,
    )
    from core.tools.advanced_shell import FixedSshShellExecutor

    async with ssh_endpoint(app) as endpoint:
        endpoint.admit("true")
        readiness = AdvancedShellPreflightService(endpoint.config, endpoint.system_root)
        first = await readiness.status()
        assert first.state is AdvancedShellReadiness.READY
        assert (await readiness.status()).state is AdvancedShellReadiness.READY
        assert endpoint.executions == [{"command": "true", "stdin": ""}]

        endpoint.admit(PILOT_COMMAND)
        stdin = "payload: π and literal $HOME\n"
        result = await FixedSshShellExecutor(endpoint.transport()).execute(
            PILOT_COMMAND, stdin=stdin, timeout_seconds=10
        )
        assert result.status == "completed" and result.exit_code == 17
        assert result.stdout == "received:\n" + stdin
        assert result.stderr == PILOT_STDERR.decode()
        assert result.output_bytes == len(result.stdout.encode()) + len(PILOT_STDERR)
        assert endpoint.executions == [
            {"command": "true", "stdin": ""},
            {"command": PILOT_COMMAND, "stdin": stdin},
        ]
        assert endpoint.authentications and all(
            attempt["accepted"] for attempt in endpoint.authentications
        )
        app.evidence.save_json(
            "shell-result.json",
            {
                "status": result.status,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "output_bytes": result.output_bytes,
            },
        )
    assert endpoint.cleanup_completed


async def test_pinned_host_key_mismatch_rejects_before_authentication_or_execution(app):
    await app.start()
    from core.advanced_shell.preflight import (
        AdvancedShellPreflightService,
        AdvancedShellReadiness,
    )

    async with ssh_endpoint(app, mismatched_host_key=True) as endpoint:
        endpoint.admit("true")
        readiness = AdvancedShellPreflightService(endpoint.config, endpoint.system_root)
        result = await readiness.status()
        assert result.state is AdvancedShellReadiness.HOST_KEY_MISMATCH
        assert endpoint.authentications == [] and endpoint.executions == []
        assert str(endpoint.system_root) not in result.message
        app.evidence.save_json(
            "shell-readiness.json",
            {
                "state": result.state.value,
                "message": result.message,
            },
        )
    assert endpoint.cleanup_completed
