"""One-time transactional import from the legacy YAML secret store."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

from core.access_store import write_transaction
from core.identity import LOCAL_USER_AUTHORITY, SYSTEM_AUTHORITY, ExecutionAuthority
from core.migration_backups import (
    prepare_migration_backup_path,
)

from .schema import connect_secrets
from .service import EncryptedSecretsService, SecretWrite

MIGRATION_NAME = "legacy-secrets-yaml-v1"
LEGACY_BACKUP_FILENAME = "secrets.yaml.bak"
DEFAULT_NAMESPACE = "configuration"
SYSTEM_SECRET_NAMES = frozenset({"LOGFIRE_TOKEN"})
SKIPPED_OAUTH_SECRET_NAMES = frozenset(
    {"OPENAI_OAUTH_PENDING_STATE", "OPENAI_OAUTH_TOKEN_STATE"}
)


@dataclass(frozen=True)
class LegacySecretsMigrationResult:
    """Sanitized result of one legacy-secret migration attempt."""

    phase: str
    imported_count: int
    skipped_oauth_count: int
    source_retired: bool


def migrate_legacy_secrets_yaml(
    *, system_root: str | Path, service: EncryptedSecretsService
) -> LegacySecretsMigrationResult:
    """Import, verify, and retire legacy YAML without a runtime fallback."""
    root = Path(system_root)
    source_path = root / "secrets.yaml"
    state = _read_migration_state(root)
    if state is not None and state[0] == "complete":
        return LegacySecretsMigrationResult(
            phase="complete",
            imported_count=state[2],
            skipped_oauth_count=0,
            source_retired=not source_path.exists(),
        )

    skipped_oauth_count = 0
    if state is None:
        if not source_path.exists():
            _record_imported(root, fingerprint=None, writes=[])
        else:
            source_bytes = source_path.read_bytes()
            values = _parse_legacy_yaml(source_bytes)
            writes, skipped_oauth_count = _build_writes(values)
            _import_and_record(
                root,
                service=service,
                fingerprint=_fingerprint(source_bytes),
                writes=writes,
            )
        state = _read_migration_state(root)
        if state is None:
            raise RuntimeError("Legacy secret migration state was not recorded.")

    phase, expected_fingerprint, imported_count = state
    if phase != "imported":
        raise RuntimeError("Legacy secret migration has an invalid phase.")

    if source_path.exists():
        source_bytes = source_path.read_bytes()
        if expected_fingerprint != _fingerprint(source_bytes):
            raise RuntimeError(
                "Legacy secrets changed after import. Restore the original file or "
                "reset the incomplete migration before retrying."
            )
        values = _parse_legacy_yaml(source_bytes)
        writes, skipped_oauth_count = _build_writes(values)
        _verify_writes(service, writes)
        backup_path = prepare_migration_backup_path(root, LEGACY_BACKUP_FILENAME)
        source_path.rename(backup_path)
    else:
        _verify_recorded_items(root, service)

    _mark_complete(root)
    return LegacySecretsMigrationResult(
        phase="complete",
        imported_count=imported_count,
        skipped_oauth_count=skipped_oauth_count,
        source_retired=True,
    )


def _parse_legacy_yaml(source_bytes: bytes) -> dict[str, str]:
    payload = yaml.safe_load(source_bytes.decode("utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("Legacy secrets file must contain a mapping.")
    values: dict[str, str] = {}
    for raw_name, raw_value in payload.items():
        if not isinstance(raw_name, str):
            raise ValueError("Legacy secret names must be strings.")
        name = raw_name.strip()
        if not name:
            raise ValueError("Legacy secret names cannot be empty.")
        if raw_value is None or (isinstance(raw_value, str) and not raw_value.strip()):
            continue
        if not isinstance(raw_value, str):
            raise ValueError(f"Legacy secret '{name}' must contain a string value.")
        values[name] = raw_value
    return values


def _build_writes(values: dict[str, str]) -> tuple[list[SecretWrite], int]:
    writes: list[SecretWrite] = []
    skipped_oauth_count = 0
    for name, value in values.items():
        if name in SKIPPED_OAUTH_SECRET_NAMES:
            skipped_oauth_count += 1
            continue
        authority = (
            SYSTEM_AUTHORITY if name in SYSTEM_SECRET_NAMES else LOCAL_USER_AUTHORITY
        )
        writes.append(
            SecretWrite(
                authority=authority,
                namespace=DEFAULT_NAMESPACE,
                name=name,
                value=value,
            )
        )
    return writes, skipped_oauth_count


def _record_imported(
    root: Path, *, fingerprint: str | None, writes: list[SecretWrite]
) -> None:
    conn = connect_secrets(str(root))
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO secrets_bootstrap_migrations (
                    migration_name, phase, source_fingerprint, imported_count
                ) VALUES (?, 'imported', ?, ?)
                """,
                (MIGRATION_NAME, fingerprint, len(writes)),
            )
            conn.executemany(
                """
                INSERT INTO secrets_bootstrap_import_items (
                    migration_name, owner_principal_id, namespace, name
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        MIGRATION_NAME,
                        write.authority.principal_id,
                        write.namespace,
                        write.name,
                    )
                    for write in writes
                ],
            )
    finally:
        conn.close()


def _import_and_record(
    root: Path,
    *,
    service: EncryptedSecretsService,
    fingerprint: str,
    writes: list[SecretWrite],
) -> None:
    """Commit imported ciphertext and restart state in one access transaction."""
    with write_transaction(str(root)) as conn:
        for write in writes:
            service.set_for_authority_on_connection(
                conn,
                write.authority,
                write.namespace,
                write.name,
                write.value,
            )
        conn.execute(
            """INSERT INTO secrets_bootstrap_migrations (
                migration_name, phase, source_fingerprint, imported_count
            ) VALUES (?, 'imported', ?, ?)""",
            (MIGRATION_NAME, fingerprint, len(writes)),
        )
        conn.executemany(
            """INSERT INTO secrets_bootstrap_import_items (
                migration_name, owner_principal_id, namespace, name
            ) VALUES (?, ?, ?, ?)""",
            [
                (
                    MIGRATION_NAME,
                    write.authority.principal_id,
                    write.namespace,
                    write.name,
                )
                for write in writes
            ],
        )


def _read_migration_state(root: Path) -> tuple[str, str | None, int] | None:
    conn = connect_secrets(str(root))
    try:
        row = conn.execute(
            """
            SELECT phase, source_fingerprint, imported_count
            FROM secrets_bootstrap_migrations WHERE migration_name = ?
            """,
            (MIGRATION_NAME,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return str(row["phase"]), row["source_fingerprint"], int(row["imported_count"])


def _verify_writes(service: EncryptedSecretsService, writes: list[SecretWrite]) -> None:
    for write in writes:
        actual = service.get_for_authority(write.authority, write.namespace, write.name)
        if actual != write.value:
            raise RuntimeError("Legacy secret migration verification failed.")


def _verify_recorded_items(root: Path, service: EncryptedSecretsService) -> None:
    conn = connect_secrets(str(root))
    try:
        rows = conn.execute(
            """
            SELECT owner_principal_id, namespace, name
            FROM secrets_bootstrap_import_items WHERE migration_name = ?
            """,
            (MIGRATION_NAME,),
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        value = service.get_for_authority(
            ExecutionAuthority(str(row["owner_principal_id"])),
            str(row["namespace"]),
            str(row["name"]),
        )
        if value is None:
            raise RuntimeError("Legacy secret migration verification failed.")


def _mark_complete(root: Path) -> None:
    conn = connect_secrets(str(root))
    try:
        with conn:
            conn.execute(
                """
                UPDATE secrets_bootstrap_migrations
                SET phase = 'complete', updated_at = CURRENT_TIMESTAMP
                WHERE migration_name = ? AND phase = 'imported'
                """,
                (MIGRATION_NAME,),
            )
    finally:
        conn.close()


def _fingerprint(source_bytes: bytes) -> str:
    return hashlib.sha256(source_bytes).hexdigest()
