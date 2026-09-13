"""Stateless owner browser sessions and CSRF verification."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .models import (
    AuthenticatedIdentity,
    AuthenticationMechanism,
    local_user_identity,
)
from .policy import AuthenticationPolicy

SESSION_VERSION = 1
DEFAULT_SESSION_LIFETIME = timedelta(hours=12)
MAXIMUM_SESSION_TOKEN_BYTES = 4096
CSRF_TOKEN_BYTES = 32
SESSION_NONCE_BYTES = 16


class SessionVerificationError(ValueError):
    """Raised when a browser session or its CSRF proof is invalid."""


@dataclass(frozen=True)
class IssuedOwnerSession:
    """One signed browser session and its separate CSRF proof."""

    cookie_value: str
    csrf_token: str
    expires_at: datetime


@dataclass(frozen=True)
class VerifiedOwnerSession:
    """Validated non-secret claims from an owner browser session."""

    identity: AuthenticatedIdentity
    issued_at: datetime
    expires_at: datetime
    csrf_digest: bytes


class OwnerSessionCodec:
    """Issue and verify compact sessions signed with a derived owner key."""

    def __init__(
        self,
        policy: AuthenticationPolicy,
        *,
        lifetime: timedelta = DEFAULT_SESSION_LIFETIME,
    ) -> None:
        if lifetime <= timedelta(0):
            raise ValueError("Owner session lifetime must be positive.")
        self._signing_key = policy.derive_session_key()
        self._lifetime = lifetime

    def issue(self, *, now: datetime | None = None) -> IssuedOwnerSession:
        """Create one session without embedding the owner credential."""
        issued_at = _utc_datetime(now)
        expires_at = issued_at + self._lifetime
        csrf_token = secrets.token_urlsafe(CSRF_TOKEN_BYTES)
        payload = {
            "v": SESSION_VERSION,
            "iat": int(issued_at.timestamp()),
            "exp": int(expires_at.timestamp()),
            "csrf": _csrf_digest(csrf_token).hex(),
            "nonce": secrets.token_urlsafe(SESSION_NONCE_BYTES),
        }
        encoded_payload = _encode_segment(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        signature = _sign(self._signing_key, encoded_payload)
        return IssuedOwnerSession(
            cookie_value=f"{encoded_payload}.{_encode_segment(signature)}",
            csrf_token=csrf_token,
            expires_at=expires_at,
        )

    def verify(
        self,
        cookie_value: str | None,
        *,
        now: datetime | None = None,
    ) -> VerifiedOwnerSession:
        """Validate signature, schema, and lifetime for one session cookie."""
        if (
            not cookie_value
            or len(cookie_value.encode("utf-8")) > MAXIMUM_SESSION_TOKEN_BYTES
        ):
            raise SessionVerificationError("Owner session is invalid.")
        segments = cookie_value.split(".")
        if len(segments) != 2 or not all(segments):
            raise SessionVerificationError("Owner session is invalid.")
        encoded_payload, encoded_signature = segments
        signature = _decode_segment(encoded_signature)
        try:
            expected_signature = _sign(self._signing_key, encoded_payload)
        except UnicodeEncodeError as exc:
            raise SessionVerificationError("Owner session is invalid.") from exc
        if not hmac.compare_digest(signature, expected_signature):
            raise SessionVerificationError("Owner session is invalid.")
        payload = _decode_payload(encoded_payload)
        issued_at, expires_at, csrf_digest = _validate_claims(payload)
        current_time = _utc_datetime(now)
        if issued_at > current_time or expires_at <= current_time:
            raise SessionVerificationError("Owner session is invalid.")
        return VerifiedOwnerSession(
            identity=local_user_identity(AuthenticationMechanism.OWNER_SESSION),
            issued_at=issued_at,
            expires_at=expires_at,
            csrf_digest=csrf_digest,
        )

    def verify_csrf(
        self,
        session: VerifiedOwnerSession,
        csrf_token: str | None,
    ) -> None:
        """Require the separate browser CSRF proof for an ambient session."""
        if csrf_token is None:
            raise SessionVerificationError("CSRF verification failed.")
        candidate_digest = _csrf_digest(csrf_token)
        if not hmac.compare_digest(session.csrf_digest, candidate_digest):
            raise SessionVerificationError("CSRF verification failed.")


def _sign(signing_key: bytes, encoded_payload: str) -> bytes:
    return hmac.new(
        signing_key,
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()


def _csrf_digest(csrf_token: str) -> bytes:
    return hashlib.sha256(csrf_token.encode("utf-8")).digest()


def _encode_segment(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode_segment(value: str) -> bytes:
    try:
        encoded = value.encode("ascii")
        padding = b"=" * (-len(encoded) % 4)
        return base64.b64decode(encoded + padding, altchars=b"-_", validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise SessionVerificationError("Owner session is invalid.") from exc


def _decode_payload(encoded_payload: str) -> dict[str, Any]:
    try:
        decoded = _decode_segment(encoded_payload)
        payload = json.loads(decoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SessionVerificationError("Owner session is invalid.") from exc
    if not isinstance(payload, dict):
        raise SessionVerificationError("Owner session is invalid.")
    return payload


def _validate_claims(payload: dict[str, Any]) -> tuple[datetime, datetime, bytes]:
    if set(payload) != {"v", "iat", "exp", "csrf", "nonce"}:
        raise SessionVerificationError("Owner session is invalid.")
    version = payload["v"]
    issued_timestamp = payload["iat"]
    expiry_timestamp = payload["exp"]
    csrf_hex = payload["csrf"]
    nonce = payload["nonce"]
    if version != SESSION_VERSION:
        raise SessionVerificationError("Owner session is invalid.")
    if (
        not isinstance(issued_timestamp, int)
        or isinstance(issued_timestamp, bool)
        or not isinstance(expiry_timestamp, int)
        or isinstance(expiry_timestamp, bool)
        or not isinstance(csrf_hex, str)
        or not isinstance(nonce, str)
        or not nonce
    ):
        raise SessionVerificationError("Owner session is invalid.")
    try:
        issued_at = datetime.fromtimestamp(issued_timestamp, tz=UTC)
        expires_at = datetime.fromtimestamp(expiry_timestamp, tz=UTC)
        csrf_digest = bytes.fromhex(csrf_hex)
    except (OverflowError, OSError, ValueError) as exc:
        raise SessionVerificationError("Owner session is invalid.") from exc
    if expires_at <= issued_at or len(csrf_digest) != hashlib.sha256().digest_size:
        raise SessionVerificationError("Owner session is invalid.")
    return issued_at, expires_at, csrf_digest


def _utc_datetime(value: datetime | None) -> datetime:
    resolved = value or datetime.now(UTC)
    if resolved.tzinfo is None:
        raise ValueError("Owner session timestamps must be timezone-aware.")
    return resolved.astimezone(UTC)
