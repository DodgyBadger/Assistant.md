"""Deterministic tests for advanced-shell infrastructure configuration."""

import base64
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from core.advanced_shell import (
    AdvancedShellConfig,
    AdvancedShellConfigurationError,
    ExecutionMode,
    load_advanced_shell_config,
)
from core.advanced_shell.capability import AdvancedShellCapabilityService
from core.advanced_shell.preflight import (
    AdvancedShellPreflightService,
    AdvancedShellPreflightSnapshot,
    AdvancedShellReadiness,
)
from core.advanced_shell.stdio import (
    MAX_STRUCTURED_STDIO_ENVELOPE_BYTES,
    encode_structured_launch,
)
from core.chat.instructions import primary_chat_instruction_layers
from core.constants import ADVANCED_SHELL_FLIGHT_CARD, REGULAR_CHAT_INSTRUCTIONS
from core.identity import ExecutionAuthority
from core.runtime.paths import set_bootstrap_roots
from core.settings import AppSettings
from core.tools.advanced_shell import (
    AdvancedShell,
    ShellExecutionResult,
    ShellTransportConfig,
)
from core.tools.base import ToolRecoveryPolicy, recovery_policy_from_tool_metadata

_TEST_ROOT = Path("/tmp/assistantmd-advanced-shell-tests")
set_bootstrap_roots(_TEST_ROOT / "data", _TEST_ROOT / "system")


def test_advanced_shell_defaults_are_restricted_and_fixed() -> None:
    config = load_advanced_shell_config(AppSettings())

    assert config.execution_mode is ExecutionMode.RESTRICTED
    assert not config.enabled
    assert config.host == "advanced-shell"
    assert config.port == 2222
    assert config.user == "advanced-shell"
    assert config.host_key_alias is None


def test_structured_stdio_envelope_enforces_complete_payload_budget() -> None:
    environment = (("PAYLOAD", "x" * MAX_STRUCTURED_STDIO_ENVELOPE_BYTES),)

    with pytest.raises(ValueError, match="launch exceeds the size limit"):
        encode_structured_launch(
            executable="/usr/bin/python3",
            arguments=(),
            working_directory="/workspace",
            environment=environment,
        )

    encoded = encode_structured_launch(
        executable="/usr/bin/python3",
        arguments=("-m", "provider"),
        working_directory="/workspace",
        environment=(("LOG_LEVEL", "warning"),),
    )
    raw = encoded.partition(":")[2]
    payload = base64.urlsafe_b64decode(raw)
    assert len(payload) <= MAX_STRUCTURED_STDIO_ENVELOPE_BYTES
    assert json.loads(payload)["args"] == ["-m", "provider"]


def test_advanced_compose_profile_and_execution_mode_enable_shell() -> None:
    config = load_advanced_shell_config(
        AppSettings(
            COMPOSE_PROFILES="browser, advanced",
            ASSISTANTMD_EXECUTION_MODE="advanced",
        )
    )

    assert config.execution_mode is ExecutionMode.ADVANCED
    assert config.enabled


@pytest.mark.parametrize("execution_mode", [None, "restricted"])
def test_advanced_compose_profile_requires_explicit_advanced_execution_mode(
    execution_mode: str | None,
) -> None:
    values: dict[str, str] = {"COMPOSE_PROFILES": "advanced"}
    if execution_mode is not None:
        values["ASSISTANTMD_EXECUTION_MODE"] = execution_mode

    with pytest.raises(
        AdvancedShellConfigurationError,
        match="ASSISTANTMD_EXECUTION_MODE must also be advanced",
    ):
        load_advanced_shell_config(AppSettings(**values))


def test_compose_deployment_rejects_advanced_mode_without_advanced_profile() -> None:
    with pytest.raises(
        AdvancedShellConfigurationError,
        match="COMPOSE_PROFILES must also include advanced",
    ):
        load_advanced_shell_config(
            AppSettings(
                COMPOSE_PROFILES="browser",
                ASSISTANTMD_EXECUTION_MODE="advanced",
            )
        )


