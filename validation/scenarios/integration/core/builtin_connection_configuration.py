"""Validate principal-owned built-in connection metadata contracts."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

_direct_run_root: tempfile.TemporaryDirectory[str] | None = None
if __name__ == "__main__":
    from core.runtime.paths import set_bootstrap_roots

    _direct_run_root = tempfile.TemporaryDirectory(
        prefix="assistantmd-builtin-connections-"
    )
    direct_root = Path(_direct_run_root.name)
    data_root = direct_root / "data"
    bootstrap_system_root = direct_root / "system"
    data_root.mkdir()
    bootstrap_system_root.mkdir()
    set_bootstrap_roots(data_root=data_root, system_root=bootstrap_system_root)

from api.models import (  # noqa: E402
    GmailConnectionPreferencesRequest,
    GoogleConnectionUpdateRequest,
)
from api.services.google_connections import _google_oauth_capabilities  # noqa: E402
from core.connections import (  # noqa: E402
    BuiltInConnectionService,
    GmailPreferences,
    GoogleConnectionCreate,
    GoogleConnectionUpdate,
)
from core.identity import ExecutionAuthority, use_execution_authority  # noqa: E402
from core.integrations.google import (  # noqa: E402
    GoogleCapability,
    GoogleConnectionService,
    GoogleOAuthTokenState,
)
from core.integrations.google.connection import (  # noqa: E402
    GoogleOAuthStateChangedError,
)
from core.secrets import EncryptedSecretsService, SecretKeyring  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class BuiltInConnectionConfigurationScenario(BaseScenario):
    """Prove Google metadata and Gmail preferences are owner-scoped."""

    def test_scenario(self) -> None:
        api_preferences = GmailConnectionPreferencesRequest.model_validate(
            {
                "attachment_download_enabled": True,
                "attachment_max_mb": 40,
            }
        )
        self.soft_assert_equal(
            (
                api_preferences.attachment_download_enabled,
                api_preferences.attachment_max_mb,
            ),
            (True, 40),
            "The public API should preserve Gmail attachment preferences",
        )
        system_root = self.run_path / "system"
        system_root.mkdir()
        service = BuiltInConnectionService(system_root=str(system_root))
        google = GoogleConnectionService(
            connections=service,
            secrets=EncryptedSecretsService(
                system_root=str(system_root),
                keyring=SecretKeyring(keys={1: bytes(range(32))}, active_version=1),
            ),
        )
        owner = ExecutionAuthority("google-owner")
        other = ExecutionAuthority("google-other")

        created = service.create_google_connection_for_authority(
            owner,
            GoogleConnectionCreate(
                display_name="Google", client_id="owner.apps.googleusercontent.com"
            ),
        )
        self.soft_assert_equal(
            (
                created.client_id,
                created.gmail.search_default_results,
                created.gmail.search_max_results,
                created.gmail.message_max_characters,
                created.gmail.thread_max_messages,
                created.gmail.attachment_download_enabled,
                created.gmail.attachment_max_mb,
                created.gmail.draft_creation_enabled,
                created.gmail.draft_max_characters,
                created.config_version,
                created.oauth_generation,
                created.display_name,
                created.slug,
                created.is_default,
            ),
            (
                "owner.apps.googleusercontent.com",
                20,
                100,
                50_000,
                25,
                False,
                25,
                False,
                50_000,
                1,
                1,
                "Google",
                "google",
                True,
            ),
            "Google connection creation should apply accepted Gmail defaults",
        )
        self.soft_assert_equal(
            service.get_google_connection_for_authority(other),
            None,
            "Another principal must not see Google connection metadata",
        )

        updated = google.update_connection(
            owner,
            created.connection_id,
            GoogleConnectionUpdate(
                client_id="replacement.apps.googleusercontent.com",
                gmail=GmailPreferences(
                    search_default_results=10,
                    search_max_results=40,
                    message_max_characters=75_000,
                    thread_max_messages=30,
                    attachment_download_enabled=True,
                    attachment_max_mb=40,
                    draft_creation_enabled=True,
                    draft_max_characters=80_000,
                ),
            ),
        )
        self.soft_assert_equal(
            (
                updated.client_id,
                updated.gmail,
                updated.config_version,
                updated.oauth_generation,
            ),
            (
                "replacement.apps.googleusercontent.com",
                GmailPreferences(
                    search_default_results=10,
                    search_max_results=40,
                    message_max_characters=75_000,
                    thread_max_messages=30,
                    attachment_download_enabled=True,
                    attachment_max_mb=40,
                    draft_creation_enabled=True,
                    draft_max_characters=80_000,
                ),
                2,
                2,
            ),
            "Client identity updates should persist preferences and advance OAuth generation",
        )

        preferences_only = google.update_connection(
            owner,
            created.connection_id,
            GoogleConnectionUpdate(
                client_id=updated.client_id,
                gmail=GmailPreferences(
                    search_default_results=12,
                    search_max_results=40,
                    message_max_characters=75_000,
                    thread_max_messages=30,
                    attachment_download_enabled=True,
                    attachment_max_mb=60,
                    draft_creation_enabled=True,
                    draft_max_characters=90_000,
                ),
            ),
        )
        self.soft_assert_equal(
            (preferences_only.config_version, preferences_only.oauth_generation),
            (3, 2),
            "Non-identity edits should advance config version without invalidating OAuth",
        )
        self.soft_assert_equal(
            (
                _google_oauth_capabilities(created),
                _google_oauth_capabilities(preferences_only),
            ),
            (
                (GoogleCapability.GMAIL_READ,),
                (
                    GoogleCapability.GMAIL_READ,
                    GoogleCapability.GMAIL_COMPOSE,
                ),
            ),
            "OAuth scope selection should follow persisted draft capability policy",
        )

        secondary = service.create_google_connection_for_authority(
            owner,
            GoogleConnectionCreate(
                display_name="Work Gmail",
                client_id="work.apps.googleusercontent.com",
            ),
        )
        self.soft_assert_equal(
            (secondary.slug, secondary.is_default),
            ("work-gmail", False),
            "Additional Google connections should have stable slugs without replacing the default",
        )
        self.soft_assert_equal(
            len(service.list_google_connections_for_authority(owner)),
            2,
            "One principal should own multiple Google connections",
        )
        duplicate_rejected = False
        try:
            service.create_google_connection_for_authority(
                owner,
                GoogleConnectionCreate(
                    display_name="work gmail",
                    client_id="duplicate.apps.googleusercontent.com",
                ),
            )
        except ValueError:
            duplicate_rejected = True
        self.soft_assert(
            duplicate_rejected,
            "Google display names should be unique per principal regardless of case",
        )

        promoted = google.update_connection(
            owner,
            secondary.connection_id,
            GoogleConnectionUpdate(
                display_name=secondary.display_name,
                client_id=secondary.client_id,
                is_default=True,
                gmail=secondary.gmail,
            ),
        )
        self.soft_assert(promoted.is_default, "A second connection can become default")
        self.soft_assert_equal(
            service.get_google_connection_for_authority(owner).connection_id,
            secondary.connection_id,
            "Compatibility access should resolve the effective default",
        )

        protected_default = False
        try:
            google.delete_connection(owner, secondary.connection_id)
        except ValueError:
            protected_default = True
        self.soft_assert(
            protected_default,
            "Deleting a default should require an explicit replacement",
        )
        self.soft_assert(
            google.delete_connection(
                owner,
                secondary.connection_id,
                replacement_default_id=created.connection_id,
            ),
            "The default should be deletable with an explicit replacement",
        )

        invalid_cases = (
            {"search_default_results": 101, "search_max_results": 100},
            {"search_default_results": 1, "search_max_results": 501},
            {"message_max_characters": 250_001},
            {"thread_max_messages": 101},
            {"attachment_max_mb": 0},
            {"attachment_max_mb": 101},
            {"attachment_max_mb": True},
            {"attachment_download_enabled": 1},
            {"draft_creation_enabled": 1},
            {"draft_max_characters": 0},
            {"draft_max_characters": 250_001},
        )
        for invalid in invalid_cases:
            try:
                GmailPreferences(**invalid)
            except ValueError:
                pass
            else:
                self.soft_assert(
                    False,
                    f"Invalid Gmail preferences should be rejected: {invalid}",
                )

        self.soft_assert(
            google.delete_connection(owner, created.connection_id),
            "The owner should be able to remove Google connection metadata",
        )
        self.soft_assert_equal(
            service.get_google_connection_for_authority(owner),
            None,
            "Deleted Google connection metadata should remain absent",
        )
        self._assert_atomic_mutations(service, google, system_root)
        self.assert_no_failures()
        self.teardown_scenario()

    def _assert_atomic_mutations(self, service, google, system_root) -> None:
        from api.exceptions import APIException
        from api.services import google_connections as api_google
        from core.access_store import write_transaction

        owner = ExecutionAuthority("google-atomic-owner")

        def create(name):
            return service.create_google_connection_for_authority(
                owner, GoogleConnectionCreate(display_name=name, client_id=name)
            )

        first, second, third = (create(name) for name in ("First", "Second", "Third"))
        google.set_client_secret(owner, "atomic-fixture", first.connection_id)
        runtime = SimpleNamespace(
            built_in_connections=service, google_connection=google
        )
        with (
            use_execution_authority(owner),
            patch.object(api_google, "get_runtime_context", return_value=runtime),
            patch.object(api_google, "_google_connection_response", return_value=None),
        ):
            api_google.update_google_connection_by_id(
                second.connection_id,
                GoogleConnectionUpdateRequest(
                    client_id=second.client_id, is_default=True
                ),
            )
            self.soft_assert_equal(
                service.get_google_connection_for_authority(owner).connection_id,
                second.connection_id,
                "API update must promote the requested default",
            )
            try:
                api_google.update_google_connection_by_id(
                    second.connection_id,
                    GoogleConnectionUpdateRequest(
                        client_id=second.client_id, is_default=False
                    ),
                )
            except APIException as exc:
                self.soft_assert_equal(
                    exc.status_code, 400, "Removing the only default must be rejected"
                )
            else:
                self.soft_assert(False, "Unsetting the current default must fail")
            try:
                api_google.update_google_connection_by_id(
                    second.connection_id,
                    GoogleConnectionUpdateRequest(
                        client_id=second.client_id, display_name=first.display_name
                    ),
                )
            except APIException as exc:
                self.soft_assert_equal(
                    exc.status_code,
                    400,
                    "Duplicate name must remain a domain/API error",
                )
            else:
                self.soft_assert(False, "Duplicate name must fail")
        google.update_connection(
            owner,
            first.connection_id,
            GoogleConnectionUpdate(client_id=first.client_id, is_default=True),
        )

        @contextmanager
        def delete_replacement_before_begin(root):
            with patch(
                "core.integrations.google.connection.write_transaction",
                write_transaction,
            ):
                google.delete_connection(owner, second.connection_id)
            with write_transaction(root) as conn:
                yield conn

        with patch(
            "core.integrations.google.connection.write_transaction",
            delete_replacement_before_begin,
        ):
            try:
                google.delete_connection(
                    owner,
                    first.connection_id,
                    replacement_default_id=second.connection_id,
                )
            except LookupError:
                pass
            else:
                self.soft_assert(
                    False,
                    "A replacement deleted before the transaction must reject deletion",
                )
        self.soft_assert_equal(
            service.get_google_connection_for_authority(owner).connection_id,
            first.connection_id,
            "Rejected replacement must preserve the current default",
        )
        recreated = create("Second")
        self.soft_assert(
            recreated.slug != second.slug,
            "Deleted slugs must never retarget a new account",
        )
        with patch("core.connections.service.uuid4", return_value=second.connection_id):
            try:
                create("Reused UUID")
            except ValueError:
                pass
            else:
                self.soft_assert(False, "Deleted connection UUIDs must remain reserved")
        with sqlite3.connect(system_root / "access.db") as conn:
            conn.execute(
                "CREATE TRIGGER reject_google_secret_delete BEFORE DELETE ON encrypted_secrets BEGIN SELECT RAISE(ABORT, 'injected rollback'); END"
            )
        before = service.get_google_connection_for_authority(owner, first.connection_id)
        try:
            google.update_connection(
                owner,
                first.connection_id,
                GoogleConnectionUpdate(client_id="replacement-client"),
            )
        except ValueError:
            pass
        else:
            self.soft_assert(
                False, "Injected secret deletion failure must reject mutation"
            )
        self.soft_assert_equal(
            service.get_google_connection_for_authority(owner, first.connection_id),
            before,
            "Encrypted cleanup failure must roll back client identity and generation",
        )
        self.soft_assert_equal(
            google.resolve_client_secret(owner, first.connection_id),
            "atomic-fixture",
            "Rollback must preserve the previous credential",
        )
        with sqlite3.connect(system_root / "access.db") as conn:
            conn.execute("DROP TRIGGER reject_google_secret_delete")

        @contextmanager
        def change_identity_before_begin(root):
            with patch(
                "core.integrations.google.connection.write_transaction",
                write_transaction,
            ):
                google.update_connection(
                    owner,
                    first.connection_id,
                    GoogleConnectionUpdate(client_id="intervening-client"),
                )
                google.set_client_secret(
                    owner, "intervening-fixture", first.connection_id
                )
            with write_transaction(root) as conn:
                yield conn

        with patch(
            "core.integrations.google.connection.write_transaction",
            change_identity_before_begin,
        ):
            google.update_connection(
                owner,
                first.connection_id,
                GoogleConnectionUpdate(client_id=first.client_id),
            )
        self.soft_assert_equal(
            google.resolve_client_secret(owner, first.connection_id),
            None,
            "Current transaction metadata must govern identity cleanup after an intervening update",
        )
        concurrent_owner = ExecutionAuthority("google-concurrent-owner")
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    service.create_google_connection_for_authority,
                    concurrent_owner,
                    GoogleConnectionCreate(display_name=name, client_id=name),
                )
                for name in ("Concurrent A", "Concurrent B")
            ]
            for future in futures:
                future.result(timeout=10)
        self.soft_assert_equal(
            sum(
                item.is_default
                for item in service.list_google_connections_for_authority(
                    concurrent_owner
                )
            ),
            1,
            "Concurrent first creation must leave exactly one default",
        )

        google.set_client_secret(owner, "read-fixture-a", third.connection_id)
        newer_grant = GoogleOAuthTokenState(
            access_token="newer-read-token",
            account_id="fixture-account",
            account_email="fixture@example.test",
        )
        resolve_credential = google.resolve_client_credential

        def replace_after_credential_read(*args, **kwargs):
            captured = resolve_credential(*args, **kwargs)
            google.set_client_secret(owner, "read-fixture-b", third.connection_id)
            with patch.object(google, "resolve_client_credential", resolve_credential):
                google.save_token_state(
                    owner,
                    newer_grant,
                    third.connection_id,
                    expected_credential=resolve_credential(owner, third.connection_id),
                )
            return captured

        with patch.object(
            google, "resolve_client_credential", replace_after_credential_read
        ):
            self.soft_assert_equal(
                google.load_token_state(owner, third.connection_id),
                None,
                "A read spanning credential replacement must reject mixed state",
            )
        self.soft_assert_equal(
            google.load_token_state(owner, third.connection_id),
            newer_grant,
            "A stale reader must not delete a newer authorized grant",
        )
        self.soft_assert_equal(
            google.resolve_client_secret(owner, third.connection_id),
            "read-fixture-b",
            "A stale reader must preserve the replacement credential",
        )
        no_credential = create("Disconnect admission")
        admitted_credentials = []

        @contextmanager
        def authorize_before_disconnect(root):
            with patch(
                "core.integrations.google.connection.write_transaction",
                write_transaction,
            ):
                google.set_client_secret(
                    owner, "new-disconnect-secret", no_credential.connection_id
                )
                credential = google.resolve_client_credential(
                    owner, no_credential.connection_id
                )
                admitted_credentials.append(credential)
                google.save_token_state(
                    owner,
                    newer_grant,
                    no_credential.connection_id,
                    expected_credential=credential,
                )
            with write_transaction(root) as conn:
                yield conn

        with patch(
            "core.integrations.google.connection.write_transaction",
            authorize_before_disconnect,
        ):
            google.clear_token_state(owner, no_credential.connection_id)
        self.soft_assert_equal(
            len(admitted_credentials),
            1,
            "Disconnect must admit the current credential in its transaction",
        )
        try:
            google.save_token_state(
                owner,
                newer_grant,
                no_credential.connection_id,
                expected_credential=admitted_credentials[0],
            )
        except ValueError:
            pass
        else:
            self.soft_assert(
                False,
                "Authorization admitted before disconnect must not restore the cleared grant",
            )
        self.soft_assert_equal(
            google.resolve_client_secret(owner, no_credential.connection_id),
            "new-disconnect-secret",
            "Disconnect must retain the current reusable client secret",
        )
        self.soft_assert_equal(
            google.load_token_state(owner, no_credential.connection_id),
            None,
            "Disconnect must clear the newly admitted grant",
        )

        before_disconnect = google.resolve_client_credential(owner, third.connection_id)
        with sqlite3.connect(system_root / "access.db") as conn:
            conn.execute(
                "CREATE TRIGGER reject_disconnect_delete BEFORE DELETE ON encrypted_secrets BEGIN SELECT RAISE(ABORT, 'injected disconnect failure'); END"
            )
        try:
            google.clear_token_state(owner, third.connection_id)
        except sqlite3.IntegrityError:
            pass
        else:
            self.soft_assert(
                False, "Failure after disconnect identity rotation must roll back"
            )
        finally:
            with sqlite3.connect(system_root / "access.db") as conn:
                conn.execute("DROP TRIGGER reject_disconnect_delete")
        self.soft_assert_equal(
            google.resolve_client_credential(owner, third.connection_id),
            before_disconnect,
            "Failed disconnect must roll back credential identity rotation",
        )
        self.soft_assert_equal(
            google.load_token_state(owner, third.connection_id),
            newer_grant,
            "Failed disconnect must preserve the prior grant",
        )

        storage = google._storage(owner, third.connection_id)
        guarded_put = storage.put_sync_if_unchanged

        def delete_before_token_commit(*args, **kwargs):
            google.delete_connection(owner, third.connection_id)
            return guarded_put(*args, **kwargs)

        with (
            patch.object(google, "_storage", return_value=storage),
            patch.object(storage, "put_sync_if_unchanged", delete_before_token_commit),
        ):
            try:
                google.save_token_state(owner, newer_grant, third.connection_id)
            except GoogleOAuthStateChangedError:
                pass
            else:
                self.soft_assert(
                    False,
                    "Token saves without an explicit guard must still reject concurrent deletion",
                )
        with sqlite3.connect(system_root / "access.db") as conn:
            remaining = conn.execute(
                "SELECT count(*) FROM encrypted_secrets WHERE owner_principal_id=? AND namespace=?",
                (owner.principal_id, f"oauth.google.{third.connection_id}"),
            ).fetchone()[0]
        self.soft_assert_equal(
            remaining,
            0,
            "A delayed standalone token save must not recreate deleted credential state",
        )


if __name__ == "__main__":
    BuiltInConnectionConfigurationScenario().test_scenario()
