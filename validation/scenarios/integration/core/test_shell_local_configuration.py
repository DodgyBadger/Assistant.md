"""Local shell configuration and client bootstrap require no SSH service."""

import base64
import json
import stat

import pytest

from validation.scenarios.support.shell_bootstrap_inputs import bootstrap_client

pytestmark = pytest.mark.scenario


async def test_shell_configuration_modes_and_invalid_coordinates_fail_closed(app):
    await app.start()
    from core.advanced_shell import (
        AdvancedShellConfigurationError,
        load_advanced_shell_config,
    )
    from core.settings import AppSettings

    for mode, profiles, enabled in (
        ("restricted", "", False),
        ("restricted", "browser", False),
        ("advanced", "", True),
        ("advanced", "browser, advanced", True),
    ):
        config = load_advanced_shell_config(
            AppSettings(
                ASSISTANTMD_EXECUTION_MODE=mode,
                COMPOSE_PROFILES=profiles,
            )
        )
        assert config.enabled is enabled
        assert (config.host, config.port, config.user, config.host_key_alias) == (
            "advanced-shell",
            2222,
            "advanced-shell",
            None,
        )
    config = load_advanced_shell_config(
        AppSettings(
            ASSISTANTMD_EXECUTION_MODE="advanced",
            COMPOSE_PROFILES="",
            ASSISTANTMD_SHELL_HOST="fixture-alias",
            ASSISTANTMD_SHELL_PORT=2200,
            ASSISTANTMD_SHELL_USER="operator",
            ASSISTANTMD_SHELL_HOST_KEY_ALIAS="stable-alias",
        )
    )
    assert (config.host, config.port, config.user, config.host_key_alias) == (
        "fixture-alias",
        2200,
        "operator",
        "stable-alias",
    )
    from core.tools.advanced_shell import ShellTransportConfig

    system_root = app.root / "isolated-shell-system"
    paths = config.state_paths(system_root)
    assert paths.directory == system_root / "advanced-shell"
    assert paths.client_identity == paths.directory / "client_identity"
    assert paths.known_hosts == paths.directory / "known_hosts"
    for key_root in (None, app.root / "deployment-owned-key-root"):
        transport = ShellTransportConfig.from_infrastructure(
            config, system_root, key_root=key_root
        )
        expected_root = key_root or paths.directory
        assert transport.private_key_path == expected_root / "client_identity"
        assert transport.known_hosts_path == expected_root / "known_hosts"
    for values in (
        {"COMPOSE_PROFILES": "advanced"},
        {"ASSISTANTMD_EXECUTION_MODE": "advanced", "COMPOSE_PROFILES": "browser"},
        {"ASSISTANTMD_EXECUTION_MODE": "unexpected"},
        {"ASSISTANTMD_SHELL_HOST": "-oProxyCommand=bad"},
        {"ASSISTANTMD_SHELL_USER": "two users"},
        {"ASSISTANTMD_SHELL_PORT": 0},
    ):
        with pytest.raises(AdvancedShellConfigurationError):
            load_advanced_shell_config(
                AppSettings(
                    **{
                        "ASSISTANTMD_EXECUTION_MODE": "restricted",
                        "COMPOSE_PROFILES": "",
                        **values,
                    }
                )
            )


@pytest.mark.parametrize(
    "mode,profiles,accepted",
    [
        ("restricted", "", True),
        ("restricted", "advanced", False),
        ("advanced", "browser", False),
        ("advanced", "browser,advanced", True),
    ],
)
async def test_local_client_bootstrap_gates_and_persists_key_and_trust(
    app, mode, profiles, accepted
):
    await app.start()
    identity, public, host, run = bootstrap_client(app, mode, profiles)
    first = run()
    assert (first.returncode == 0) is accepted, first.stderr
    if not accepted or mode == "restricted":
        assert not identity.exists() and not public.exists()
        return
    private = identity / "client_identity"
    known_hosts = identity / "known_hosts"
    original = private.read_bytes()
    assert stat.S_IMODE(identity.stat().st_mode) == 0o700
    assert stat.S_IMODE(private.stat().st_mode) == 0o600
    assert stat.S_IMODE((public / "client_identity.pub").stat().st_mode) == 0o644
    assert (public / "client_identity.pub").read_bytes() == private.with_suffix(
        ".pub"
    ).read_bytes()
    assert (
        known_hosts.read_text()
        == "fixture-host-alias " + (host / "ssh_host_ed25519_key.pub").read_text()
    )
    private.with_suffix(".pub").unlink()
    second = run()
    assert second.returncode == 0, second.stderr
    assert private.read_bytes() == original
    assert (
        private.with_suffix(".pub").read_bytes()
        == (public / "client_identity.pub").read_bytes()
    )


async def test_complete_stdio_envelope_budget_and_unready_status_privacy(app):
    await app.start()
    from api.services.system import project_advanced_shell_status
    from core.advanced_shell import AdvancedShellConfig, ExecutionMode
    from core.advanced_shell.preflight import AdvancedShellPreflightService
    from core.advanced_shell.stdio import (
        MAX_STRUCTURED_STDIO_ENVELOPE_BYTES,
        encode_structured_launch,
    )

    with pytest.raises(ValueError, match="size limit"):
        encode_structured_launch(
            executable="/usr/bin/python3",
            arguments=(),
            working_directory="/workspace",
            environment=(("PAYLOAD", "x" * MAX_STRUCTURED_STDIO_ENVELOPE_BYTES),),
        )
    encoded = encode_structured_launch(
        executable="/usr/bin/python3",
        arguments=("-m", "provider"),
        working_directory="/workspace",
        environment=(("LOG_LEVEL", "warning"),),
    )
    raw = base64.urlsafe_b64decode(encoded.partition(":")[2])
    assert len(raw) <= MAX_STRUCTURED_STDIO_ENVELOPE_BYTES
    assert json.loads(raw)["args"] == ["-m", "provider"]
    config = AdvancedShellConfig(
        ExecutionMode.ADVANCED, "fixture-shell", 2200, "operator", "PRIVATE_HOST_ALIAS"
    )
    # Missing local identity stops the real preflight before any SSH launch.
    snapshot = await AdvancedShellPreflightService(
        config, app.root / "no-shell-identity"
    ).status()
    payload = project_advanced_shell_status(config, snapshot).model_dump(mode="json")
    assert payload["readiness_state"] == "identity_missing"
    assert payload["host"] == "fixture-shell" and payload["port"] == 2200
    assert "PRIVATE_HOST_ALIAS" not in json.dumps(payload)
    assert str(app.root) not in json.dumps(payload)
    app.evidence.save_json("shell-status.json", payload)
