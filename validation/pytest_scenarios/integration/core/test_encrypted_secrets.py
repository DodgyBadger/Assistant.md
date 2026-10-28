"""Principal isolation, atomic encrypted mutations, key rotation and tamper rejection."""

from __future__ import annotations

import sqlite3

import pytest

from core.identity import ExecutionAuthority
from core.identity.context import use_execution_authority
from core.secrets import (
    EncryptedSecretsService,
    SecretCopy,
    SecretGuardMismatchError,
    SecretIdentity,
    SecretIntegrityError,
    SecretKeyring,
    SecretNamespaceDeletion,
    SecretRelocation,
)
from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.security_migrations import (
    isolated_process,
    register_synthetic_secrets,
)

pytestmark = pytest.mark.scenario


async def test_encrypted_secret_boundaries(app: Application) -> None:
    register_synthetic_secrets(app)
    app.vault("EncryptedSecretBoundaries")
    await app.start()
    owner = ExecutionAuthority("secret-owner")
    other = ExecutionAuthority("secret-other")
    key_v1 = bytes(range(32))
    key_v2 = bytes(reversed(range(32)))
    system_root = app.root / "encrypted-store"
    system_root.mkdir()
    service = EncryptedSecretsService(
        system_root=str(system_root),
        keyring=SecretKeyring(keys={1: key_v1}, active_version=1),
    )
    missing_authority_failed = False
    try:
        service.get("providers", "API_KEY")
    except RuntimeError:
        missing_authority_failed = True
    assert (
        missing_authority_failed
    ), "Secret lookup must fail when execution authority is absent"
    with use_execution_authority(owner):
        service.set("providers", "API_KEY", "owner-value")
        service.set("mcp", "gmail-token", "gmail-value")
        assert (
            service.get("providers", "API_KEY") == "owner-value"
        ), "The owner should decrypt its own secret"
    with use_execution_authority(other):
        assert (
            service.get("providers", "API_KEY") is None
        ), "A foreign principal should see a missing secret"
        service.set("providers", "API_KEY", "other-value")
        assert [(item.namespace, item.name) for item in service.list_metadata()] == [
            ("providers", "API_KEY")
        ], "Metadata enumeration should contain only the active owner"
    with use_execution_authority(owner):
        assert (
            service.get("providers", "API_KEY") == "owner-value"
        ), "A same-named foreign secret must not replace the owner's value"
        metadata = service.list_metadata()
        assert [(item.namespace, item.name) for item in metadata] == [
            ("mcp", "gmail-token"),
            ("providers", "API_KEY"),
        ], "Metadata should expose names but never values"
    service.set_for_authority(owner, "legacy", "preserved", "legacy-value")
    service.set_for_authority(owner, "canonical", "preserved", "canonical-value")
    preserved = service.mutate_for_authority(
        owner,
        relocations=(
            SecretRelocation(
                source=SecretIdentity("legacy", "preserved"),
                destination=SecretIdentity("canonical", "preserved"),
            ),
        ),
    )
    assert (
        preserved.relocated_count,
        service.get_for_authority(owner, "legacy", "preserved"),
        service.get_for_authority(owner, "canonical", "preserved"),
    ) == (
        1,
        None,
        "canonical-value",
    ), "Atomic relocation should preserve a canonical destination and retire legacy state"
    service.set_for_authority(owner, "staging", "replacement", "new-value")
    service.set_for_authority(owner, "canonical", "replacement", "old-value")
    service.mutate_for_authority(
        owner,
        relocations=(
            SecretRelocation(
                source=SecretIdentity("staging", "replacement"),
                destination=SecretIdentity("canonical", "replacement"),
                overwrite=True,
            ),
        ),
    )
    assert (
        service.get_for_authority(owner, "staging", "replacement"),
        service.get_for_authority(owner, "canonical", "replacement"),
    ) == (
        None,
        "new-value",
    ), "Atomic relocation should promote an explicit replacement when requested"
    service.set_for_authority(owner, "saga-stage", "credential", "new-token")
    service.set_for_authority(owner, "saga-live", "credential", "old-token")
    copied = service.mutate_for_authority(
        owner,
        copies=(
            SecretCopy(
                source=SecretIdentity("saga-stage", "credential"),
                destination=SecretIdentity("saga-live", "credential"),
                overwrite=True,
            ),
        ),
    )
    replayed = service.mutate_for_authority(
        owner,
        copies=(
            SecretCopy(
                source=SecretIdentity("saga-stage", "credential"),
                destination=SecretIdentity("saga-live", "credential"),
                overwrite=True,
            ),
        ),
    )
    assert (
        copied.copied_count,
        replayed.copied_count,
        service.get_for_authority(owner, "saga-stage", "credential"),
        service.get_for_authority(owner, "saga-live", "credential"),
    ) == (
        1,
        1,
        "new-token",
        "new-token",
    ), "Copy promotion should preserve staging and remain safe to replay"
    service.set_for_authority(owner, "oauth-state", "token", "owner-token")
    service.set_for_authority(owner, "oauth-state", "pending", "owner-pending")
    service.set_for_authority(owner, "oauth-state.child", "token", "child-token")
    service.set_for_authority(other, "oauth-state", "token", "other-token")
    namespace_cleanup = service.mutate_for_authority(
        owner, namespace_deletions=(SecretNamespaceDeletion("oauth-state"),)
    )
    assert (
        namespace_cleanup.deleted_count,
        service.list_metadata_for_authority(owner, "oauth-state"),
        service.get_for_authority(owner, "oauth-state.child", "token"),
        service.get_for_authority(other, "oauth-state", "token"),
    ) == (
        2,
        [],
        "child-token",
        "other-token",
    ), "Namespace deletion should be exact and principal scoped"
    service.delete_for_authority(other, "oauth-state", "token")
    service.set_for_authority(owner, "oauth-fence", "marker", "fence-v1")
    service.guarded_set_for_authority(
        owner,
        guard=SecretIdentity("oauth-fence", "marker"),
        expected_guard_value="fence-v1",
        target=SecretIdentity("oauth-fence.state", "token"),
        value="guarded-token",
    )
    try:
        service.guarded_set_for_authority(
            owner,
            guard=SecretIdentity("oauth-fence", "marker"),
            expected_guard_value="stale-fence",
            target=SecretIdentity("oauth-fence.state", "token"),
            value="stale-overwrite",
        )
    except SecretGuardMismatchError:
        pass
    else:
        raise AssertionError("A stale encrypted guard should reject its write")
    assert (
        service.get_for_authority(owner, "oauth-fence.state", "token")
        == "guarded-token"
    ), "A guard mismatch must preserve the previously committed target"
    service.set_for_authority(owner, "copy-rollback", "source", "copy-value")
    service.set_for_authority(owner, "delete-rollback", "blocked", "keep-value")
    with sqlite3.connect(system_root / "access.db") as conn:
        conn.execute("""
                CREATE TRIGGER reject_namespace_delete
                BEFORE DELETE ON encrypted_secrets
                WHEN OLD.owner_principal_id = 'secret-owner'
                  AND OLD.namespace = 'delete-rollback'
                BEGIN
                    SELECT RAISE(ABORT, 'injected namespace deletion failure');
                END
                """)
    try:
        service.mutate_for_authority(
            owner,
            copies=(
                SecretCopy(
                    source=SecretIdentity("copy-rollback", "source"),
                    destination=SecretIdentity("copy-rollback", "destination"),
                ),
            ),
            namespace_deletions=(SecretNamespaceDeletion("delete-rollback"),),
        )
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("Injected namespace deletion failure should propagate")
    with sqlite3.connect(system_root / "access.db") as conn:
        conn.execute("DROP TRIGGER reject_namespace_delete")
    assert (
        service.get_for_authority(owner, "copy-rollback", "source"),
        service.get_for_authority(owner, "copy-rollback", "destination"),
        service.get_for_authority(owner, "delete-rollback", "blocked"),
    ) == (
        "copy-value",
        None,
        "keep-value",
    ), "A namespace deletion failure should roll back a preceding copy"
    service.set_for_authority(owner, "rollback", "source", "rollback-value")
    with sqlite3.connect(system_root / "access.db") as conn:
        conn.execute("""
                CREATE TRIGGER reject_relocation_delete
                BEFORE DELETE ON encrypted_secrets
                WHEN OLD.owner_principal_id = 'secret-owner'
                  AND OLD.namespace = 'rollback'
                  AND OLD.name = 'source'
                BEGIN
                    SELECT RAISE(ABORT, 'injected relocation failure');
                END
                """)
    try:
        service.mutate_for_authority(
            owner,
            relocations=(
                SecretRelocation(
                    source=SecretIdentity("rollback", "source"),
                    destination=SecretIdentity("rollback", "destination"),
                ),
            ),
        )
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("Injected relocation failure should propagate")
    with sqlite3.connect(system_root / "access.db") as conn:
        conn.execute("DROP TRIGGER reject_relocation_delete")
    assert (
        service.get_for_authority(owner, "rollback", "source"),
        service.get_for_authority(owner, "rollback", "destination"),
    ) == (
        "rollback-value",
        None,
    ), "A relocation failure must roll back both destination and source changes"
    cleanup = service.mutate_for_authority(
        owner,
        deletions=(
            SecretIdentity("canonical", "preserved"),
            SecretIdentity("canonical", "replacement"),
            SecretIdentity("rollback", "source"),
            SecretIdentity("saga-stage", "credential"),
            SecretIdentity("saga-live", "credential"),
            SecretIdentity("oauth-state.child", "token"),
            SecretIdentity("copy-rollback", "source"),
            SecretIdentity("delete-rollback", "blocked"),
            SecretIdentity("oauth-fence", "marker"),
            SecretIdentity("oauth-fence.state", "token"),
        ),
    )
    assert (
        cleanup.deleted_count == 10
    ), "Atomic batch deletion should remove every requested existing identity"
    rotating_service = EncryptedSecretsService(
        system_root=str(system_root),
        keyring=SecretKeyring(keys={1: key_v1, 2: key_v2}, active_version=2),
    )
    assert (
        rotating_service.rotate_all() == 3
    ), "Rotation should update every record using the old version"
    v2_only_service = EncryptedSecretsService(
        system_root=str(system_root),
        keyring=SecretKeyring(keys={2: key_v2}, active_version=2),
    )
    with use_execution_authority(owner):
        assert (
            v2_only_service.get("mcp", "gmail-token") == "gmail-value"
        ), "Rotated values should no longer require the retired key"
    database_path = system_root / "access.db"
    conn = sqlite3.connect(database_path)
    try:
        conn.execute(
            """
                UPDATE encrypted_secrets
                SET ciphertext = CAST(ciphertext || X'00' AS BLOB)
                WHERE owner_principal_id = ? AND namespace = ? AND name = ?
                """,
            (owner.principal_id, "mcp", "gmail-token"),
        )
        conn.commit()
    finally:
        conn.close()
    with use_execution_authority(owner):
        tamper_failed = False
        try:
            v2_only_service.get("mcp", "gmail-token")
        except SecretIntegrityError as exc:
            tamper_failed = True
            assert "gmail-value" not in str(
                exc
            ), "Integrity failures must not expose plaintext"
        assert tamper_failed, "Modified ciphertext must fail authenticated decryption"


