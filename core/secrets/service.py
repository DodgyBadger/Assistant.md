"""Synchronous principal-aware encrypted secret operations."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from hmac import compare_digest
from typing import cast

from core.identity import ExecutionAuthority, require_current_execution_authority

from .crypto import EncryptedValue, SecretKeyring
from .schema import connect_secrets, ensure_secrets_schema


@dataclass(frozen=True)
class SecretMetadata:
    """Non-sensitive metadata for one stored secret."""

    namespace: str
    name: str
    has_value: bool = True


class SecretGuardMismatchError(RuntimeError):
    """Raised when an encrypted guard is missing or no longer matches."""


@dataclass(frozen=True)
class SecretWrite:
    """One trusted principal-owned value for an atomic batch write."""

    authority: ExecutionAuthority
    namespace: str
    name: str
    value: str


@dataclass(frozen=True)
class SecretIdentity:
    """One principal-relative encrypted secret identity."""

    namespace: str
    name: str


@dataclass(frozen=True)
class SecretRelocation:
    """Move one encrypted value to a new AAD-bound identity atomically."""

    source: SecretIdentity
    destination: SecretIdentity
    overwrite: bool = False


@dataclass(frozen=True)
class SecretCopy:
    """Copy one encrypted value to a new AAD-bound identity atomically."""

    source: SecretIdentity
    destination: SecretIdentity
    overwrite: bool = False


@dataclass(frozen=True)
class SecretNamespaceDeletion:
    """Delete every principal-owned value in one exact namespace atomically."""

    namespace: str


@dataclass(frozen=True)
class SecretMutationResult:
    """Counts from one committed atomic secret mutation."""

    relocated_count: int
    copied_count: int
    deleted_count: int


class EncryptedSecretsService:
    """Store secrets under the current execution principal."""

    def __init__(
        self,
        *,
        system_root: str,
        keyring: SecretKeyring,
        initialize_schema: bool = True,
    ) -> None:
        self._system_root = system_root
        self._keyring = keyring
        if initialize_schema:
            ensure_secrets_schema(system_root)

    def get(self, namespace: str, name: str) -> str | None:
        """Return a current-principal secret without exposing other owners."""
        return self.get_for_authority(
            require_current_execution_authority(), namespace, name
        )

    def set(self, namespace: str, name: str, value: str) -> None:
        """Create or replace a current-principal secret."""
        self.set_for_authority(
            require_current_execution_authority(), namespace, name, value
        )

    def delete(self, namespace: str, name: str) -> bool:
        """Delete a current-principal secret if present."""
        return self.delete_for_authority(
            require_current_execution_authority(), namespace, name
        )

    def list_metadata(self, namespace: str | None = None) -> list[SecretMetadata]:
        """List non-sensitive metadata for the current principal."""
        return self.list_metadata_for_authority(
            require_current_execution_authority(), namespace
        )

    def get_for_authority(
        self, authority: ExecutionAuthority, namespace: str, name: str
    ) -> str | None:
        """Return a secret for an explicitly captured trusted authority."""
        namespace, name = _normalize_identity(namespace, name)
        conn = connect_secrets(self._system_root)
        try:
            row = conn.execute(
                """
                SELECT envelope_version, key_version, nonce, ciphertext
                FROM encrypted_secrets
                WHERE owner_principal_id = ? AND namespace = ? AND name = ?
                """,
                (authority.principal_id, namespace, name),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return self._decrypt_row(
            row,
            owner_principal_id=authority.principal_id,
            namespace=namespace,
            name=name,
        )

    def get_for_authority_on_connection(
        self,
        conn: sqlite3.Connection,
        authority: ExecutionAuthority,
        namespace: str,
        name: str,
    ) -> str | None:
        """Read a secret through a caller-owned access transaction."""
        namespace, name = _normalize_identity(namespace, name)
        row = self._select_row(
            conn,
            owner_principal_id=authority.principal_id,
            namespace=namespace,
            name=name,
        )
        if row is None:
            return None
        return self._decrypt_row(
            row,
            owner_principal_id=authority.principal_id,
            namespace=namespace,
            name=name,
        )

    def set_for_authority_on_connection(
        self,
        conn: sqlite3.Connection,
        authority: ExecutionAuthority,
        namespace: str,
        name: str,
        value: str,
    ) -> None:
        """Encrypt and verify a secret in a caller-owned access transaction."""
        namespace, name = _normalize_identity(namespace, name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Secret value must be a non-empty string.")
        encrypted = self._keyring.encrypt(
            value,
            owner_principal_id=authority.principal_id,
            namespace=namespace,
            name=name,
        )
        self._upsert_encrypted(
            conn,
            owner_principal_id=authority.principal_id,
            namespace=namespace,
            name=name,
            encrypted=encrypted,
        )
        self._verify_value(
            conn,
            authority=authority,
            identity=SecretIdentity(namespace, name),
            expected=value,
        )

    def delete_for_authority_on_connection(
        self,
        conn: sqlite3.Connection,
        authority: ExecutionAuthority,
        namespace: str,
        name: str,
    ) -> bool:
        """Delete a secret in a caller-owned access transaction."""
        identity = SecretIdentity(*_normalize_identity(namespace, name))
        return bool(self._delete_identity(conn, authority=authority, identity=identity))

    def delete_namespace_for_authority_on_connection(
        self,
        conn: sqlite3.Connection,
        authority: ExecutionAuthority,
        namespace: str,
    ) -> int:
        """Delete one exact encrypted namespace in a caller-owned transaction."""
        cursor = conn.execute(
            """DELETE FROM encrypted_secrets
            WHERE owner_principal_id = ? AND namespace = ?""",
            (authority.principal_id, _normalize_namespace(namespace)),
        )
        return cursor.rowcount

    def set_for_authority(
        self,
        authority: ExecutionAuthority,
        namespace: str,
        name: str,
        value: str,
    ) -> None:
        """Write a secret for an explicitly captured trusted authority."""
        namespace, name = _normalize_identity(namespace, name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Secret value must be a non-empty string.")
        encrypted = self._keyring.encrypt(
            value,
            owner_principal_id=authority.principal_id,
            namespace=namespace,
            name=name,
        )
        conn = connect_secrets(self._system_root)
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO encrypted_secrets (
                        owner_principal_id, namespace, name, envelope_version,
                        key_version, nonce, ciphertext
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(owner_principal_id, namespace, name) DO UPDATE SET
                        envelope_version = excluded.envelope_version,
                        key_version = excluded.key_version,
                        nonce = excluded.nonce,
                        ciphertext = excluded.ciphertext,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        authority.principal_id,
                        namespace,
                        name,
                        encrypted.envelope_version,
                        encrypted.key_version,
                        encrypted.nonce,
                        encrypted.ciphertext,
                    ),
                )
        finally:
            conn.close()

    def delete_for_authority(
        self, authority: ExecutionAuthority, namespace: str, name: str
    ) -> bool:
        """Delete a secret for an explicitly captured trusted authority."""
        namespace, name = _normalize_identity(namespace, name)
        conn = connect_secrets(self._system_root)
        try:
            with conn:
                cursor = conn.execute(
                    """
                    DELETE FROM encrypted_secrets
                    WHERE owner_principal_id = ? AND namespace = ? AND name = ?
                    """,
                    (authority.principal_id, namespace, name),
                )
                return cursor.rowcount > 0
        finally:
            conn.close()

    def guarded_set_for_authority(
        self,
        authority: ExecutionAuthority,
        *,
        guard: SecretIdentity,
        expected_guard_value: str,
        target: SecretIdentity,
        value: str,
        additional_guards: tuple[tuple[SecretIdentity, str], ...] = (),
    ) -> None:
        """Set a value only while an authenticated encrypted guard matches."""
        prepared_guard = SecretIdentity(
            *_normalize_identity(guard.namespace, guard.name)
        )
        prepared_target = SecretIdentity(
            *_normalize_identity(target.namespace, target.name)
        )
        if not isinstance(expected_guard_value, str) or not expected_guard_value:
            raise ValueError("Expected secret guard value must be a non-empty string.")
        prepared_guards = ((prepared_guard, expected_guard_value),) + tuple(
            (
                SecretIdentity(*_normalize_identity(identity.namespace, identity.name)),
                expected,
            )
            for identity, expected in additional_guards
        )
        if len({identity for identity, _expected in prepared_guards}) != len(
            prepared_guards
        ):
            raise ValueError("Secret guards must use unique identities.")
        if any(
            not isinstance(expected, str) or not expected
            for _identity, expected in prepared_guards
        ):
            raise ValueError("Expected secret guard value must be a non-empty string.")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Secret value must be a non-empty string.")
        encrypted = self._keyring.encrypt(
            value,
            owner_principal_id=authority.principal_id,
            namespace=prepared_target.namespace,
            name=prepared_target.name,
        )
        conn = connect_secrets(self._system_root)
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                for guard_identity, expected in prepared_guards:
                    guard_row = self._select_row(
                        conn,
                        owner_principal_id=authority.principal_id,
                        namespace=guard_identity.namespace,
                        name=guard_identity.name,
                    )
                    if guard_row is None:
                        raise SecretGuardMismatchError(
                            "Encrypted secret guard mismatch."
                        )
                    actual_guard_value = self._decrypt_row(
                        guard_row,
                        owner_principal_id=authority.principal_id,
                        namespace=guard_identity.namespace,
                        name=guard_identity.name,
                    )
                    if not compare_digest(actual_guard_value, expected):
                        raise SecretGuardMismatchError(
                            "Encrypted secret guard mismatch."
                        )
                self._upsert_encrypted(
                    conn,
                    owner_principal_id=authority.principal_id,
                    namespace=prepared_target.namespace,
                    name=prepared_target.name,
                    encrypted=encrypted,
                )
                self._verify_value(
                    conn,
                    authority=authority,
                    identity=prepared_target,
                    expected=value,
                )
        finally:
            conn.close()

    def guarded_delete_for_authority(
        self,
        authority: ExecutionAuthority,
        *,
        guard: SecretIdentity,
        expected_guard_value: str,
        target: SecretIdentity,
    ) -> bool:
        """Delete a value only while an authenticated encrypted guard matches."""
        prepared_guard = SecretIdentity(
            *_normalize_identity(guard.namespace, guard.name)
        )
        prepared_target = SecretIdentity(
            *_normalize_identity(target.namespace, target.name)
        )
        if not isinstance(expected_guard_value, str) or not expected_guard_value:
            raise ValueError("Expected secret guard value must be a non-empty string.")
        conn = connect_secrets(self._system_root)
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                guard_row = self._select_row(
                    conn,
                    owner_principal_id=authority.principal_id,
                    namespace=prepared_guard.namespace,
                    name=prepared_guard.name,
                )
                if guard_row is None:
                    raise SecretGuardMismatchError("Encrypted secret guard mismatch.")
                actual_guard_value = self._decrypt_row(
                    guard_row,
                    owner_principal_id=authority.principal_id,
                    namespace=prepared_guard.namespace,
                    name=prepared_guard.name,
                )
                if not compare_digest(actual_guard_value, expected_guard_value):
                    raise SecretGuardMismatchError("Encrypted secret guard mismatch.")
                return bool(
                    self._delete_identity(
                        conn,
                        authority=authority,
                        identity=prepared_target,
                    )
                )
        finally:
            conn.close()

    def replace_and_delete_for_authority(
        self,
        authority: ExecutionAuthority,
        *,
        target: SecretIdentity,
        value: str,
        deletions: tuple[SecretIdentity, ...],
        expected_value: str | None = None,
    ) -> int:
        """Replace one value and delete related identities atomically."""
        prepared_target = SecretIdentity(
            *_normalize_identity(target.namespace, target.name)
        )
        prepared_deletions = tuple(
            SecretIdentity(*_normalize_identity(item.namespace, item.name))
            for item in deletions
        )
        if prepared_target in prepared_deletions:
            raise ValueError("Replacement target cannot also be deleted.")
        if len(set(prepared_deletions)) != len(prepared_deletions):
            raise ValueError("Secret deletions must use unique identities.")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Secret value must be a non-empty string.")
        if expected_value is not None and (
            not isinstance(expected_value, str) or not expected_value
        ):
            raise ValueError("Expected secret value must be a non-empty string.")
        encrypted = self._keyring.encrypt(
            value,
            owner_principal_id=authority.principal_id,
            namespace=prepared_target.namespace,
            name=prepared_target.name,
        )
        deleted_count = 0
        conn = connect_secrets(self._system_root)
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                if expected_value is not None:
                    current_row = self._select_row(
                        conn,
                        owner_principal_id=authority.principal_id,
                        namespace=prepared_target.namespace,
                        name=prepared_target.name,
                    )
                    if current_row is None:
                        raise SecretGuardMismatchError(
                            "Encrypted secret guard mismatch."
                        )
                    current_value = self._decrypt_row(
                        current_row,
                        owner_principal_id=authority.principal_id,
                        namespace=prepared_target.namespace,
                        name=prepared_target.name,
                    )
                    if not compare_digest(current_value, expected_value):
                        raise SecretGuardMismatchError(
                            "Encrypted secret guard mismatch."
                        )
                self._upsert_encrypted(
                    conn,
                    owner_principal_id=authority.principal_id,
                    namespace=prepared_target.namespace,
                    name=prepared_target.name,
                    encrypted=encrypted,
                )
                self._verify_value(
                    conn,
                    authority=authority,
                    identity=prepared_target,
                    expected=value,
                )
                for identity in prepared_deletions:
                    deleted_count += self._delete_identity(
                        conn,
                        authority=authority,
                        identity=identity,
                    )
        finally:
            conn.close()
        return deleted_count

    def list_metadata_for_authority(
        self, authority: ExecutionAuthority, namespace: str | None = None
    ) -> list[SecretMetadata]:
        """List non-sensitive metadata for an explicitly captured authority."""
        conn = connect_secrets(self._system_root)
        try:
            if namespace is None:
                rows = conn.execute(
                    """
                    SELECT namespace, name FROM encrypted_secrets
                    WHERE owner_principal_id = ? ORDER BY namespace, name
                    """,
                    (authority.principal_id,),
                ).fetchall()
            else:
                normalized_namespace, _ = _normalize_identity(namespace, "placeholder")
                rows = conn.execute(
                    """
                    SELECT namespace, name FROM encrypted_secrets
                    WHERE owner_principal_id = ? AND namespace = ? ORDER BY name
                    """,
                    (authority.principal_id, normalized_namespace),
                ).fetchall()
        finally:
            conn.close()
        return [
            SecretMetadata(namespace=row["namespace"], name=row["name"]) for row in rows
        ]

    def rotate_all(self) -> int:
        """Re-encrypt every non-active record atomically with the active key."""
        conn = connect_secrets(self._system_root)
        rotated = 0
        try:
            with conn:
                rows = conn.execute(
                    """
                    SELECT owner_principal_id, namespace, name, envelope_version,
                           key_version, nonce, ciphertext
                    FROM encrypted_secrets WHERE key_version != ?
                    """,
                    (self._keyring.active_version,),
                ).fetchall()
                for row in rows:
                    owner = str(row["owner_principal_id"])
                    namespace = str(row["namespace"])
                    name = str(row["name"])
                    value = self._decrypt_row(
                        row,
                        owner_principal_id=owner,
                        namespace=namespace,
                        name=name,
                    )
                    encrypted = self._keyring.encrypt(
                        value,
                        owner_principal_id=owner,
                        namespace=namespace,
                        name=name,
                    )
                    conn.execute(
                        """
                        UPDATE encrypted_secrets SET envelope_version = ?,
                            key_version = ?, nonce = ?, ciphertext = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE owner_principal_id = ? AND namespace = ? AND name = ?
                        """,
                        (
                            encrypted.envelope_version,
                            encrypted.key_version,
                            encrypted.nonce,
                            encrypted.ciphertext,
                            owner,
                            namespace,
                            name,
                        ),
                    )
                    rotated += 1
                self._verify_all(conn)
        finally:
            conn.close()
        return rotated

    def set_many_for_authorities(self, writes: list[SecretWrite]) -> int:
        """Encrypt, write, and authenticate a trusted batch in one transaction."""
        prepared: list[tuple[SecretWrite, str, str, EncryptedValue]] = []
        identities: set[tuple[str, str, str]] = set()
        for write in writes:
            namespace, name = _normalize_identity(write.namespace, write.name)
            if not isinstance(write.value, str) or not write.value.strip():
                raise ValueError("Secret value must be a non-empty string.")
            identity = (write.authority.principal_id, namespace, name)
            if identity in identities:
                raise ValueError("A secret batch cannot contain duplicate identities.")
            identities.add(identity)
            encrypted = self._keyring.encrypt(
                write.value,
                owner_principal_id=write.authority.principal_id,
                namespace=namespace,
                name=name,
            )
            prepared.append((write, namespace, name, encrypted))

        conn = connect_secrets(self._system_root)
        try:
            with conn:
                for write, namespace, name, encrypted in prepared:
                    self._upsert_encrypted(
                        conn,
                        owner_principal_id=write.authority.principal_id,
                        namespace=namespace,
                        name=name,
                        encrypted=encrypted,
                    )
                for write, namespace, name, _encrypted in prepared:
                    row = self._select_row(
                        conn,
                        owner_principal_id=write.authority.principal_id,
                        namespace=namespace,
                        name=name,
                    )
                    if row is None:
                        raise RuntimeError("Imported secret verification failed.")
                    value = self._decrypt_row(
                        row,
                        owner_principal_id=write.authority.principal_id,
                        namespace=namespace,
                        name=name,
                    )
                    if value != write.value:
                        raise RuntimeError("Imported secret verification failed.")
        finally:
            conn.close()
        return len(prepared)

    def mutate_for_authority(
        self,
        authority: ExecutionAuthority,
        *,
        copies: tuple[SecretCopy, ...] = (),
        relocations: tuple[SecretRelocation, ...] = (),
        deletions: tuple[SecretIdentity, ...] = (),
        namespace_deletions: tuple[SecretNamespaceDeletion, ...] = (),
    ) -> SecretMutationResult:
        """Copy, relocate, and delete principal-owned secrets atomically."""
        prepared_copies = tuple(
            SecretCopy(
                source=SecretIdentity(
                    *_normalize_identity(item.source.namespace, item.source.name)
                ),
                destination=SecretIdentity(
                    *_normalize_identity(
                        item.destination.namespace,
                        item.destination.name,
                    )
                ),
                overwrite=item.overwrite,
            )
            for item in copies
        )
        prepared_relocations = tuple(
            SecretRelocation(
                source=SecretIdentity(
                    *_normalize_identity(
                        item.source.namespace,
                        item.source.name,
                    )
                ),
                destination=SecretIdentity(
                    *_normalize_identity(
                        item.destination.namespace,
                        item.destination.name,
                    )
                ),
                overwrite=item.overwrite,
            )
            for item in relocations
        )
        prepared_deletions = tuple(
            SecretIdentity(*_normalize_identity(item.namespace, item.name))
            for item in deletions
        )
        prepared_namespace_deletions = tuple(
            SecretNamespaceDeletion(_normalize_namespace(item.namespace))
            for item in namespace_deletions
        )
        _validate_secret_mutation(
            prepared_copies,
            prepared_relocations,
            prepared_deletions,
            prepared_namespace_deletions,
        )

        relocated_count = 0
        copied_count = 0
        deleted_count = 0
        conn = connect_secrets(self._system_root)
        try:
            with conn:
                for copy in prepared_copies:
                    if self._copy_secret(
                        conn,
                        authority=authority,
                        source=copy.source,
                        destination=copy.destination,
                        overwrite=copy.overwrite,
                    ):
                        copied_count += 1
                for relocation in prepared_relocations:
                    source = self._select_row(
                        conn,
                        owner_principal_id=authority.principal_id,
                        namespace=relocation.source.namespace,
                        name=relocation.source.name,
                    )
                    if source is None:
                        continue
                    destination = self._select_row(
                        conn,
                        owner_principal_id=authority.principal_id,
                        namespace=relocation.destination.namespace,
                        name=relocation.destination.name,
                    )
                    if destination is None or relocation.overwrite:
                        value = self._decrypt_row(
                            source,
                            owner_principal_id=authority.principal_id,
                            namespace=relocation.source.namespace,
                            name=relocation.source.name,
                        )
                        encrypted = self._keyring.encrypt(
                            value,
                            owner_principal_id=authority.principal_id,
                            namespace=relocation.destination.namespace,
                            name=relocation.destination.name,
                        )
                        self._upsert_encrypted(
                            conn,
                            owner_principal_id=authority.principal_id,
                            namespace=relocation.destination.namespace,
                            name=relocation.destination.name,
                            encrypted=encrypted,
                        )
                        self._verify_value(
                            conn,
                            authority=authority,
                            identity=relocation.destination,
                            expected=value,
                        )
                    else:
                        self._decrypt_row(
                            destination,
                            owner_principal_id=authority.principal_id,
                            namespace=relocation.destination.namespace,
                            name=relocation.destination.name,
                        )
                    deleted_count += self._delete_identity(
                        conn, authority=authority, identity=relocation.source
                    )
                    relocated_count += 1
                for identity in prepared_deletions:
                    deleted_count += self._delete_identity(
                        conn, authority=authority, identity=identity
                    )
                for namespace_deletion in prepared_namespace_deletions:
                    cursor = conn.execute(
                        """
                        DELETE FROM encrypted_secrets
                        WHERE owner_principal_id = ? AND namespace = ?
                        """,
                        (authority.principal_id, namespace_deletion.namespace),
                    )
                    deleted_count += cursor.rowcount
        finally:
            conn.close()
        return SecretMutationResult(
            relocated_count=relocated_count,
            copied_count=copied_count,
            deleted_count=deleted_count,
        )

    def verify_all(self) -> None:
        """Authenticate every stored record without returning secret values."""
        conn = connect_secrets(self._system_root)
        try:
            self._verify_all(conn)
        finally:
            conn.close()

    def _verify_all(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            """
            SELECT owner_principal_id, namespace, name, envelope_version,
                   key_version, nonce, ciphertext FROM encrypted_secrets
            """
        ).fetchall()
        for row in rows:
            self._decrypt_row(
                row,
                owner_principal_id=str(row["owner_principal_id"]),
                namespace=str(row["namespace"]),
                name=str(row["name"]),
            )

    @staticmethod
    def _select_row(
        conn: sqlite3.Connection,
        *,
        owner_principal_id: str,
        namespace: str,
        name: str,
    ) -> sqlite3.Row | None:
        row = conn.execute(
            """
            SELECT envelope_version, key_version, nonce, ciphertext
            FROM encrypted_secrets
            WHERE owner_principal_id = ? AND namespace = ? AND name = ?
            """,
            (owner_principal_id, namespace, name),
        ).fetchone()
        return cast(sqlite3.Row | None, row)

    @staticmethod
    def _upsert_encrypted(
        conn: sqlite3.Connection,
        *,
        owner_principal_id: str,
        namespace: str,
        name: str,
        encrypted: EncryptedValue,
    ) -> None:
        conn.execute(
            """
            INSERT INTO encrypted_secrets (
                owner_principal_id, namespace, name, envelope_version,
                key_version, nonce, ciphertext
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner_principal_id, namespace, name) DO UPDATE SET
                envelope_version = excluded.envelope_version,
                key_version = excluded.key_version,
                nonce = excluded.nonce,
                ciphertext = excluded.ciphertext,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                owner_principal_id,
                namespace,
                name,
                encrypted.envelope_version,
                encrypted.key_version,
                encrypted.nonce,
                encrypted.ciphertext,
            ),
        )

    def _decrypt_row(
        self,
        row: sqlite3.Row,
        *,
        owner_principal_id: str,
        namespace: str,
        name: str,
    ) -> str:
        encrypted = EncryptedValue(
            envelope_version=int(row["envelope_version"]),
            key_version=int(row["key_version"]),
            nonce=bytes(row["nonce"]),
            ciphertext=bytes(row["ciphertext"]),
        )
        return self._keyring.decrypt(
            encrypted,
            owner_principal_id=owner_principal_id,
            namespace=namespace,
            name=name,
        )

    def _verify_value(
        self,
        conn: sqlite3.Connection,
        *,
        authority: ExecutionAuthority,
        identity: SecretIdentity,
        expected: str,
    ) -> None:
        row = self._select_row(
            conn,
            owner_principal_id=authority.principal_id,
            namespace=identity.namespace,
            name=identity.name,
        )
        if (
            row is None
            or self._decrypt_row(
                row,
                owner_principal_id=authority.principal_id,
                namespace=identity.namespace,
                name=identity.name,
            )
            != expected
        ):
            raise RuntimeError("Relocated secret verification failed.")

    def _copy_secret(
        self,
        conn: sqlite3.Connection,
        *,
        authority: ExecutionAuthority,
        source: SecretIdentity,
        destination: SecretIdentity,
        overwrite: bool,
    ) -> bool:
        source_row = self._select_row(
            conn,
            owner_principal_id=authority.principal_id,
            namespace=source.namespace,
            name=source.name,
        )
        if source_row is None:
            return False
        destination_row = self._select_row(
            conn,
            owner_principal_id=authority.principal_id,
            namespace=destination.namespace,
            name=destination.name,
        )
        if destination_row is not None and not overwrite:
            self._decrypt_row(
                destination_row,
                owner_principal_id=authority.principal_id,
                namespace=destination.namespace,
                name=destination.name,
            )
            return True
        value = self._decrypt_row(
            source_row,
            owner_principal_id=authority.principal_id,
            namespace=source.namespace,
            name=source.name,
        )
        encrypted = self._keyring.encrypt(
            value,
            owner_principal_id=authority.principal_id,
            namespace=destination.namespace,
            name=destination.name,
        )
        self._upsert_encrypted(
            conn,
            owner_principal_id=authority.principal_id,
            namespace=destination.namespace,
            name=destination.name,
            encrypted=encrypted,
        )
        self._verify_value(
            conn,
            authority=authority,
            identity=destination,
            expected=value,
        )
        return True

    @staticmethod
    def _delete_identity(
        conn: sqlite3.Connection,
        *,
        authority: ExecutionAuthority,
        identity: SecretIdentity,
    ) -> int:
        cursor = conn.execute(
            """
            DELETE FROM encrypted_secrets
            WHERE owner_principal_id = ? AND namespace = ? AND name = ?
            """,
            (authority.principal_id, identity.namespace, identity.name),
        )
        return cursor.rowcount


