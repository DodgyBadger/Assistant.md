"""Real native SSH sessions enforce app-owned budgets, lifetime and admission."""

import asyncio
from dataclasses import replace

import pytest

from validation.scenarios.support.shell_ssh_workloads import (
    DISCONNECT,
    FLOOD,
    HOLD,
    PRIVATE_COMMAND,
    PRIVATE_STDIN,
    install_shell_workloads,
    shell_activity,
)
from validation.scenarios.support.ssh_endpoint import ssh_endpoint

pytestmark = pytest.mark.scenario


async def test_wrong_client_key_is_denied_and_readiness_is_sanitized(app):
    await app.start()
    from core.advanced_shell.preflight import (
        AdvancedShellPreflightService,
        AdvancedShellReadiness,
    )

    async with ssh_endpoint(app, reject_client_key=True) as endpoint:
        endpoint.admit("true")
        preflight = AdvancedShellPreflightService(endpoint.config, endpoint.system_root)
        snapshot = await preflight.status()
        assert snapshot.state is AdvancedShellReadiness.AUTHENTICATION_FAILURE
        assert endpoint.authentications and not any(
            row["accepted"] for row in endpoint.authentications
        )
        assert not endpoint.executions
        assert str(endpoint.system_root) not in snapshot.message
        from core.advanced_shell import ExecutionMode
        from core.advanced_shell.capability import AdvancedShellCapabilityService
        from core.identity import LOCAL_USER_AUTHORITY

        service = AdvancedShellCapabilityService(
            endpoint.config, endpoint.transport(), preflight
        )
        assert await service.resolve_for_primary_chat(LOCAL_USER_AUTHORITY) is None
        attempted = len(endpoint.authentications)
        restricted = replace(endpoint.config, execution_mode=ExecutionMode.RESTRICTED)
        restricted_preflight = AdvancedShellPreflightService(
            restricted, endpoint.system_root
        )
        assert (
            await restricted_preflight.status()
        ).state is AdvancedShellReadiness.INACTIVE
        restricted_service = AdvancedShellCapabilityService(
            restricted, endpoint.transport(), restricted_preflight
        )
        assert (
            await restricted_service.resolve_for_primary_chat(LOCAL_USER_AUTHORITY)
            is None
        )
        assert len(endpoint.authentications) == attempted
    assert endpoint.cleanup_completed


async def test_queue_deadline_and_cancelled_session_release_shell_capacity(app):
    await app.start()
    from core.tools.advanced_shell import FixedSshShellExecutor

    async with ssh_endpoint(app) as endpoint:
        install_shell_workloads(endpoint)
        endpoint.admit("queued-command")
        endpoint.admit("true")
        executor = FixedSshShellExecutor(
            replace(endpoint.transport(), max_concurrent_commands=1)
        )
        held = asyncio.create_task(executor.execute(HOLD, timeout_seconds=30))
        try:
            await asyncio.wait_for(endpoint.entered(HOLD).wait(), 10)
            queued = await executor.execute("queued-command", timeout_seconds=0.2)
            assert queued.status == "timed_out" and queued.output_bytes == 0
            assert [row["command"] for row in endpoint.executions] == [HOLD]
            held.cancel()
            with pytest.raises(asyncio.CancelledError):
                await held
            await asyncio.wait_for(endpoint.closed(HOLD).wait(), 5)
            recovered = await executor.execute("true", timeout_seconds=5)
            assert recovered.status == "completed" and recovered.exit_code == 0
        finally:
            held.cancel()
            await asyncio.gather(held, return_exceptions=True)
    assert endpoint.cleanup_completed


async def test_runtime_deadline_disconnects_the_held_ssh_session(app):
    await app.start()
    from core.tools.advanced_shell import FixedSshShellExecutor

    async with ssh_endpoint(app) as endpoint:
        install_shell_workloads(endpoint)
        task = asyncio.create_task(
            FixedSshShellExecutor(endpoint.transport()).execute(HOLD, timeout_seconds=2)
        )
        try:
            await asyncio.wait_for(endpoint.entered(HOLD).wait(), 5)
            result = await asyncio.wait_for(task, 6)
            assert result.status == "timed_out"
            await asyncio.wait_for(endpoint.closed(HOLD).wait(), 5)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert endpoint.cleanup_completed


async def test_input_and_combined_output_budgets_bound_real_ssh_work(app):
    await app.start()
    from core.tools.advanced_shell import FixedSshShellExecutor, ShellInputLimitExceeded

    async with ssh_endpoint(app) as endpoint:
        install_shell_workloads(endpoint)
        executor = FixedSshShellExecutor(
            replace(endpoint.transport(), max_input_bytes=8, max_output_bytes=64)
        )
        with pytest.raises(ShellInputLimitExceeded):
            await executor.execute(FLOOD, stdin="é" * 5)
        assert endpoint.executions == []
        result = await executor.execute(FLOOD, timeout_seconds=5)
        assert result.status == "output_limit_exceeded"
        assert result.output_bytes > 64
        assert len(result.stdout.encode()) + len(result.stderr.encode()) <= 64
    assert endpoint.cleanup_completed


