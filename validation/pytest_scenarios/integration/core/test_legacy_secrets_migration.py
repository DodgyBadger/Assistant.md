"""Verified legacy import, ownership, retirement and durable failure recovery."""

from __future__ import annotations

import sqlite3

import pytest

from core.identity import LOCAL_USER_AUTHORITY, SYSTEM_AUTHORITY
from core.migration_backups import MIGRATION_BACKUP_DIRECTORY
from core.secrets.legacy_migration import (
    DEFAULT_NAMESPACE,
    LEGACY_BACKUP_FILENAME,
    migrate_legacy_secrets_yaml,
)
from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.secrets_migration import (
    installation,
    retain_result,
)

pytestmark = pytest.mark.scenario


async def test_verified_legacy_import_and_backup_collision(app: Application) -> None:
    app.vault("VerifiedLegacySecrets")
    await app.start()
    source = "OPENAI_API_KEY: user-api-key\nLOGFIRE_TOKEN: system-logfire-token\nOPENAI_OAUTH_TOKEN_STATE: reconnect-required\nOPENAI_OAUTH_PENDING_STATE: discard-pending\nEMPTY_VALUE:\n"
    fixture = installation(app, "successful-system", source)

    result = migrate_legacy_secrets_yaml(
        system_root=fixture.root, service=fixture.service
    )
    retain_result(app, fixture, result)
    assert result.phase == "complete"
    assert result.imported_count == 2
    assert result.skipped_oauth_count == 2
    assert not fixture.source.exists()
    backup = fixture.root / MIGRATION_BACKUP_DIRECTORY / LEGACY_BACKUP_FILENAME
    assert backup.read_text() == source
    assert (
        fixture.service.get_for_authority(
            LOCAL_USER_AUTHORITY, DEFAULT_NAMESPACE, "OPENAI_API_KEY"
        )
        == "user-api-key"
    )
    assert (
        fixture.service.get_for_authority(
            SYSTEM_AUTHORITY, DEFAULT_NAMESPACE, "LOGFIRE_TOKEN"
        )
        == "system-logfire-token"
    )
    assert (
        fixture.service.get_for_authority(
            LOCAL_USER_AUTHORITY, DEFAULT_NAMESPACE, "OPENAI_OAUTH_TOKEN_STATE"
        )
        is None
    )
    assert (
        fixture.service.get_for_authority(
            LOCAL_USER_AUTHORITY, DEFAULT_NAMESPACE, "OPENAI_OAUTH_PENDING_STATE"
        )
        is None
    )
    assert fixture.state()["secret_count"] == 2
    with sqlite3.connect(fixture.root / "access.db") as conn:
        ciphertexts = [
            row[0] for row in conn.execute("SELECT ciphertext FROM encrypted_secrets")
        ]
    assert all(
        value not in ciphertext
        for value in (b"user-api-key", b"system-logfire-token")
        for ciphertext in ciphertexts
    )

    repeated = migrate_legacy_secrets_yaml(
        system_root=fixture.root, service=fixture.service
    )
    retain_result(app, fixture, repeated)
    assert repeated.phase == "complete" and repeated.imported_count == 2
    assert fixture.state()["secret_count"] == 2

    collision = installation(app, "collision-system", "OPENAI_API_KEY: new-value\n")
    backups = collision.root / MIGRATION_BACKUP_DIRECTORY
    backups.mkdir()
    old_backup = backups / LEGACY_BACKUP_FILENAME
    old_backup.write_text("OPENAI_API_KEY: old-value\n")
    result = migrate_legacy_secrets_yaml(
        system_root=collision.root, service=collision.service
    )
    retain_result(app, collision, result)
    assert result.phase == "complete"
    assert old_backup.read_text() == "OPENAI_API_KEY: old-value\n"
    assert (
        backups / f"{LEGACY_BACKUP_FILENAME} (2)"
    ).read_text() == "OPENAI_API_KEY: new-value\n"
    assert not collision.source.exists()


async def test_invalid_legacy_secret_preserves_source_and_empty_database(
    app: Application,
) -> None:
    app.vault("InvalidLegacySecrets")
    await app.start()
    fixture = installation(app, "invalid-system", "OPENAI_API_KEY: 123\n")
    with pytest.raises(ValueError):
        migrate_legacy_secrets_yaml(system_root=fixture.root, service=fixture.service)
    assert fixture.source.read_text() == "OPENAI_API_KEY: 123\n"
    assert fixture.state() == {
        "secret_count": 0,
        "migration_count": 0,
        "phases": [],
        "source_exists": True,
    }
    app.evidence.save("invalid-secret-state.json", fixture.state())


@pytest.mark.parametrize("boundary", ["during-import", "after-import", "after-rename"])
async def test_legacy_secret_retry_after_durable_failure(
    app: Application, boundary: str
) -> None:
    """Real constraint/filesystem failures; abrupt process-death coverage remains in the original scenario."""
    app.vault("LegacySecretsRetry")
    await app.start()
    fixture = installation(
        app, boundary, "OPENAI_API_KEY: original\nLOGFIRE_TOKEN: operational\n"
    )
    backups = fixture.root / MIGRATION_BACKUP_DIRECTORY
    if boundary == "during-import":
        fixture.execute(
            "CREATE TRIGGER reject_import BEFORE INSERT ON encrypted_secrets WHEN NEW.name='LOGFIRE_TOKEN' BEGIN SELECT RAISE(ABORT, 'controlled import failure'); END"
        )
    elif boundary == "after-import":
        backups.write_text("controlled filesystem obstruction")
    else:
        fixture.execute(
            "CREATE TRIGGER reject_completion BEFORE UPDATE ON secrets_bootstrap_migrations WHEN NEW.phase='complete' BEGIN SELECT RAISE(ABORT, 'controlled completion failure'); END"
        )

    with pytest.raises((sqlite3.IntegrityError, FileExistsError)):
        migrate_legacy_secrets_yaml(system_root=fixture.root, service=fixture.service)
    state = fixture.state()
    app.evidence.save("failed-import-state.json", state)
    assert state["secret_count"] == (0 if boundary == "during-import" else 2)
    assert state["migration_count"] == (0 if boundary == "during-import" else 1)
    assert state["phases"] == ([] if boundary == "during-import" else ["imported"])
    assert state["source_exists"] == (boundary != "after-rename")

    if boundary == "during-import":
        fixture.execute("DROP TRIGGER reject_import")
    elif boundary == "after-import":
        backups.unlink()
    else:
        fixture.execute("DROP TRIGGER reject_completion")
        fixture.service.set_for_authority(
            LOCAL_USER_AUTHORITY, DEFAULT_NAMESPACE, "OPENAI_API_KEY", "newer"
        )
    resumed = migrate_legacy_secrets_yaml(
        system_root=fixture.root, service=fixture.service
    )
    retain_result(app, fixture, resumed)
    assert resumed.phase == "complete"
    assert fixture.service.get_for_authority(
        LOCAL_USER_AUTHORITY, DEFAULT_NAMESPACE, "OPENAI_API_KEY"
    ) == ("newer" if boundary == "after-rename" else "original")
    assert (
        fixture.service.get_for_authority(
            SYSTEM_AUTHORITY, DEFAULT_NAMESPACE, "LOGFIRE_TOKEN"
        )
        == "operational"
    )
    assert (backups / LEGACY_BACKUP_FILENAME).exists()
    repeated = migrate_legacy_secrets_yaml(
        system_root=fixture.root, service=fixture.service
    )
    retain_result(app, fixture, repeated)
    assert repeated.phase == "complete"
