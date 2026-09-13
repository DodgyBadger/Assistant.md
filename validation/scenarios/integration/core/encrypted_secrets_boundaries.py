"""Security contracts for principal-owned encrypted secret storage."""

from __future__ import annotations

import asyncio
import base64
import json
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

_direct_run_root: tempfile.TemporaryDirectory[str] | None = None
if __name__ == "__main__":
    from core.runtime.paths import set_bootstrap_roots

    _direct_run_root = tempfile.TemporaryDirectory(
        prefix="assistantmd-secret-boundary-"
    )
    direct_root = Path(_direct_run_root.name)
    data_root = direct_root / "data"
    bootstrap_system_root = direct_root / "system"
    data_root.mkdir()
    bootstrap_system_root.mkdir()
    set_bootstrap_roots(data_root=data_root, system_root=bootstrap_system_root)

from core.identity import ExecutionAuthority  # noqa: E402
from core.identity.context import use_execution_authority  # noqa: E402
from core.secrets import (  # noqa: E402
    EncryptedSecretsService,
    SecretCopy,
    SecretGuardMismatchError,
    SecretIdentity,
    SecretIntegrityError,
    SecretKeyring,
    SecretNamespaceDeletion,
    SecretRelocation,
    initialize_secrets_bootstrap,
    require_secrets_ready,
    reset_secrets_bootstrap_status,
)
from core.secrets.crypto import (  # noqa: E402
    LEGACY_ACTIVE_KEY_VERSION_ENV,
    LEGACY_KEYRING_ENV,
    SECRET_KEY_ENV,
)
from core.system_migrations import run_system_migrations  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class EncryptedSecretsBoundariesScenario(BaseScenario):
    """Prove ownership, integrity, and rotation without exposing values."""

    async def test_scenario(self) -> None:
        encoded_key = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")
        with patch.dict("os.environ", {SECRET_KEY_ENV: encoded_key}, clear=True):
            configured_keyring = SecretKeyring.from_environment()
        self.soft_assert_equal(
            configured_keyring.active_version,
            1,
            "The installation key should map to internal key version 1",
        )

        legacy_keyring = json.dumps({"1": encoded_key}, separators=(",", ":"))
        with patch.dict(
            "os.environ",
            {
                LEGACY_KEYRING_ENV: legacy_keyring,
                LEGACY_ACTIVE_KEY_VERSION_ENV: "1",
            },
            clear=True,
        ):
            legacy_configuration = SecretKeyring.from_environment()
        self.soft_assert_equal(
            legacy_configuration.keys[1],
            bytes(range(32)),
            "Existing development keyring configuration should remain readable",
        )

        locked_root = self.run_path / "locked-system"
        with patch.dict(
            "os.environ",
            {
                SECRET_KEY_ENV: "",
                LEGACY_KEYRING_ENV: "",
                LEGACY_ACTIVE_KEY_VERSION_ENV: "",
            },
            clear=False,
        ):
            locked_status = initialize_secrets_bootstrap(locked_root)
        self.soft_assert_equal(
            locked_status.state,
            "locked",
            "Missing key configuration should enter secrets-locked mode",
        )
        self.soft_assert(
            not (locked_root / "access.db").exists(),
            "Locked bootstrap must not create or mutate the secrets database",
        )
        execution_blocked = False
        try:
            require_secrets_ready()
        except SecretIntegrityError:
            execution_blocked = True
        self.soft_assert(
            execution_blocked,
            "Secrets-locked bootstrap must block model/secret execution",
        )
        run_system_migrations(locked_root, backup=False)
        self.soft_assert(
            not (locked_root / "access.db").exists(),
            "General system migrations must not touch locked encrypted storage",
        )
        reset_secrets_bootstrap_status()

        owner = ExecutionAuthority("secret-owner")
        other = ExecutionAuthority("secret-other")
        key_v1 = bytes(range(32))
        key_v2 = bytes(reversed(range(32)))
        system_root = self.run_path / "system"
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
        self.soft_assert(
            missing_authority_failed,
            "Secret lookup must fail when execution authority is absent",
        )

        with use_execution_authority(owner):
            service.set("providers", "API_KEY", "owner-value")
            service.set("mcp", "gmail-token", "gmail-value")
            self.soft_assert_equal(
                service.get("providers", "API_KEY"),
                "owner-value",
                "The owner should decrypt its own secret",
            )

        with use_execution_authority(other):
            self.soft_assert_equal(
                service.get("providers", "API_KEY"),
                None,
                "A foreign principal should see a missing secret",
            )
            service.set("providers", "API_KEY", "other-value")
            self.soft_assert_equal(
                [(item.namespace, item.name) for item in service.list_metadata()],
                [("providers", "API_KEY")],
                "Metadata enumeration should contain only the active owner",
            )

        with use_execution_authority(owner):
            self.soft_assert_equal(
                service.get("providers", "API_KEY"),
                "owner-value",
                "A same-named foreign secret must not replace the owner's value",
            )
            metadata = service.list_metadata()
            self.soft_assert_equal(
                [(item.namespace, item.name) for item in metadata],
                [("mcp", "gmail-token"), ("providers", "API_KEY")],
                "Metadata should expose names but never values",
            )

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
        self.soft_assert_equal(
            (
                preserved.relocated_count,
                service.get_for_authority(owner, "legacy", "preserved"),
                service.get_for_authority(owner, "canonical", "preserved"),
            ),
            (1, None, "canonical-value"),
            "Atomic relocation should preserve a canonical destination and retire legacy state",
        )

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
        self.soft_assert_equal(
            (
                service.get_for_authority(owner, "staging", "replacement"),
                service.get_for_authority(owner, "canonical", "replacement"),
            ),
            (None, "new-value"),
            "Atomic relocation should promote an explicit replacement when requested",
        )

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
        self.soft_assert_equal(
            (
                copied.copied_count,
                replayed.copied_count,
                service.get_for_authority(owner, "saga-stage", "credential"),
                service.get_for_authority(owner, "saga-live", "credential"),
            ),
            (1, 1, "new-token", "new-token"),
            "Copy promotion should preserve staging and remain safe to replay",
        )

        service.set_for_authority(owner, "oauth-state", "token", "owner-token")
        service.set_for_authority(owner, "oauth-state", "pending", "owner-pending")
        service.set_for_authority(owner, "oauth-state.child", "token", "child-token")
        service.set_for_authority(other, "oauth-state", "token", "other-token")
        namespace_cleanup = service.mutate_for_authority(
            owner,
            namespace_deletions=(SecretNamespaceDeletion("oauth-state"),),
        )
        self.soft_assert_equal(
            (
                namespace_cleanup.deleted_count,
                service.list_metadata_for_authority(owner, "oauth-state"),
                service.get_for_authority(owner, "oauth-state.child", "token"),
                service.get_for_authority(other, "oauth-state", "token"),
            ),
            (2, [], "child-token", "other-token"),
            "Namespace deletion should be exact and principal scoped",
        )
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
            self.soft_assert(False, "A stale encrypted guard should reject its write")
        self.soft_assert_equal(
            service.get_for_authority(owner, "oauth-fence.state", "token"),
            "guarded-token",
            "A guard mismatch must preserve the previously committed target",
        )

        service.set_for_authority(owner, "serialized-fence", "marker", "serialized-v1")
        guard_selected = threading.Event()
        release_guard = threading.Event()
        writer_started = threading.Event()
        writer_finished = threading.Event()
        guarded_finished = threading.Event()
        thread_errors: list[BaseException] = []
        original_select = service._select_row

        def pause_after_guard_select(
            conn: sqlite3.Connection,
            *,
            owner_principal_id: str,
            namespace: str,
            name: str,
        ) -> sqlite3.Row | None:
            row = original_select(
                conn,
                owner_principal_id=owner_principal_id,
                namespace=namespace,
                name=name,
            )
            if namespace == "serialized-fence" and name == "marker":
                guard_selected.set()
                if not release_guard.wait(timeout=5):
                    raise TimeoutError("Timed out waiting to release guarded write")
            return row

        def guarded_writer() -> None:
            try:
                service.guarded_set_for_authority(
                    owner,
                    guard=SecretIdentity("serialized-fence", "marker"),
                    expected_guard_value="serialized-v1",
                    target=SecretIdentity("serialized-fence.state", "token"),
                    value="serialized-token",
                )
            except BaseException as exc:  # noqa: BLE001 - surfaced to scenario
                thread_errors.append(exc)
            finally:
                guarded_finished.set()

        def competing_writer() -> None:
            writer_started.set()
            try:
                service.set_for_authority(
                    owner, "serialized-fence", "marker", "serialized-v2"
                )
            except BaseException as exc:  # noqa: BLE001 - surfaced to scenario
                thread_errors.append(exc)
            finally:
                writer_finished.set()

        with patch.object(service, "_select_row", side_effect=pause_after_guard_select):
            guarded_thread = threading.Thread(target=guarded_writer)
            guarded_thread.start()
            self.soft_assert(
                guard_selected.wait(timeout=5),
                "Guarded write should reach the authenticated comparison",
            )
            competing_thread = threading.Thread(target=competing_writer)
            competing_thread.start()
            self.soft_assert(
                writer_started.wait(timeout=5),
                "Competing guard writer should start",
            )
            self.soft_assert(
                not writer_finished.wait(timeout=0.1),
                "A competing writer must not enter the guarded compare/write window",
            )
            release_guard.set()
            guarded_thread.join(timeout=5)
            competing_thread.join(timeout=5)
        self.soft_assert_equal(
            (
                guarded_finished.is_set(),
                writer_finished.is_set(),
                thread_errors,
                service.get_for_authority(owner, "serialized-fence.state", "token"),
                service.get_for_authority(owner, "serialized-fence", "marker"),
            ),
            (True, True, [], "serialized-token", "serialized-v2"),
            "The guarded mutation should serialize before the competing writer",
        )
        service.delete_for_authority(owner, "serialized-fence.state", "token")
        service.delete_for_authority(owner, "serialized-fence", "marker")

        service.set_for_authority(owner, "copy-rollback", "source", "copy-value")
        service.set_for_authority(owner, "delete-rollback", "blocked", "keep-value")
        with sqlite3.connect(system_root / "access.db") as conn:
            conn.execute(
                """
                CREATE TRIGGER reject_namespace_delete
                BEFORE DELETE ON encrypted_secrets
                WHEN OLD.owner_principal_id = 'secret-owner'
                  AND OLD.namespace = 'delete-rollback'
                BEGIN
                    SELECT RAISE(ABORT, 'injected namespace deletion failure');
                END
                """
            )
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
            self.soft_assert(
                False, "Injected namespace deletion failure should propagate"
            )
        with sqlite3.connect(system_root / "access.db") as conn:
            conn.execute("DROP TRIGGER reject_namespace_delete")
        self.soft_assert_equal(
            (
                service.get_for_authority(owner, "copy-rollback", "source"),
                service.get_for_authority(owner, "copy-rollback", "destination"),
                service.get_for_authority(owner, "delete-rollback", "blocked"),
            ),
            ("copy-value", None, "keep-value"),
            "A namespace deletion failure should roll back a preceding copy",
        )

        service.set_for_authority(owner, "rollback", "source", "rollback-value")
        with sqlite3.connect(system_root / "access.db") as conn:
            conn.execute(
                """
                CREATE TRIGGER reject_relocation_delete
                BEFORE DELETE ON encrypted_secrets
                WHEN OLD.owner_principal_id = 'secret-owner'
                  AND OLD.namespace = 'rollback'
                  AND OLD.name = 'source'
                BEGIN
                    SELECT RAISE(ABORT, 'injected relocation failure');
                END
                """
            )
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
            self.soft_assert(False, "Injected relocation failure should propagate")
        with sqlite3.connect(system_root / "access.db") as conn:
            conn.execute("DROP TRIGGER reject_relocation_delete")
        self.soft_assert_equal(
            (
                service.get_for_authority(owner, "rollback", "source"),
                service.get_for_authority(owner, "rollback", "destination"),
            ),
            ("rollback-value", None),
            "A relocation failure must roll back both destination and source changes",
        )
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
        self.soft_assert_equal(
            cleanup.deleted_count,
            10,
            "Atomic batch deletion should remove every requested existing identity",
        )

        rotating_service = EncryptedSecretsService(
            system_root=str(system_root),
            keyring=SecretKeyring(keys={1: key_v1, 2: key_v2}, active_version=2),
        )
        self.soft_assert_equal(
            rotating_service.rotate_all(),
            3,
            "Rotation should update every record using the old version",
        )
        v2_only_service = EncryptedSecretsService(
            system_root=str(system_root),
            keyring=SecretKeyring(keys={2: key_v2}, active_version=2),
        )
        with use_execution_authority(owner):
            self.soft_assert_equal(
                v2_only_service.get("mcp", "gmail-token"),
                "gmail-value",
                "Rotated values should no longer require the retired key",
            )

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
                self.soft_assert(
                    "gmail-value" not in str(exc),
                    "Integrity failures must not expose plaintext",
                )
            self.soft_assert(
                tamper_failed,
                "Modified ciphertext must fail authenticated decryption",
            )

        self.assert_no_failures()
        self.teardown_scenario()


if __name__ == "__main__":
    asyncio.run(EncryptedSecretsBoundariesScenario().test_scenario())