async def test_secret_key_configuration_and_locked_bootstrap(app: Application) -> None:
    register_synthetic_secrets(app)
    app.vault("SecretsBootstrapConfiguration")
    await app.start()
    root = app.root / "locked-system"
    isolated_process(
        app,
        """
import base64, json, os, sys
from pathlib import Path
from core.runtime.paths import set_bootstrap_roots
root = Path(sys.argv[1])
set_bootstrap_roots(root / 'data', root)
from core.secrets import SecretKeyring, SecretIntegrityError, initialize_secrets_bootstrap, require_secrets_ready
from core.secrets.crypto import SECRET_KEY_ENV, LEGACY_KEYRING_ENV, LEGACY_ACTIVE_KEY_VERSION_ENV
from core.system_migrations import run_system_migrations
encoded = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip('=')
os.environ[SECRET_KEY_ENV] = encoded
configured = SecretKeyring.from_environment()
assert configured.active_version == 1
os.environ.pop(SECRET_KEY_ENV, None)
os.environ[LEGACY_KEYRING_ENV] = json.dumps({'1': encoded}, separators=(',', ':'))
os.environ[LEGACY_ACTIVE_KEY_VERSION_ENV] = '1'
assert SecretKeyring.from_environment().keys[1] == bytes(range(32))
for name in (SECRET_KEY_ENV, LEGACY_KEYRING_ENV, LEGACY_ACTIVE_KEY_VERSION_ENV):
    os.environ[name] = ''
status = initialize_secrets_bootstrap(root)
assert status.state == 'locked'
assert not (root / 'access.db').exists()
try:
    require_secrets_ready()
except SecretIntegrityError:
    pass
else:
    raise AssertionError('Locked bootstrap must reject secret execution')
run_system_migrations(root, backup=False)
assert not (root / 'access.db').exists()
print(json.dumps({'state': status.state, 'database_exists': (root / 'access.db').exists()}))
""",
        str(root),
    )
    assert not (root / "access.db").exists()