def test_client_bootstrap_requires_both_advanced_settings(
    tmp_path: Path,
) -> None:
    identity_root = tmp_path / "client-identity"
    client_public_root = tmp_path / "client-public"
    host_public_root = tmp_path / "host-public"
    host_public_root.mkdir()
    subprocess.run(
        [
            "ssh-keygen",
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-f",
            str(host_public_root / "ssh_host_ed25519_key"),
        ],
        check=True,
    )
    environment = {
        **os.environ,
        "COMPOSE_PROFILES": "browser, advanced",
        "ASSISTANTMD_EXECUTION_MODE": "advanced",
        "ASSISTANTMD_SHELL_CLIENT_IDENTITY_ROOT": str(identity_root),
        "ASSISTANTMD_SHELL_CLIENT_PUBLIC_ROOT": str(client_public_root),
        "ASSISTANTMD_SHELL_HOST_PUBLIC_ROOT": str(host_public_root),
    }
    subprocess.run(
        ["sh", "docker/bootstrap-advanced-shell-client.sh", "true"],
        check=True,
        env=environment,
    )

    assert (identity_root / "client_identity").is_file()
    assert (identity_root / "known_hosts").is_file()
    assert (client_public_root / "client_identity.pub").is_file()


@pytest.mark.parametrize("execution_mode", [None, "restricted"])
def test_client_bootstrap_rejects_advanced_profile_without_advanced_mode(
    tmp_path: Path, execution_mode: str | None
) -> None:
    identity_root = tmp_path / "client-identity"
    environment = {
        **os.environ,
        "COMPOSE_PROFILES": "advanced",
        "ASSISTANTMD_SHELL_CLIENT_IDENTITY_ROOT": str(identity_root),
    }
    if execution_mode is None:
        environment.pop("ASSISTANTMD_EXECUTION_MODE", None)
    else:
        environment["ASSISTANTMD_EXECUTION_MODE"] = execution_mode

    result = subprocess.run(
        ["sh", "docker/bootstrap-advanced-shell-client.sh", "true"],
        check=False,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "ASSISTANTMD_EXECUTION_MODE must also be advanced" in result.stderr
    assert not identity_root.exists()


def test_client_bootstrap_rejects_advanced_mode_without_advanced_profile(
    tmp_path: Path,
) -> None:
    identity_root = tmp_path / "client-identity"
    result = subprocess.run(
        ["sh", "docker/bootstrap-advanced-shell-client.sh", "true"],
        check=False,
        env={
            **os.environ,
            "COMPOSE_PROFILES": "browser",
            "ASSISTANTMD_EXECUTION_MODE": "advanced",
            "ASSISTANTMD_SHELL_CLIENT_IDENTITY_ROOT": str(identity_root),
        },
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "COMPOSE_PROFILES must also include advanced" in result.stderr
    assert not identity_root.exists()


def test_advanced_shell_flight_card_defines_tool_selection_without_secrets() -> None:
    instruction = ADVANCED_SHELL_FLIGHT_CARD

    for required in (
        "base tool-selection rules still apply",
        "Delegates do not receive shell",
        "official AssistantMD MCP connection",
        "inspect the working directory and exact target",
        "unprivileged user in a separate constrained Linux container",
        "/exchange/<vault-name>",
        "Inspect /exchange before relying on it",
        "Persistence applies to files",
        "there is no supported systemd or cron/service supervisor",
        "Continuously running services belong in their own managed Compose service",
    ):
        assert required in instruction
    for prohibited in (
        "owner token",
        "private key",
        "known_hosts",
        "ASSISTANTMD_SHELL_HOST",
    ):
        assert prohibited not in instruction

    assert Path("docs/tools/shell.md").is_file()


def test_primary_chat_instruction_layers_gate_advanced_shell_exactly_once() -> None:
    restricted = primary_chat_instruction_layers(
        base_instructions="base",
        tool_instructions="tools",
        has_advanced_shell=False,
    )
    advanced = primary_chat_instruction_layers(
        base_instructions="base",
        tool_instructions="tools",
        has_advanced_shell=True,
    )

    assert restricted == ("base", "tools")
    assert advanced == ("base", "tools", ADVANCED_SHELL_FLIGHT_CARD)
    assert advanced.count(ADVANCED_SHELL_FLIGHT_CARD) == 1


def test_base_instructions_keep_inline_edit_guidance_conditional() -> None:
    assert "If inline edit mode is enabled" in REGULAR_CHAT_INSTRUCTIONS
    assert "You have access to the following capabilities" not in (
        REGULAR_CHAT_INSTRUCTIONS
    )


class _RecordingLogger:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def info(self, message: str, *, data: dict[str, Any]) -> None:
        del message
        self.events.append(data)

    def warning(self, message: str, *, data: dict[str, Any]) -> None:
        del message
        self.events.append(data)


@pytest.mark.asyncio
async def test_shell_activity_is_bounded_and_omits_command_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import core.tools.advanced_shell as shell_module

    class _Executor:
        async def execute(
            self,
            command: str,
            *,
            stdin: str = "",
            timeout_seconds: float | None = None,
        ) -> ShellExecutionResult:
            assert command == "sensitive command text"
            assert stdin == "private input"
            assert timeout_seconds == 10
            return ShellExecutionResult("ok", "", 0, "completed", 2)

    recording_logger = _RecordingLogger()
    monkeypatch.setattr(shell_module, "logger", recording_logger)
    tool = AdvancedShell.for_executor(_Executor())

    await tool.function(
        command="sensitive command text",
        stdin="private input",
        timeout_seconds=10,
    )

    assert [event["event"] for event in recording_logger.events] == [
        "advanced_shell_command_started",
        "advanced_shell_command_completed",
    ]
    assert recording_logger.events[0]["command_chars"] == 22
    assert recording_logger.events[0]["stdin_bytes"] == 13
    assert recording_logger.events[1]["exit_code"] == 0
    assert recording_logger.events[1]["output_bytes"] == 2
    assert "sensitive command text" not in str(recording_logger.events)
    assert "private input" not in str(recording_logger.events)


def test_advanced_shell_coordinates_are_environment_owned() -> None:
    config = load_advanced_shell_config(
        AppSettings(
            ASSISTANTMD_EXECUTION_MODE="advanced",
            ASSISTANTMD_SHELL_HOST="shell-alias",
            ASSISTANTMD_SHELL_PORT=2200,
            ASSISTANTMD_SHELL_USER="operator",
            ASSISTANTMD_SHELL_HOST_KEY_ALIAS="stable-shell",
        )
    )

    assert config.execution_mode is ExecutionMode.ADVANCED
    assert config.enabled
    assert config.host == "shell-alias"
    assert config.port == 2200
    assert config.user == "operator"
    assert config.host_key_alias == "stable-shell"


def test_advanced_shell_state_paths_are_fixed_below_system_root() -> None:
    config = load_advanced_shell_config(AppSettings())

    paths = config.state_paths(Path("/protected/system"))

    assert paths.directory == Path("/protected/system/advanced-shell")
    assert paths.client_identity == paths.directory / "client_identity"
    assert paths.known_hosts == paths.directory / "known_hosts"

    transport = ShellTransportConfig.from_infrastructure(
        config, Path("/protected/system")
    )
    assert transport.private_key_path == paths.client_identity
    assert transport.known_hosts_path == paths.known_hosts


def test_container_transport_uses_deployment_owned_key_root() -> None:
    config = load_advanced_shell_config(AppSettings())
    key_root = Path("/run/advanced-shell/client-identity")

    transport = ShellTransportConfig.from_infrastructure(
        config, Path("/protected/system"), key_root=key_root
    )

    assert transport.private_key_path == key_root / "client_identity"
    assert transport.known_hosts_path == key_root / "known_hosts"


def test_status_projection_is_sanitized() -> None:
    from api.services.system import project_advanced_shell_status

    config = load_advanced_shell_config(
        AppSettings(
            ASSISTANTMD_EXECUTION_MODE="advanced",
            ASSISTANTMD_SHELL_HOST="custom-shell",
            ASSISTANTMD_SHELL_PORT=2200,
            ASSISTANTMD_SHELL_USER="operator",
            ASSISTANTMD_SHELL_HOST_KEY_ALIAS="private-alias",
        )
    )

    payload = project_advanced_shell_status(
        config,
        AdvancedShellPreflightSnapshot(
            state=AdvancedShellReadiness.READY,
            message="The advanced shell is authenticated and ready.",
        ),
    ).model_dump(mode="json")

    assert payload == {
        "execution_mode": "advanced",
        "host": "custom-shell",
        "port": 2200,
        "user": "operator",
        "readiness_state": "ready",
        "readiness_message": "The advanced shell is authenticated and ready.",
    }
    assert "private-alias" not in str(payload)
    assert "system" not in str(payload)


class _FakeExecutor:
    def __init__(self, result: ShellExecutionResult | BaseException) -> None:
        self.result = result
        self.calls = 0

    async def execute(self, command: str, **kwargs: Any) -> ShellExecutionResult:
        del kwargs
        assert command == "true"
        self.calls += 1
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def _advanced_config() -> AdvancedShellConfig:
    return load_advanced_shell_config(
        AppSettings(ASSISTANTMD_EXECUTION_MODE="advanced")
    )


def _write_preflight_files(config: AdvancedShellConfig, system_root: Path) -> None:
    paths = config.state_paths(system_root)
    paths.directory.mkdir(parents=True)
    paths.client_identity.write_text("private", encoding="utf-8")
    paths.known_hosts.write_text("host", encoding="utf-8")


@pytest.mark.asyncio
async def test_restricted_preflight_is_inactive_without_touching_executor(
    tmp_path: Path,
) -> None:
    executor = _FakeExecutor(ShellExecutionResult("", "", 0, "completed", 0))
    service = AdvancedShellPreflightService(
        load_advanced_shell_config(AppSettings()),
        tmp_path,
        executor_factory=lambda config: executor,
    )

    snapshot = await service.status()

    assert snapshot.state is AdvancedShellReadiness.INACTIVE
    assert executor.calls == 0


@pytest.mark.asyncio
async def test_preflight_reports_missing_identity_and_trust(tmp_path: Path) -> None:
    config = _advanced_config()
    service = AdvancedShellPreflightService(config, tmp_path)

    assert (await service.status()).state is AdvancedShellReadiness.IDENTITY_MISSING

    paths = config.state_paths(tmp_path)
    paths.directory.mkdir(parents=True)
    paths.client_identity.write_text("private", encoding="utf-8")
    service = AdvancedShellPreflightService(config, tmp_path)

    assert (await service.status()).state is AdvancedShellReadiness.TRUST_MISSING


@pytest.mark.asyncio
async def test_successful_preflight_is_authenticated_and_cached(tmp_path: Path) -> None:
    config = _advanced_config()
    _write_preflight_files(config, tmp_path)
    executor = _FakeExecutor(ShellExecutionResult("", "", 0, "completed", 0))
    service = AdvancedShellPreflightService(
        config,
        tmp_path,
        executor_factory=lambda transport: executor,
        cache_seconds=60,
    )

    first = await service.status()
    second = await service.status()

    assert first.state is AdvancedShellReadiness.READY
    assert second == first
    assert executor.calls == 1


class _FakePreflight:
    def __init__(self, state: AdvancedShellReadiness) -> None:
        self.state = state
        self.calls = 0

    async def status(self) -> AdvancedShellPreflightSnapshot:
        self.calls += 1
        return AdvancedShellPreflightSnapshot(self.state, "sanitized")


@pytest.mark.asyncio
async def test_primary_chat_shell_requires_advanced_shell_readiness(
    tmp_path: Path,
) -> None:
    config = _advanced_config()
    preflight = _FakePreflight(AdvancedShellReadiness.READY)
    service = AdvancedShellCapabilityService(
        config,
        ShellTransportConfig.from_infrastructure(config, tmp_path),
        preflight,
    )

    tool = await service.resolve_for_primary_chat(ExecutionAuthority("local-user"))

    assert tool is not None
    assert tool.name == "shell"
    assert (
        recovery_policy_from_tool_metadata(tool.metadata)
        is ToolRecoveryPolicy.MANUAL_REQUIRED
    )
    assert preflight.calls == 1


@pytest.mark.asyncio
async def test_primary_chat_shell_is_omitted_when_unavailable(tmp_path: Path) -> None:
    config = _advanced_config()
    preflight = _FakePreflight(AdvancedShellReadiness.CONNECTION_FAILURE)
    service = AdvancedShellCapabilityService(
        config,
        ShellTransportConfig.from_infrastructure(config, tmp_path),
        preflight,
    )

    tool = await service.resolve_for_primary_chat(ExecutionAuthority("local-user"))

    assert tool is None
    assert preflight.calls == 1


@pytest.mark.asyncio
async def test_restricted_shell_resolution_skips_preflight(tmp_path: Path) -> None:
    config = load_advanced_shell_config(AppSettings())
    preflight = _FakePreflight(AdvancedShellReadiness.READY)
    service = AdvancedShellCapabilityService(
        config,
        ShellTransportConfig.from_infrastructure(config, tmp_path),
        preflight,
    )

    tool = await service.resolve_for_primary_chat(ExecutionAuthority("local-user"))

    assert tool is None
    assert preflight.calls == 0


@pytest.mark.asyncio
async def test_shell_resolution_denies_unmapped_principal(tmp_path: Path) -> None:
    config = _advanced_config()
    preflight = _FakePreflight(AdvancedShellReadiness.READY)
    service = AdvancedShellCapabilityService(
        config,
        ShellTransportConfig.from_infrastructure(config, tmp_path),
        preflight,
    )

    tool = await service.resolve_for_primary_chat(ExecutionAuthority("system"))

    assert tool is None
    assert preflight.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (
            ShellExecutionResult(
                "", "Could not resolve hostname shell", 255, "indeterminate_255", 0
            ),
            AdvancedShellReadiness.DNS_FAILURE,
        ),
        (
            ShellExecutionResult("", "Connection refused", 255, "indeterminate_255", 0),
            AdvancedShellReadiness.CONNECTION_FAILURE,
        ),
        (
            ShellExecutionResult(
                "",
                "REMOTE HOST IDENTIFICATION HAS CHANGED",
                255,
                "indeterminate_255",
                0,
            ),
            AdvancedShellReadiness.HOST_KEY_MISMATCH,
        ),
        (
            ShellExecutionResult(
                "", "Permission denied (publickey)", 255, "indeterminate_255", 0
            ),
            AdvancedShellReadiness.AUTHENTICATION_FAILURE,
        ),
        (
            ShellExecutionResult("", "", None, "timed_out", 0),
            AdvancedShellReadiness.CONNECTION_FAILURE,
        ),
    ],
)
async def test_preflight_sanitizes_common_ssh_failures(
    tmp_path: Path,
    result: ShellExecutionResult,
    expected: AdvancedShellReadiness,
) -> None:
    config = _advanced_config()
    _write_preflight_files(config, tmp_path)
    executor = _FakeExecutor(result)
    service = AdvancedShellPreflightService(
        config,
        tmp_path,
        executor_factory=lambda transport: executor,
    )

    snapshot = await service.status()

    assert snapshot.state is expected
    if result.stderr:
        assert result.stderr not in snapshot.message


@pytest.mark.parametrize(
    ("values", "message"),
    [
        (
            {"ASSISTANTMD_EXECUTION_MODE": "unexpected"},
            "ASSISTANTMD_EXECUTION_MODE must be restricted or advanced.",
        ),
        (
            {"ASSISTANTMD_SHELL_HOST": "-oProxyCommand=bad"},
            "ASSISTANTMD_SHELL_HOST must contain only visible",
        ),
        (
            {"ASSISTANTMD_SHELL_USER": "two users"},
            "ASSISTANTMD_SHELL_USER must contain only visible",
        ),
        (
            {"ASSISTANTMD_SHELL_PORT": 0},
            "ASSISTANTMD_SHELL_PORT must be between 1 and 65535.",
        ),
    ],
)
def test_invalid_advanced_shell_configuration_fails_closed(
    values: dict[str, str | int], message: str
) -> None:
    with pytest.raises(AdvancedShellConfigurationError, match=message):
        load_advanced_shell_config(AppSettings(**values))
