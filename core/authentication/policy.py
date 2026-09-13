"""Validated ingress-authentication configuration and pure admission policy."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import SecretStr

from core.settings import AppSettings

from .models import (
    AuthenticatedIdentity,
    AuthenticationMechanism,
    AuthenticationMode,
    local_user_identity,
)

MINIMUM_SECRET_BYTES = 32
MAXIMUM_SECRET_BYTES = 4096
DEFAULT_PROXY_ASSERTION_HEADER = "X-AssistantMD-Proxy-Assertion"
_HEADER_NAME_PATTERN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_EXACT_LOOPBACK_ADDRESSES = frozenset(
    {ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address("::1")}
)
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


class AuthenticationConfigurationError(ValueError):
    """Raised when ingress authentication cannot fail closed."""


class _SecretMaterial:
    """Bound secret operations that never expose raw credential bytes."""

    __slots__ = ("_value",)

    def __init__(self, value: bytes) -> None:
        self._value = value

    def __repr__(self) -> str:
        return "<redacted authentication secret>"

    def matches(self, candidate: str | None) -> bool:
        """Compare one supplied credential without leaking match position."""
        candidate_bytes = _candidate_bytes(candidate)
        if candidate_bytes is None:
            return False
        return hmac.compare_digest(self._value, candidate_bytes)

    def derive_key(self, purpose: bytes) -> bytes:
        """Derive domain-separated key material for a bounded auth purpose."""
        if not purpose:
            raise ValueError("Authentication key purpose cannot be empty.")
        return hmac.new(self._value, purpose, hashlib.sha256).digest()


@dataclass(frozen=True)
class AuthenticationPolicy:
    """Immutable admission policy built from validated infrastructure settings."""

    mode: AuthenticationMode
    _secret: _SecretMaterial | None = None
    proxy_assertion_header: str = DEFAULT_PROXY_ASSERTION_HEADER
    trusted_proxy_networks: tuple[IPNetwork, ...] = ()

    @property
    def is_protected(self) -> bool:
        """Return whether this policy requires authentication evidence."""
        return self.mode is not AuthenticationMode.DISABLED

    def authenticate_disabled(self) -> AuthenticatedIdentity | None:
        """Admit a request only when protection was explicitly disabled."""
        if self.mode is not AuthenticationMode.DISABLED:
            return None
        return local_user_identity(AuthenticationMechanism.DISABLED)

    def authenticate_loopback(
        self, peer_host: str | None
    ) -> AuthenticatedIdentity | None:
        """Admit only an exact IPv4 or IPv6 loopback socket peer."""
        if self.mode is not AuthenticationMode.LOOPBACK:
            return None
        peer = _parse_ip_address(peer_host)
        if peer not in _EXACT_LOOPBACK_ADDRESSES:
            return None
        return local_user_identity(AuthenticationMechanism.LOOPBACK)

    def authenticate_proxy(
        self,
        *,
        assertion: str | None,
        peer_host: str | None,
    ) -> AuthenticatedIdentity | None:
        """Admit a valid proxy assertion from an allowed immediate peer."""
        if self.mode is not AuthenticationMode.TRUSTED_PROXY:
            return None
        if not self._peer_is_trusted_proxy(peer_host):
            return None
        if self._secret is None or not self._secret.matches(assertion):
            return None
        return local_user_identity(AuthenticationMechanism.TRUSTED_PROXY)

    def authenticate_owner_bearer(
        self, bearer_token: str | None
    ) -> AuthenticatedIdentity | None:
        """Admit a direct bearer credential only in owner-token mode."""
        if self.mode is not AuthenticationMode.OWNER_TOKEN:
            return None
        if self._secret is None or not self._secret.matches(bearer_token):
            return None
        return local_user_identity(AuthenticationMechanism.OWNER_BEARER)

    def derive_session_key(self) -> bytes:
        """Derive the owner-session signing key without returning the owner token."""
        if self.mode is not AuthenticationMode.OWNER_TOKEN or self._secret is None:
            raise AuthenticationConfigurationError(
                "Owner session keys require owner-token authentication mode."
            )
        return self._secret.derive_key(b"assistantmd-owner-session-v1")

    def _peer_is_trusted_proxy(self, peer_host: str | None) -> bool:
        if not self.trusted_proxy_networks:
            return True
        peer = _parse_ip_address(peer_host)
        if peer is None:
            return False
        return any(peer in network for network in self.trusted_proxy_networks)


def load_authentication_policy(settings: AppSettings) -> AuthenticationPolicy:
    """Build a fail-closed authentication policy from infrastructure settings."""
    mode = _parse_mode(settings.auth_mode)
    secret = _load_configured_secret(
        direct_secret=settings.auth_secret,
        secret_file=settings.auth_secret_file,
    )
    trusted_networks = _parse_networks(settings.auth_trusted_proxy_networks)
    header = _validate_header_name(settings.auth_proxy_assertion_header)

    if mode in {AuthenticationMode.DISABLED, AuthenticationMode.LOOPBACK}:
        if secret is not None:
            raise AuthenticationConfigurationError(
                f"Authentication mode '{mode.value}' does not accept a secret."
            )
        if trusted_networks:
            raise AuthenticationConfigurationError(
                f"Authentication mode '{mode.value}' does not accept trusted proxy networks."
            )
        return AuthenticationPolicy(mode=mode, proxy_assertion_header=header)

    if secret is None:
        raise AuthenticationConfigurationError(
            f"Authentication mode '{mode.value}' requires an authentication secret."
        )
    if mode is AuthenticationMode.OWNER_TOKEN and trusted_networks:
        raise AuthenticationConfigurationError(
            "Owner-token mode does not accept trusted proxy networks."
        )
    return AuthenticationPolicy(
        mode=mode,
        _secret=secret,
        proxy_assertion_header=header,
        trusted_proxy_networks=trusted_networks,
    )


def _parse_mode(raw_mode: str | None) -> AuthenticationMode:
    normalized = (raw_mode or "").strip()
    if not normalized:
        raise AuthenticationConfigurationError(
            "ASSISTANTMD_AUTH_MODE must explicitly select disabled, loopback, "
            "trusted_proxy, or owner_token."
        )
    try:
        return AuthenticationMode(normalized)
    except ValueError as exc:
        raise AuthenticationConfigurationError(
            "ASSISTANTMD_AUTH_MODE must be disabled, loopback, trusted_proxy, "
            "or owner_token."
        ) from exc


def _load_configured_secret(
    *, direct_secret: SecretStr | None, secret_file: Path | None
) -> _SecretMaterial | None:
    if direct_secret is not None and secret_file is not None:
        raise AuthenticationConfigurationError(
            "Configure only one of ASSISTANTMD_AUTH_SECRET and "
            "ASSISTANTMD_AUTH_SECRET_FILE."
        )
    if direct_secret is not None:
        raw_secret = direct_secret.get_secret_value().encode("utf-8")
        return _SecretMaterial(_validate_secret(raw_secret))
    if secret_file is None:
        return None
    try:
        with secret_file.open("rb") as handle:
            raw_secret = handle.read(MAXIMUM_SECRET_BYTES + 2)
    except OSError as exc:
        raise AuthenticationConfigurationError(
            f"Unable to read authentication secret file: {secret_file}"
        ) from exc
    if raw_secret.endswith(b"\r\n"):
        raw_secret = raw_secret[:-2]
    elif raw_secret.endswith(b"\n"):
        raw_secret = raw_secret[:-1]
    return _SecretMaterial(_validate_secret(raw_secret))


def _validate_secret(value: bytes) -> bytes:
    if len(value) < MINIMUM_SECRET_BYTES:
        raise AuthenticationConfigurationError(
            f"Authentication secrets must contain at least {MINIMUM_SECRET_BYTES} bytes."
        )
    if len(value) > MAXIMUM_SECRET_BYTES:
        raise AuthenticationConfigurationError(
            f"Authentication secrets cannot exceed {MAXIMUM_SECRET_BYTES} bytes."
        )
    if any(byte < 0x21 or byte > 0x7E for byte in value):
        raise AuthenticationConfigurationError(
            "Authentication secrets must contain only visible ASCII characters."
        )
    return value


def _candidate_bytes(candidate: str | None) -> bytes | None:
    if candidate is None:
        return None
    try:
        encoded = candidate.encode("ascii")
    except UnicodeEncodeError:
        return None
    if not (MINIMUM_SECRET_BYTES <= len(encoded) <= MAXIMUM_SECRET_BYTES):
        return None
    return encoded


def _validate_header_name(raw_name: str) -> str:
    name = raw_name.strip()
    if not name or not _HEADER_NAME_PATTERN.fullmatch(name):
        raise AuthenticationConfigurationError(
            "ASSISTANTMD_AUTH_PROXY_ASSERTION_HEADER must be a valid HTTP header name."
        )
    return name


def _parse_networks(raw_networks: str | None) -> tuple[IPNetwork, ...]:
    if raw_networks is None or not raw_networks.strip():
        return ()
    networks: list[IPNetwork] = []
    for raw_network in raw_networks.split(","):
        value = raw_network.strip()
        if not value:
            raise AuthenticationConfigurationError(
                "Trusted proxy networks cannot contain an empty entry."
            )
        try:
            networks.append(ipaddress.ip_network(value, strict=True))
        except ValueError as exc:
            raise AuthenticationConfigurationError(
                f"Invalid trusted proxy network: {value}"
            ) from exc
    return tuple(networks)


def _parse_ip_address(raw_address: str | None) -> IPAddress | None:
    if raw_address is None:
        return None
    try:
        return ipaddress.ip_address(raw_address)
    except ValueError:
        return None
