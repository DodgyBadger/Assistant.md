"""Real account selection supplies each account's encrypted grant to HTTP."""

import pytest

from validation.scenarios.support.gmail_http import gmail_server, http_factory
from validation.scenarios.support.google_connections import grant

pytestmark = pytest.mark.scenario


async def test_selected_and_default_gmail_accounts_send_their_own_tokens(app):
    await app.start()
    from core.connections import GoogleConnectionCreate
    from core.identity import LOCAL_USER_AUTHORITY
    from core.integrations.google.gmail_service import GmailResourceService
    from core.runtime.state import get_runtime_context

    runtime = get_runtime_context()
    connections = []
    for name, default in (("Personal", True), ("Work", False)):
        connection = (
            runtime.built_in_connections.create_google_connection_for_authority(
                LOCAL_USER_AUTHORITY,
                GoogleConnectionCreate(
                    display_name=name, client_id="local-client", is_default=default
                ),
            )
        )
        runtime.google_connection.set_client_secret(
            LOCAL_USER_AUTHORITY, "local-client-secret", connection.connection_id
        )
        runtime.google_connection.save_token_state(
            LOCAL_USER_AUTHORITY, grant(app, name.lower()), connection.connection_id
        )
        connections.append(connection)
    app.evidence.register_secret("local-client-secret")
    with gmail_server(app) as provider:
        # Inject external HTTP only. Resource selection, OAuth token lookup,
        # encrypted storage and the internally constructed Gmail client run real.
        service = GmailResourceService(
            connections=runtime.built_in_connections,
            google=runtime.google_connection,
            oauth=runtime.google_oauth,
            http_client_factory=http_factory(provider),
        )
        observed = []
        for selector, expected in (
            (connections[0].slug, "personal"),
            (connections[1].slug, "work"),
            (None, "personal"),
        ):
            before = len(provider.requests)
            result, capped = await service.search(
                LOCAL_USER_AUTHORITY, query="subject:receipt", connection=selector
            )
            assert result.messages and not capped
            sent = provider.requests[before:]
            assert sent and all(
                row["authorization"] == f"Bearer {expected}-access-token"
                for row in sent
            )
            observed.append(
                {"selector": selector, "account": expected, "request_count": len(sent)}
            )
        app.evidence.save_json("selected-account-routing.json", observed)
