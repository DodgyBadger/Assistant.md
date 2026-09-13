"""Targeted contract tests for owner browser sessions and CSRF proofs."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest

from core.authentication import (
    AuthenticationMechanism,
    OwnerSessionCodec,
    SessionVerificationError,
    load_authentication_policy,
)
from core.settings import AppSettings

_SECRET = "a" * 32
_NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def _codec(
    *, secret: str = _SECRET, lifetime: timedelta = timedelta(hours=12)
) -> OwnerSessionCodec:
    policy = load_authentication_policy(
        AppSettings(
            ASSISTANTMD_AUTH_MODE="owner_token",
            ASSISTANTMD_AUTH_SECRET=secret,
        )
    )
    return OwnerSessionCodec(policy, lifetime=lifetime)


def test_session_round_trip_and_csrf_verification() -> None:
    codec = _codec()
    issued = codec.issue(now=_NOW)

    verified = codec.verify(issued.cookie_value, now=_NOW + timedelta(minutes=1))
    codec.verify_csrf(verified, issued.csrf_token)

    assert verified.identity.mechanism is AuthenticationMechanism.OWNER_SESSION
    assert verified.issued_at == _NOW
    assert verified.expires_at == _NOW + timedelta(hours=12)
    assert _SECRET not in issued.cookie_value
    assert _SECRET not in issued.csrf_token


def test_session_expiry_is_exclusive() -> None:
    codec = _codec(lifetime=timedelta(minutes=5))
    issued = codec.issue(now=_NOW)

    with pytest.raises(SessionVerificationError, match="invalid"):
        codec.verify(issued.cookie_value, now=_NOW + timedelta(minutes=5))


def test_session_is_invalid_before_issued_at() -> None:
    codec = _codec()
    issued = codec.issue(now=_NOW)

    with pytest.raises(SessionVerificationError, match="invalid"):
        codec.verify(issued.cookie_value, now=_NOW - timedelta(seconds=1))


@pytest.mark.parametrize(
    "cookie_value",
    [None, "", "one-segment", ".", "x.y.z", "é.abc", "a" * 4097],
)
def test_malformed_sessions_fail_uniformly(cookie_value: str | None) -> None:
    with pytest.raises(SessionVerificationError, match="Owner session is invalid"):
        _codec().verify(cookie_value, now=_NOW)


def test_payload_and_signature_tampering_fail() -> None:
    codec = _codec()
    issued = codec.issue(now=_NOW)
    payload, signature = issued.cookie_value.split(".")
    tampered_payload = ("A" if payload[0] != "A" else "B") + payload[1:]
    tampered_signature = ("A" if signature[0] != "A" else "B") + signature[1:]

    with pytest.raises(SessionVerificationError, match="invalid"):
        codec.verify(f"{tampered_payload}.{signature}", now=_NOW)
    with pytest.raises(SessionVerificationError, match="invalid"):
        codec.verify(f"{payload}.{tampered_signature}", now=_NOW)


def test_rotation_invalidates_existing_sessions() -> None:
    issued = _codec(secret="a" * 32).issue(now=_NOW)

    with pytest.raises(SessionVerificationError, match="invalid"):
        _codec(secret="b" * 32).verify(issued.cookie_value, now=_NOW)


@pytest.mark.parametrize("csrf_token", [None, "", "wrong-token"])
def test_csrf_proof_is_required_and_bound_to_session(csrf_token: str | None) -> None:
    codec = _codec()
    issued = codec.issue(now=_NOW)
    verified = codec.verify(issued.cookie_value, now=_NOW)

    with pytest.raises(SessionVerificationError, match="CSRF"):
        codec.verify_csrf(verified, csrf_token)


def test_csrf_token_from_another_session_is_rejected() -> None:
    codec = _codec()
    first = codec.issue(now=_NOW)
    second = codec.issue(now=_NOW)
    verified = codec.verify(first.cookie_value, now=_NOW)

    with pytest.raises(SessionVerificationError, match="CSRF"):
        codec.verify_csrf(verified, second.csrf_token)


def test_signed_payload_contains_only_bounded_non_secret_claims() -> None:
    issued = _codec().issue(now=_NOW)
    payload_segment = issued.cookie_value.split(".", maxsplit=1)[0]
    padding = "=" * (-len(payload_segment) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_segment + padding))

    assert set(payload) == {"v", "iat", "exp", "csrf", "nonce"}
    assert _SECRET not in json.dumps(payload)
    assert issued.csrf_token not in json.dumps(payload)


def test_session_lifetime_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        _codec(lifetime=timedelta(0))


def test_naive_timestamps_are_rejected() -> None:
    codec = _codec()

    with pytest.raises(ValueError, match="timezone-aware"):
        codec.issue(now=datetime(2026, 8, 31, 12, 0))