async def test_transport_disconnect_has_an_indeterminate_exit_status(app):
    await app.start()
    from core.tools.advanced_shell import FixedSshShellExecutor

    async with ssh_endpoint(app) as endpoint:
        install_shell_workloads(endpoint)
        result = await FixedSshShellExecutor(endpoint.transport()).execute(
            DISCONNECT, timeout_seconds=5
        )
        assert result.status == "indeterminate_255" and result.exit_code == 255
    assert endpoint.cleanup_completed


async def test_real_shell_capability_and_activity_preserve_authority_and_privacy(app):
    await app.start()
    from core.advanced_shell.capability import AdvancedShellCapabilityService
    from core.advanced_shell.preflight import AdvancedShellPreflightService
    from core.identity import LOCAL_USER_AUTHORITY, ExecutionAuthority
    from core.tools.base import ToolRecoveryPolicy, recovery_policy_from_tool_metadata

    async with ssh_endpoint(app) as endpoint:
        install_shell_workloads(endpoint)
        endpoint.admit("true")
        service = AdvancedShellCapabilityService(
            endpoint.config,
            endpoint.transport(),
            AdvancedShellPreflightService(endpoint.config, endpoint.system_root),
        )
        assert (
            await service.resolve_for_primary_chat(ExecutionAuthority("unmapped-owner"))
            is None
        )
        assert endpoint.executions == []
        tool = await service.resolve_for_primary_chat(LOCAL_USER_AUTHORITY)
        assert tool is not None
        assert (
            recovery_policy_from_tool_metadata(tool.metadata)
            is ToolRecoveryPolicy.MANUAL_REQUIRED
        )
        result = await tool.function(command=PRIVATE_COMMAND, stdin=PRIVATE_STDIN)
        assert result.metadata["status"] == "completed"
        events = shell_activity(app)
        started = next(
            row for row in events if row["event"] == "advanced_shell_command_started"
        )
        completed = next(
            row for row in events if row["event"] == "advanced_shell_command_completed"
        )
        assert started["command_chars"] == len(PRIVATE_COMMAND)
        assert started["stdin_bytes"] == len(PRIVATE_STDIN.encode())
        assert completed["exit_code"] == 0 and completed["output_bytes"] > 0
        assert PRIVATE_COMMAND not in str(events) and PRIVATE_STDIN.strip() not in str(
            events
        )
    assert endpoint.cleanup_completed


@pytest.mark.parametrize(
    "missing,expected",
    [("private_key_path", "identity_missing"), ("known_hosts_path", "trust_missing")],
)
async def test_missing_identity_or_trust_blocks_ssh_before_network_admission(
    app, missing, expected
):
    await app.start()
    from core.advanced_shell.preflight import AdvancedShellPreflightService

    async with ssh_endpoint(app) as endpoint:
        getattr(endpoint.transport(), missing).unlink()
        status = await AdvancedShellPreflightService(
            endpoint.config, endpoint.system_root
        ).status()
        assert status.state.value == expected
        assert endpoint.authentications == [] and endpoint.executions == []
    assert endpoint.cleanup_completed


@pytest.mark.parametrize(
    "kind,expected",
    [("missing", "ssh_unavailable"), ("dns", "dns_failure"), ("other", "unavailable")],
)
async def test_external_ssh_diagnostics_are_classified_without_disclosing_raw_output(
    app, monkeypatch, kind, expected
):
    await app.start()
    from core.advanced_shell.preflight import AdvancedShellPreflightService
    from validation.scenarios.support.shell_ssh_workloads import (
        missing_ssh_snapshot,
        ssh_diagnostic_path,
    )

    async with ssh_endpoint(app) as endpoint:
        endpoint.admit("true")
        path = ssh_diagnostic_path(app, kind)
        if kind == "missing":
            observed = missing_ssh_snapshot(app, endpoint, path)
        else:
            with monkeypatch.context() as environment:
                environment.setenv("PATH", str(path))
                status = await AdvancedShellPreflightService(
                    endpoint.config, endpoint.system_root
                ).status()
            observed = {"state": status.state.value, "message": status.message}
        assert observed["state"] == expected
        assert "PRIVATE_SSH_DIAGNOSTIC" not in observed["message"]
        app.evidence.save_json("external-ssh-diagnostic.json", observed)
        assert "PRIVATE_SSH_DIAGNOSTIC" not in str(shell_activity(app))
        assert endpoint.authentications == [] and endpoint.executions == []
    assert endpoint.cleanup_completed
