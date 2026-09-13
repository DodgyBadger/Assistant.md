"""Validate principal-owned Google connection and Gmail scope readiness."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

_direct_run_root: tempfile.TemporaryDirectory[str] | None = None
if __name__ == "__main__":
    from core.runtime.paths import set_bootstrap_roots

    _direct_run_root = tempfile.TemporaryDirectory(
        prefix="assistantmd-google-connection-"
    )
    direct_root = Path(_direct_run_root.name)
    data_root = direct_root / "data"
    bootstrap_system_root = direct_root / "system"
    data_root.mkdir()
    bootstrap_system_root.mkdir()
    set_bootstrap_roots(data_root=data_root, system_root=bootstrap_system_root)

from core.connections import (  # noqa: E402
    BuiltInConnectionService,
    ConnectionRequirement,
    GoogleConnectionCreate,
    GoogleConnectionUpdate,
    connection_requirement_available,
)
from core.identity import ExecutionAuthority, use_execution_authority  # noqa: E402
from core.integrations.google import (  # noqa: E402
    GMAIL_COMPOSE_SCOPE,
    GMAIL_READONLY_SCOPE,
    GOOGLE_IDENTITY_SCOPES,
    GmailResourceService,
    GoogleCapability,
    GoogleConnectionService,
    GoogleOAuthCoordinator,
    GoogleOAuthTokenState,
)
from core.secrets import EncryptedSecretsService, SecretKeyring  # noqa: E402
from core.settings.store import get_enabled_tools_config  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class GmailPrincipalConnectionScenario(BaseScenario):
    """Prove Google secrets, identity, scopes, and availability stay owner-scoped."""

    def test_scenario(self) -> None:
        system_root = self.run_path / "system"
        system_root.mkdir()
        connections = BuiltInConnectionService(system_root=str(system_root))
        secrets = EncryptedSecretsService(
            system_root=str(system_root),
            keyring=SecretKeyring(keys={1: bytes(range(32))}, active_version=1),
        )
        google = GoogleConnectionService(connections=connections, secrets=secrets)
        owner = ExecutionAuthority("google-owner")
        other = ExecutionAuthority("google-other")

        self.soft_assert_equal(
            google.status(owner).state,
            "not_configured",
            "Google should begin unconfigured for each principal",
        )
        connections.create_google_connection_for_authority(
            owner,
            GoogleConnectionCreate(
                display_name="Google", client_id="owner.apps.googleusercontent.com"
            ),
        )
        self.soft_assert_equal(
            google.status(owner).state,
            "not_configured",
            "A visible client ID alone should not make Google usable",
        )
        google.set_client_secret(owner, "owner-client-secret")
        self.soft_assert_equal(
            google.status(owner).state,
            "authorization_required",
            "Client configuration should still require user authorization",
        )

        partial_token = GoogleOAuthTokenState(
            access_token="partial-access-token",
            refresh_token="partial-refresh-token",
            expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            scopes=GOOGLE_IDENTITY_SCOPES,
            account_id="owner-account-id",
            account_email="owner@example.com",
        )
        google.save_token_state(owner, partial_token)
        partial = google.capability_availability(owner, GoogleCapability.GMAIL_READ)
        partial_compose = google.capability_availability(
            owner, GoogleCapability.GMAIL_COMPOSE
        )
        self.soft_assert(
            not partial.available and partial.missing_scopes == (GMAIL_READONLY_SCOPE,),
            "A connected Google account without Gmail scope must not expose Gmail",
        )
        self.soft_assert(
            not partial_compose.available
            and GMAIL_COMPOSE_SCOPE in partial_compose.missing_scopes,
            "A connection without compose scope must not expose draft creation",
        )

        ready_token = GoogleOAuthTokenState(
            access_token="owner-access-token",
            refresh_token="owner-refresh-token",
            expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            scopes=(*GOOGLE_IDENTITY_SCOPES, GMAIL_READONLY_SCOPE),
            account_id="owner-account-id",
            account_email="owner@example.com",
        )
        google.save_token_state(owner, ready_token)
        status = google.status(owner)
        ready = google.capability_availability(owner, GoogleCapability.GMAIL_READ)
        read_only_compose = google.capability_availability(
            owner, GoogleCapability.GMAIL_COMPOSE
        )
        self.soft_assert_equal(
            (
                status.state,
                status.connected,
                status.account_email,
                status.client_secret_present,
                ready.available,
            ),
            ("ready", True, "owner@example.com", True, True),
            "A complete scoped grant should make Gmail available",
        )
        self.soft_assert(
            not read_only_compose.available
            and read_only_compose.missing_scopes == (GMAIL_COMPOSE_SCOPE,),
            "Read-only Gmail authorization must remain insufficient for drafts",
        )
        with (
            use_execution_authority(owner),
            patch("core.runtime.state.has_runtime_context", return_value=True),
            patch(
                "core.runtime.state.get_runtime_context",
                return_value=SimpleNamespace(google_connection=google),
            ),
        ):
            self.soft_assert(
                connection_requirement_available(
                    ConnectionRequirement.GOOGLE_GMAIL_READ
                ),
                "Tool binding requirements should resolve the ready Gmail grant",
            )
            self.soft_assert(
                "gmail" in get_enabled_tools_config(),
                "A ready scoped grant should expose the settings-backed Gmail tool",
            )
        self.soft_assert_equal(
            google.status(other).state,
            "not_configured",
            "Another principal must not inherit Google configuration or tokens",
        )

        work = connections.create_google_connection_for_authority(
            owner,
            GoogleConnectionCreate(
                display_name="Work Gmail",
                client_id="work.apps.googleusercontent.com",
            ),
        )
        google.set_client_secret(owner, "work-client-secret", work.connection_id)
        google.save_token_state(
            owner,
            GoogleOAuthTokenState(
                access_token="work-access-token",
                refresh_token="work-refresh-token",
                expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                scopes=(*GOOGLE_IDENTITY_SCOPES, GMAIL_READONLY_SCOPE),
                account_id="work-account-id",
                account_email="work@example.com",
            ),
            work.connection_id,
        )
        gmail = GmailResourceService(
            connections=connections,
            google=google,
            oauth=GoogleOAuthCoordinator(
                connections=connections,
                google=google,
                secrets=secrets,
            ),
        )
        discovered = gmail.list_connections(owner)
        self.soft_assert_equal(
            [(item["connection"], item["account_email"]) for item in discovered],
            [("google", "owner@example.com"), ("work-gmail", "work@example.com")],
            "Gmail should expose stable account selectors and sanitized identities",
        )
        self.soft_assert_equal(
            gmail.status(owner)["account_email"],
            "owner@example.com",
            "An omitted Gmail selector should resolve the explicit default",
        )
        self.soft_assert_equal(
            gmail.status(owner, "work-gmail")["account_email"],
            "work@example.com",
            "An explicit Gmail slug should select the requested account",
        )

        database_bytes = (system_root / "access.db").read_bytes()
        for sensitive in (
            b"owner-client-secret",
            b"owner-access-token",
            b"owner-refresh-token",
            b"owner@example.com",
            b"owner-account-id",
            b"work-client-secret",
            b"work-access-token",
        ):
            self.soft_assert(
                sensitive not in database_bytes,
                "Google secret and account state must remain encrypted at rest",
            )
        self.soft_assert(
            b"owner.apps.googleusercontent.com"
            in (system_root / "access.db").read_bytes(),
            "The non-secret Google client ID should live in connection metadata",
        )

        from core.oauth import EncryptedOAuthStorage

        previous_credential = google.resolve_client_credential(owner)
        previous_grant = google.load_token_state(owner)
        with patch.object(
            EncryptedOAuthStorage,
            "delete_many_sync_on_connection",
            side_effect=sqlite3.OperationalError("injected cleanup failure"),
        ):
            try:
                google.set_client_secret(owner, "rotated-client-secret")
            except sqlite3.OperationalError:
                pass
            else:
                self.soft_assert(
                    False, "Credential replacement cleanup failure must propagate"
                )
        self.soft_assert_equal(
            (google.resolve_client_credential(owner), google.load_token_state(owner)),
            (previous_credential, previous_grant),
            "Credential replacement and grant cleanup must roll back together",
        )
        google.set_client_secret(owner, "rotated-client-secret")
        self.soft_assert_equal(
            google.status(owner).state,
            "authorization_required",
            "Committed credential replacement must invalidate the old token",
        )

        owner_connection = connections.get_google_connection_for_authority(owner)
        if owner_connection is None:
            raise AssertionError("Expected the owner's default Google connection")
        changed_identity = google.update_connection(
            owner,
            owner_connection.connection_id,
            GoogleConnectionUpdate(
                client_id="changed.apps.googleusercontent.com",
                display_name=owner_connection.display_name,
                gmail=owner_connection.gmail,
            ),
        )
        self.soft_assert_equal(
            (
                changed_identity.oauth_generation,
                google.status(owner).state,
                google.capability_availability(
                    owner, GoogleCapability.GMAIL_READ
                ).available,
            ),
            (owner_connection.oauth_generation + 1, "not_configured", False),
            "Changing client identity should immediately invalidate readiness",
        )
        restored_name = google.update_connection(
            owner,
            owner_connection.connection_id,
            GoogleConnectionUpdate(
                client_id=owner_connection.client_id,
                display_name=owner_connection.display_name,
                gmail=owner_connection.gmail,
            ),
        )
        self.soft_assert_equal(
            (restored_name.oauth_generation, google.status(owner).state),
            (owner_connection.oauth_generation + 2, "not_configured"),
            "Returning to an old client ID must not resurrect its former grant",
        )
        google.set_client_secret(owner, "replacement-client-secret")
        self.soft_assert_equal(
            google.status(owner).state,
            "authorization_required",
            "A replacement client secret should require a new authorization grant",
        )

        google.disconnect(owner)
        self.soft_assert_equal(
            google.status(owner).state,
            "not_configured",
            "Disconnect should remove encrypted client and token state",
        )
        self.assert_no_failures()
        self.teardown_scenario()


if __name__ == "__main__":
    GmailPrincipalConnectionScenario().test_scenario()
