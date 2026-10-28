"""Synthetic legacy-secret input and durable-state observations."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.secrets import EncryptedSecretsService, SecretKeyring
from core.secrets.legacy_migration import LegacySecretsMigrationResult
from validation.scenarios.application import Application

FIXTURE_VALUES = (
    "user-api-key",
    "system-logfire-token",
    "reconnect-required",
    "discard-pending",
    "new-value",
    "old-value",
    "original",
    "operational",
    "newer",
)


@dataclass
class LegacyInstallation:
    root: Path
    service: EncryptedSecretsService

    @property
    def source(self) -> Path:
        return self.root / "secrets.yaml"

    def state(self) -> dict[str, Any]:
        with sqlite3.connect(self.root / "access.db") as conn:
            return {
                "secret_count": conn.execute(
                    "SELECT count(*) FROM encrypted_secrets"
                ).fetchone()[0],
                "migration_count": conn.execute(
                    "SELECT count(*) FROM secrets_bootstrap_migrations"
                ).fetchone()[0],
                "phases": [
                    row[0]
                    for row in conn.execute(
                        "SELECT phase FROM secrets_bootstrap_migrations"
                    )
                ],
                "source_exists": self.source.exists(),
            }

    def execute(self, sql: str) -> None:
        with sqlite3.connect(self.root / "access.db") as conn:
            conn.execute(sql)


def installation(app: Application, name: str, source: str) -> LegacyInstallation:
    for value in FIXTURE_VALUES:
        app.evidence.register_secret(value)
    root = app.root / name
    root.mkdir()
    (root / "secrets.yaml").write_text(source, encoding="utf-8")
    return LegacyInstallation(
        root,
        EncryptedSecretsService(
            system_root=str(root),
            keyring=SecretKeyring(keys={1: bytes(range(32))}, active_version=1),
        ),
    )


def retain_result(
    app: Application, fixture: LegacyInstallation, result: LegacySecretsMigrationResult
) -> None:
    app.evidence.append(
        "secret-migrations.jsonl",
        {
            "root": str(fixture.root),
            "phase": result.phase,
            "imported_count": result.imported_count,
            "skipped_oauth_count": result.skipped_oauth_count,
            "source_retired": result.source_retired,
            "state": fixture.state(),
        },
    )
