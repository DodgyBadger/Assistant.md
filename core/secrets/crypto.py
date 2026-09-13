"""Authenticated-encryption primitives for principal-owned secrets."""

from __future__ import annotations

import base64
import binascii
import json
import os
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SECRET_KEY_ENV = "ASSISTANTMD_SECRETS_KEY"
LEGACY_KEYRING_ENV = "ASSISTANTMD_SECRETS_KEYS"
LEGACY_ACTIVE_KEY_VERSION_ENV = "ASSISTANTMD_SECRETS_ACTIVE_KEY_VERSION"
ENVELOPE_VERSION = 1
NONCE_BYTES = 12


class SecretIntegrityError(RuntimeError):
    """Raised when encrypted secret material cannot be authenticated."""


@dataclass(frozen=True)
class EncryptedValue:
    """One authenticated ciphertext envelope."""

    envelope_version: int
    key_version: int
    nonce: bytes
    ciphertext: bytes


@dataclass(frozen=True)
class SecretKeyring:
    """Validated versioned AES-256 keys and one active write version."""

    keys: Mapping[int, bytes]
    active_version: int

    def __post_init__(self) -> None:
        normalized: dict[int, bytes] = {}
        for version, key in self.keys.items():
            if (
                not isinstance(version, int)
                or isinstance(version, bool)
                or version <= 0
            ):
                raise ValueError("Secret key versions must be positive integers.")
            key_bytes = bytes(key)
            if len(key_bytes) != 32:
                raise ValueError("Every secret encryption key must contain 32 bytes.")
            normalized[version] = key_bytes
        if not normalized:
            raise ValueError("At least one secret encryption key is required.")
        if self.active_version not in normalized:
            raise ValueError("The active secret key version is not in the keyring.")
        object.__setattr__(self, "keys", MappingProxyType(normalized))

    @classmethod
    def from_environment(cls) -> SecretKeyring:
        """Parse and validate the installation keyring from environment variables."""
        raw_key = os.environ.get(SECRET_KEY_ENV)
        if raw_key is not None:
            try:
                return cls(keys={1: _decode_key(raw_key)}, active_version=1)
            except (ValueError, binascii.Error) as exc:
                raise RuntimeError(
                    "Secret encryption configuration is malformed. Restore a valid "
                    "installation key or reinitialize secrets."
                ) from exc

        raw_keyring = os.environ.get(LEGACY_KEYRING_ENV)
        raw_active = os.environ.get(LEGACY_ACTIVE_KEY_VERSION_ENV)
        if not raw_keyring or not raw_active:
            raise RuntimeError(
                "Secret encryption is not configured. Restore the installation "
                "keyring or reinitialize secrets."
            )
        try:
            payload = json.loads(raw_keyring)
            if not isinstance(payload, dict):
                raise TypeError
            keys = {
                int(version): _decode_key(value)
                for version, value in payload.items()
                if isinstance(version, str) and isinstance(value, str)
            }
            if len(keys) != len(payload):
                raise ValueError
            active_version = int(raw_active)
        except (TypeError, ValueError, binascii.Error, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "Secret encryption configuration is malformed. Restore a valid "
                "installation keyring or reinitialize secrets."
            ) from exc
        return cls(keys=keys, active_version=active_version)

    def encrypt(
        self,
        value: str,
        *,
        owner_principal_id: str,
        namespace: str,
        name: str,
    ) -> EncryptedValue:
        """Encrypt text using the active key and identity-bound AAD."""
        nonce = os.urandom(NONCE_BYTES)
        aad = _build_aad(
            owner_principal_id=owner_principal_id,
            namespace=namespace,
            name=name,
            key_version=self.active_version,
        )
        ciphertext = AESGCM(self.keys[self.active_version]).encrypt(
            nonce, value.encode("utf-8"), aad
        )
        return EncryptedValue(
            envelope_version=ENVELOPE_VERSION,
            key_version=self.active_version,
            nonce=nonce,
            ciphertext=ciphertext,
        )

    def decrypt(
        self,
        encrypted: EncryptedValue,
        *,
        owner_principal_id: str,
        namespace: str,
        name: str,
    ) -> str:
        """Authenticate and decrypt one identity-bound envelope."""
        if encrypted.envelope_version != ENVELOPE_VERSION:
            raise SecretIntegrityError("Secret envelope version is not supported.")
        key = self.keys.get(encrypted.key_version)
        if key is None:
            raise SecretIntegrityError(
                "A required secret encryption key version is unavailable."
            )
        aad = _build_aad(
            owner_principal_id=owner_principal_id,
            namespace=namespace,
            name=name,
            key_version=encrypted.key_version,
        )
        try:
            plaintext = AESGCM(key).decrypt(encrypted.nonce, encrypted.ciphertext, aad)
            return plaintext.decode("utf-8")
        except (InvalidTag, UnicodeDecodeError) as exc:
            raise SecretIntegrityError(
                "Encrypted secret authentication failed. Restore the matching "
                "database and installation keyring or re-enter secrets."
            ) from exc


def _decode_key(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)


def _build_aad(
    *, owner_principal_id: str, namespace: str, name: str, key_version: int
) -> bytes:
    fields = (
        b"assistantmd-secret",
        str(ENVELOPE_VERSION).encode("ascii"),
        owner_principal_id.encode("utf-8"),
        namespace.encode("utf-8"),
        name.encode("utf-8"),
        str(key_version).encode("ascii"),
    )
    return b"".join(struct.pack(">I", len(field)) + field for field in fields)
