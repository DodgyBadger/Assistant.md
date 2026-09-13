"""Durable contracts for the one-time plaintext secrets migration."""

from __future__ import annotations

import asyncio
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

_direct_run_root: tempfile.TemporaryDirectory[str] | None = None
if __name__ == "__main__":
    from core.runtime.paths import set_bootstrap_roots

    _direct_run_root = tempfile.TemporaryDirectory(
        prefix="assistantmd-secret-migration-"
    )
    direct_root = Path(_direct_run_root.name)
    data_root = direct_root / "data"
    bootstrap_system_root = direct_root / "system"
    data_root.mkdir()
    bootstrap_system_root.mkdir()
    set_bootstrap_roots(data_root=data_root, system_root=bootstrap_system_root)

from core.identity import LOCAL_USER_AUTHORITY, SYSTEM_AUTHORITY  # noqa: E402
from core.migration_backups import MIGRATION_BACKUP_DIRECTORY  # noqa: E402
from core.secrets import EncryptedSecretsService, SecretKeyring  # noqa: E402
from core.secrets.legacy_migration import (  # noqa: E402
    DEFAULT_NAMESPACE,
    LEGACY_BACKUP_FILENAME,
    migrate_legacy_secrets_yaml,
)
from validation.core.base_scenario import BaseScenario  # noqa: E402


