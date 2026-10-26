"""Targeted contract tests for ingress-authentication policy primitives."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.authentication import (
    AuthenticationConfigurationError,
    AuthenticationMechanism,
    AuthenticationMode,
    load_authentication_policy,
)
from core.identity import LOCAL_USER_PRINCIPAL_ID
from core.settings import AppSettings

_SECRET = "a" * 32


def test_mode_must_be_explicit() -> None:
    with pytest.raises(AuthenticationConfigurationError, match="explicitly select"):
        load_authentication_policy(AppSettings())


def test_disabled_mode_is_completely_open() -> None:
    policy = load_authentication_policy(AppSettings(ASSISTANTMD_AUTH_MODE="disabled"))

    identity = policy.authenticate_disabled()

    assert policy.mode is AuthenticationMode.DISABLED
    assert not policy.is_protected
    assert identity is not None
    assert identity.principal_id == LOCAL_USER_PRINCIPAL_ID
    assert identity.mechanism is AuthenticationMechanism.DISABLED


@pytest.mark.parametrize("peer_host", ["127.0.0.1", "::1"])
def test_loopback_mode_accepts_only_exact_loopback(peer_host: str) -> None:
    policy = load_authentication_policy(AppSettings(ASSISTANTMD_AUTH_MODE="loopback"))

    identity = policy.authenticate_loopback(peer_host)

    assert identity is not None
    assert identity.mechanism is AuthenticationMechanism.LOOPBACK


@pytest.mark.parametrize(
    "peer_host",
    [None, "", "127.0.0.2", "172.20.0.8", "192.168.1.4", "localhost"],
)
def test_loopback_mode_rejects_every_other_peer(peer_host: str | None) -> None:
    policy = load_authentication_policy(AppSettings(ASSISTANTMD_AUTH_MODE="loopback"))

    assert policy.authenticate_loopback(peer_host) is None


def test_owner_mode_accepts_only_the_configured_bearer() -> None:
    policy = load_authentication_policy(
        AppSettings(
            ASSISTANTMD_AUTH_MODE="owner_token",
            ASSISTANTMD_AUTH_SECRET=_SECRET,
        )
    )

    identity = policy.authenticate_owner_bearer(_SECRET)

    assert identity is not None
    assert identity.mechanism is AuthenticationMechanism.OWNER_BEARER
    assert policy.authenticate_owner_bearer("b" * 32) is None
    assert policy.authenticate_owner_bearer(None) is None
    assert _SECRET not in repr(policy)


def test_owner_session_key_is_domain_separated() -> None:
    policy = load_authentication_policy(
        AppSettings(
            ASSISTANTMD_AUTH_MODE="owner_token",
            ASSISTANTMD_AUTH_SECRET=_SECRET,
        )
    )

    derived = policy.derive_session_key()

    assert len(derived) == 32
    assert derived != _SECRET.encode("ascii")


def test_trusted_proxy_checks_assertion_and_immediate_peer() -> None:
    policy = load_authentication_policy(
        AppSettings(
            ASSISTANTMD_AUTH_MODE="trusted_proxy",
            ASSISTANTMD_AUTH_SECRET=_SECRET,
            ASSISTANTMD_AUTH_TRUSTED_PROXY_NETWORKS="172.20.0.0/24,10.4.0.9/32",
        )
    )

    identity = policy.authenticate_proxy(
        assertion=_SECRET,
        peer_host="172.20.0.4",
    )

    assert identity is not None
    assert identity.mechanism is AuthenticationMechanism.TRUSTED_PROXY
    assert policy.authenticate_proxy(assertion="b" * 32, peer_host="172.20.0.4") is None
    assert policy.authenticate_proxy(assertion=_SECRET, peer_host="172.21.0.4") is None
    assert policy.authenticate_proxy(assertion=_SECRET, peer_host=None) is None


def test_trusted_proxy_can_use_assertion_without_peer_allowlist() -> None:
    policy = load_authentication_policy(
        AppSettings(
            ASSISTANTMD_AUTH_MODE="trusted_proxy",
            ASSISTANTMD_AUTH_SECRET=_SECRET,
        )
    )

    assert policy.authenticate_proxy(assertion=_SECRET, peer_host="172.30.0.5")


def test_secret_file_accepts_one_trailing_newline(tmp_path: Path) -> None:
    secret_file = tmp_path / "auth-secret"
    secret_file.write_text(f"{_SECRET}\n", encoding="ascii")

    policy = load_authentication_policy(
        AppSettings(
            ASSISTANTMD_AUTH_MODE="owner_token",
            ASSISTANTMD_AUTH_SECRET_FILE=secret_file,
        )
    )

    assert policy.authenticate_owner_bearer(_SECRET)


def test_direct_and_file_secrets_are_mutually_exclusive(tmp_path: Path) -> None:
    secret_file = tmp_path / "auth-secret"
    secret_file.write_text(_SECRET, encoding="ascii")

    with pytest.raises(AuthenticationConfigurationError, match="only one"):
        load_authentication_policy(
            AppSettings(
                ASSISTANTMD_AUTH_MODE="owner_token",
                ASSISTANTMD_AUTH_SECRET=_SECRET,
                ASSISTANTMD_AUTH_SECRET_FILE=secret_file,
            )
        )


@pytest.mark.parametrize(
    "mode",
    ["owner_token", "trusted_proxy"],
)
def test_protected_secret_modes_require_a_secret(mode: str) -> None:
    with pytest.raises(AuthenticationConfigurationError, match="requires"):
        load_authentication_policy(AppSettings(ASSISTANTMD_AUTH_MODE=mode))


@pytest.mark.parametrize("mode", ["disabled", "loopback"])
def test_secretless_modes_reject_unused_credentials(mode: str) -> None:
    with pytest.raises(AuthenticationConfigurationError, match="does not accept"):
        load_authentication_policy(
            AppSettings(
                ASSISTANTMD_AUTH_MODE=mode,
                ASSISTANTMD_AUTH_SECRET=_SECRET,
            )
        )


@pytest.mark.parametrize(
    "secret",
    ["short", "a" * 31, "a" * 32 + "\n", "é" * 32],
)
def test_invalid_secret_material_fails_closed(secret: str) -> None:
    with pytest.raises(AuthenticationConfigurationError):
        load_authentication_policy(
            AppSettings(
                ASSISTANTMD_AUTH_MODE="owner_token",
                ASSISTANTMD_AUTH_SECRET=secret,
            )
        )


def test_invalid_proxy_header_and_network_fail_closed() -> None:
    with pytest.raises(AuthenticationConfigurationError, match="header"):
        load_authentication_policy(
            AppSettings(
                ASSISTANTMD_AUTH_MODE="trusted_proxy",
                ASSISTANTMD_AUTH_SECRET=_SECRET,
                ASSISTANTMD_AUTH_PROXY_ASSERTION_HEADER="bad header",
            )
        )
    with pytest.raises(AuthenticationConfigurationError, match="network"):
        load_authentication_policy(
            AppSettings(
                ASSISTANTMD_AUTH_MODE="trusted_proxy",
                ASSISTANTMD_AUTH_SECRET=_SECRET,
                ASSISTANTMD_AUTH_TRUSTED_PROXY_NETWORKS="172.20.0.1/24",
            )
        )
