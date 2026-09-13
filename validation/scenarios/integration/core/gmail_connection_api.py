"""Validate Gmail draft capability wiring through the connection API."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

_direct_run_root: tempfile.TemporaryDirectory[str] | None = None
if __name__ == "__main__":
    from core.runtime.paths import set_bootstrap_roots

    _direct_run_root = tempfile.TemporaryDirectory(prefix="assistantmd-gmail-api-")
    direct_root = Path(_direct_run_root.name)
    (direct_root / "data").mkdir()
    (direct_root / "system").mkdir()
    set_bootstrap_roots(
        data_root=direct_root / "data", system_root=direct_root / "system"
    )

from api.services.google_connections import (  # noqa: E402
    _google_connection_response,
    start_google_oauth,
)
from core.connections import (  # noqa: E402
    BuiltInConnectionService,
    GmailPreferences,
    GoogleConnectionCreate,
)
from core.identity import (  # noqa: E402
    ExecutionAuthority,
    use_execution_authority,
)
from core.integrations.google import (  # noqa: E402
    GMAIL_COMPOSE_SCOPE,
    GMAIL_READONLY_SCOPE,
    GOOGLE_IDENTITY_SCOPES,
    GoogleCapability,
    GoogleCapabilityAvailability,
    GoogleConnectionStatus,
    GoogleOAuthStart,
)
from validation.core.base_scenario import BaseScenario  # noqa: E402


class GmailConnectionAPIScenario(BaseScenario):
    """Prove persisted policy drives OAuth and public draft readiness."""

    def test_scenario(self) -> None:
        authority = ExecutionAuthority("gmail-api-owner")
        system_root = self.run_path / "system"
        system_root.mkdir()
        connections = BuiltInConnectionService(system_root=str(system_root))
        read_only = connections.create_google_connection_for_authority(
            authority,
            GoogleConnectionCreate(
                display_name="Read only",
                client_id="read.apps.googleusercontent.com",
                is_default=True,
            ),
        )
        drafts = connections.create_google_connection_for_authority(
            authority,
            GoogleConnectionCreate(
                display_name="Drafts",
                client_id="drafts.apps.googleusercontent.com",
                gmail=GmailPreferences(draft_creation_enabled=True),
            ),
        )
        oauth = _OAuthRecorder()
        google = _GoogleStatus(connections)
        runtime = SimpleNamespace(
            built_in_connections=connections,
            google_connection=google,
        )
        with (
            use_execution_authority(authority),
            patch(
                "api.services.google_connections.get_runtime_context",
                return_value=runtime,
            ),
            patch(
                "api.services.google_connections._oauth_redirect_uri",
                return_value="https://assistant.example/google/callback",
            ),
            patch(
                "api.services.google_connections._oauth_coordinator",
                return_value=oauth,
            ),
        ):
            start_google_oauth(read_only.connection_id)
            start_google_oauth(drafts.connection_id)
            read_response = _google_connection_response(read_only.connection_id)
            draft_response = _google_connection_response(drafts.connection_id)

        self.soft_assert_equal(
            oauth.capabilities,
            [
                (GoogleCapability.GMAIL_READ,),
                (GoogleCapability.GMAIL_READ, GoogleCapability.GMAIL_COMPOSE),
            ],
            "OAuth start should use the selected connection's persisted capability policy",
        )
        self.soft_assert_equal(
            (
                read_response.gmail_draft_available,
                read_response.gmail_draft_missing_scopes,
                draft_response.gmail_draft_available,
                draft_response.gmail_draft_missing_scopes,
            ),
            (False, [GMAIL_COMPOSE_SCOPE], False, [GMAIL_COMPOSE_SCOPE]),
            "Connection responses should expose opt-in-aware compose readiness",
        )

        script = (
            Path(__file__).resolve().parents[4] / "static/js/configuration.js"
        ).read_text(encoding="utf-8")
        self.soft_assert(
            "Save the Gmail capability changes before authorizing Google." in script
            and "const gmailReady = Boolean" in script,
            "The UI should require persisted policy and share full capability readiness",
        )
        self.soft_assert(
            "draftAuthorizationRequired ? 'state-warning'" in script
            and "Reauthorization required for Gmail drafts" in script,
            "Missing compose authorization should have a visible warning state",
        )
        self.assert_no_failures()
        self.teardown_scenario()


class _OAuthRecorder:
    def __init__(self) -> None:
        self.capabilities: list[tuple[GoogleCapability, ...]] = []

    def start(self, **kwargs: object) -> GoogleOAuthStart:
        capabilities = kwargs["capabilities"]
        assert isinstance(capabilities, tuple)
        self.capabilities.append(capabilities)
        return GoogleOAuthStart(
            authorization_url="https://accounts.example/authorize",
            redirect_uri=str(kwargs["redirect_uri"]),
            expires_at="2099-01-01T00:00:00+00:00",
            requested_scopes=tuple(
                sorted(
                    {
                        scope
                        for capability in capabilities
                        for scope in capability.required_scopes
                    }
                )
            ),
        )


class _GoogleStatus:
    def __init__(self, connections: BuiltInConnectionService) -> None:
        self._connections = connections

    def status(
        self, authority: ExecutionAuthority, connection_id: str | None
    ) -> GoogleConnectionStatus:
        connection = self._connections.get_google_connection_for_authority(
            authority, connection_id
        )
        assert connection is not None
        return GoogleConnectionStatus(
            state="ready",
            connection_id=connection.connection_id,
            slug=connection.slug,
            display_name=connection.display_name,
            is_default=connection.is_default,
            configured=True,
            connected=True,
            client_id=connection.client_id,
            client_secret_present=True,
            account_email="owner@example.com",
            granted_scopes=(*GOOGLE_IDENTITY_SCOPES, GMAIL_READONLY_SCOPE),
            config_version=connection.config_version,
        )

    def capability_availability(
        self,
        authority: ExecutionAuthority,
        capability: GoogleCapability,
        connection_id: str | None,
    ) -> GoogleCapabilityAvailability:
        status = self.status(authority, connection_id)
        missing = tuple(sorted(capability.required_scopes - set(status.granted_scopes)))
        return GoogleCapabilityAvailability(
            capability=capability,
            available=not missing,
            connection_state=status.state,
            missing_scopes=missing,
        )


if __name__ == "__main__":
    GmailConnectionAPIScenario().test_scenario()