class LegacySecretsMigrationScenario(BaseScenario):
    """Prove verified import, ownership, retirement, and failure rollback."""

    async def test_scenario(self) -> None:
        keyring = SecretKeyring(keys={1: bytes(range(32))}, active_version=1)
        successful_root = self.run_path / "successful-system"
        successful_root.mkdir()
        source_path = successful_root / "secrets.yaml"
        source_path.write_text(
            """OPENAI_API_KEY: user-api-key
LOGFIRE_TOKEN: system-logfire-token
OPENAI_OAUTH_TOKEN_STATE: reconnect-required
OPENAI_OAUTH_PENDING_STATE: discard-pending
EMPTY_VALUE:
""",
            encoding="utf-8",
        )
        service = EncryptedSecretsService(
            system_root=str(successful_root), keyring=keyring
        )

        result = migrate_legacy_secrets_yaml(
            system_root=successful_root, service=service
        )
        self.soft_assert_equal(result.phase, "complete", "Migration should complete")
        self.soft_assert_equal(
            result.imported_count,
            2,
            "Only non-empty static and operational values should import",
        )
        self.soft_assert_equal(
            result.skipped_oauth_count,
            2,
            "OAuth token and pending state should require reconnection",
        )
        self.soft_assert(
            not source_path.exists(),
            "Verified migration should retire the live plaintext file",
        )
        backup_path = (
            successful_root / MIGRATION_BACKUP_DIRECTORY / LEGACY_BACKUP_FILENAME
        )
        self.soft_assert(
            backup_path.exists(),
            "Verified migration should preserve the legacy file as a backup",
        )
        self.soft_assert(
            "OPENAI_API_KEY: user-api-key" in backup_path.read_text(encoding="utf-8"),
            "The retired backup should retain the original rollback data",
        )
        self.soft_assert_equal(
            service.get_for_authority(
                LOCAL_USER_AUTHORITY, DEFAULT_NAMESPACE, "OPENAI_API_KEY"
            ),
            "user-api-key",
            "User API keys should belong to local-user",
        )
        self.soft_assert_equal(
            service.get_for_authority(
                SYSTEM_AUTHORITY, DEFAULT_NAMESPACE, "LOGFIRE_TOKEN"
            ),
            "system-logfire-token",
            "Operational Logfire state should belong to system",
        )
        self.soft_assert_equal(
            service.get_for_authority(
                LOCAL_USER_AUTHORITY,
                DEFAULT_NAMESPACE,
                "OPENAI_OAUTH_TOKEN_STATE",
            ),
            None,
            "OAuth token state must not migrate",
        )

        repeated = migrate_legacy_secrets_yaml(
            system_root=successful_root, service=service
        )
        self.soft_assert_equal(
            repeated.imported_count,
            2,
            "Completed migration should be idempotent",
        )

        invalid_root = self.run_path / "invalid-system"
        invalid_root.mkdir()
        invalid_source = invalid_root / "secrets.yaml"
        invalid_source.write_text("OPENAI_API_KEY: 123\n", encoding="utf-8")
        invalid_service = EncryptedSecretsService(
            system_root=str(invalid_root), keyring=keyring
        )
        invalid_failed = False
        try:
            migrate_legacy_secrets_yaml(
                system_root=invalid_root, service=invalid_service
            )
        except ValueError:
            invalid_failed = True
        self.soft_assert(
            invalid_failed,
            "Malformed legacy values should fail before importing anything",
        )
        self.soft_assert(
            invalid_source.exists(),
            "Failed migration must preserve the plaintext source",
        )
        conn = sqlite3.connect(invalid_root / "access.db")
        try:
            secret_count = int(
                conn.execute("SELECT COUNT(*) FROM encrypted_secrets").fetchone()[0]
            )
            migration_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM secrets_bootstrap_migrations"
                ).fetchone()[0]
            )
        finally:
            conn.close()
        self.soft_assert_equal(
            secret_count, 0, "Failed migration must not leave partial secret rows"
        )
        self.soft_assert_equal(
            migration_count, 0, "Failed migration must not record completion"
        )

        collision_root = self.run_path / "backup-collision-system"
        collision_root.mkdir()
        collision_source = collision_root / "secrets.yaml"
        collision_source.write_text("OPENAI_API_KEY: new-value\n", encoding="utf-8")
        collision_backup_directory = collision_root / MIGRATION_BACKUP_DIRECTORY
        collision_backup_directory.mkdir()
        collision_backup = collision_backup_directory / LEGACY_BACKUP_FILENAME
        collision_backup.write_text("OPENAI_API_KEY: old-value\n", encoding="utf-8")
        collision_service = EncryptedSecretsService(
            system_root=str(collision_root), keyring=keyring
        )
        collision_result = migrate_legacy_secrets_yaml(
            system_root=collision_root, service=collision_service
        )
        self.soft_assert_equal(
            collision_result.phase,
            "complete",
            "An existing backup should not block migration",
        )
        self.soft_assert_equal(
            collision_backup.read_text(encoding="utf-8"),
            "OPENAI_API_KEY: old-value\n",
            "A backup collision should preserve the existing rollback file",
        )
        versioned_backup = collision_backup_directory / f"{LEGACY_BACKUP_FILENAME} (2)"
        self.soft_assert_equal(
            versioned_backup.read_text(encoding="utf-8"),
            "OPENAI_API_KEY: new-value\n",
            "A backup collision should retire the source under the next version",
        )
        self.soft_assert(
            not collision_source.exists(),
            "A versioned backup should retire the live plaintext file",
        )

        self._assert_crash_recovery(keyring)

        self.assert_no_failures()
        self.teardown_scenario()

    def _assert_crash_recovery(self, keyring: SecretKeyring) -> None:
        """Restart a killed importer at each durable/filesystem boundary."""
        for phase in ("during-import", "after-import", "after-rename"):
            root = self.run_path / phase
            root.mkdir()
            source = root / "secrets.yaml"
            source.write_text("OPENAI_API_KEY: original\nLOGFIRE_TOKEN: operational\n")
            script = r"""
import os, sys
from pathlib import Path
from unittest.mock import patch
root = Path(sys.argv[1])
from core.runtime.paths import set_bootstrap_roots
set_bootstrap_roots(root / 'data', root)
from core.secrets import EncryptedSecretsService, SecretKeyring
from core.secrets import legacy_migration
service = EncryptedSecretsService(system_root=str(root), keyring=SecretKeyring(keys={1: bytes(range(32))}, active_version=1))
def die(*args, **kwargs):
    os._exit(71)
if sys.argv[2] == 'during-import':
    original = service.set_for_authority_on_connection
    def write_then_die(*args, **kwargs):
        original(*args, **kwargs)
        die()
    target, name, replacement = service, 'set_for_authority_on_connection', write_then_die
elif sys.argv[2] == 'after-import':
    target, name, replacement = legacy_migration, '_verify_writes', die
else:
    target, name, replacement = legacy_migration, '_mark_complete', die
with patch.object(target, name, replacement):
    legacy_migration.migrate_legacy_secrets_yaml(system_root=root, service=service)
"""
            result = subprocess.run(
                [sys.executable, "-c", script, str(root), phase],
                cwd=Path(__file__).resolve().parents[4],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.soft_assert_equal(
                result.returncode,
                71,
                f"{phase} must kill the importer at the intended boundary: {result.stderr}",
            )
            with sqlite3.connect(root / "access.db") as conn:
                counts = (
                    conn.execute("SELECT count(*) FROM encrypted_secrets").fetchone()[
                        0
                    ],
                    conn.execute(
                        "SELECT count(*) FROM secrets_bootstrap_migrations"
                    ).fetchone()[0],
                )
            self.soft_assert_equal(
                counts,
                (0, 0) if phase == "during-import" else (2, 1),
                f"{phase} must commit ciphertext and import state together",
            )
            self.soft_assert_equal(
                source.exists(),
                phase != "after-rename",
                f"{phase} should preserve the expected source location",
            )
            service = EncryptedSecretsService(system_root=str(root), keyring=keyring)
            expected = "original"
            if phase == "after-rename":
                # Recovery must verify identity presence without replaying the
                # retired YAML over a newer credential.
                expected = "newer"
                service.set_for_authority(
                    LOCAL_USER_AUTHORITY, DEFAULT_NAMESPACE, "OPENAI_API_KEY", expected
                )
            resumed = migrate_legacy_secrets_yaml(system_root=root, service=service)
            self.soft_assert_equal(
                resumed.phase, "complete", f"{phase} must resume to completion"
            )
            self.soft_assert_equal(
                service.get_for_authority(
                    LOCAL_USER_AUTHORITY, DEFAULT_NAMESPACE, "OPENAI_API_KEY"
                ),
                expected,
                f"{phase} must preserve committed credentials",
            )
            self.soft_assert_equal(
                service.get_for_authority(
                    SYSTEM_AUTHORITY, DEFAULT_NAMESPACE, "LOGFIRE_TOKEN"
                ),
                "operational",
                f"{phase} must preserve system ownership",
            )
            self.soft_assert(
                (root / MIGRATION_BACKUP_DIRECTORY / LEGACY_BACKUP_FILENAME).exists(),
                f"{phase} must retain the plaintext backup",
            )
            repeated = migrate_legacy_secrets_yaml(system_root=root, service=service)
            self.soft_assert_equal(
                repeated.phase, "complete", f"{phase} recovery must be repeatable"
            )


if __name__ == "__main__":
    asyncio.run(LegacySecretsMigrationScenario().test_scenario())