def _normalize_identity(namespace: str, name: str) -> tuple[str, str]:
    normalized_namespace = _normalize_namespace(namespace)
    normalized_name = str(name or "").strip()
    if not normalized_name:
        raise ValueError("Secret name is required.")
    if len(normalized_namespace) > 128 or len(normalized_name) > 256:
        raise ValueError("Secret namespace or name exceeds its storage limit.")
    return normalized_namespace, normalized_name


def _normalize_namespace(namespace: str) -> str:
    normalized = str(namespace or "").strip()
    if not normalized:
        raise ValueError("Secret namespace is required.")
    if len(normalized) > 128:
        raise ValueError("Secret namespace exceeds its storage limit.")
    return normalized


def _validate_secret_mutation(
    copies: tuple[SecretCopy, ...],
    relocations: tuple[SecretRelocation, ...],
    deletions: tuple[SecretIdentity, ...],
    namespace_deletions: tuple[SecretNamespaceDeletion, ...],
) -> None:
    copy_sources = {(item.source.namespace, item.source.name) for item in copies}
    copy_destinations = {
        (item.destination.namespace, item.destination.name) for item in copies
    }
    relocation_sources = {
        (item.source.namespace, item.source.name) for item in relocations
    }
    relocation_destinations = {
        (item.destination.namespace, item.destination.name) for item in relocations
    }
    sources = copy_sources | relocation_sources
    destinations = {
        *copy_destinations,
        *relocation_destinations,
    }
    deletion_identities = {(item.namespace, item.name) for item in deletions}
    deleted_namespaces = {item.namespace for item in namespace_deletions}
    operation_count = len(copies) + len(relocations)
    if len(sources) != operation_count or len(destinations) != operation_count:
        raise ValueError("Secret mutation identities must be unique.")
    if len(deletion_identities) != len(deletions):
        raise ValueError("Secret deletion identities must be unique.")
    if len(deleted_namespaces) != len(namespace_deletions):
        raise ValueError("Secret namespace deletions must be unique.")
    if any(item.source == item.destination for item in copies) or any(
        item.source == item.destination for item in relocations
    ):
        raise ValueError("Secret copy or relocation requires distinct identities.")
    if sources.intersection(destinations):
        raise ValueError("Secret relocation chains are not supported.")
    if destinations.intersection(deletion_identities):
        raise ValueError("A copied or relocated secret cannot also be deleted.")
    touched_namespaces = {
        namespace for namespace, _name in sources | destinations | deletion_identities
    }
    if touched_namespaces.intersection(deleted_namespaces):
        raise ValueError("A secret namespace deletion cannot overlap another mutation.")
