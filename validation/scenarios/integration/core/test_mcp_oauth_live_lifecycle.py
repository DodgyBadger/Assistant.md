"""Actual OAuth discovery/start and callback over a local external provider."""

import pytest

from validation.scenarios.support.mcp_oauth_provider import (
    ACCESS,
    authorize,
    create_connection,
    oauth_provider,
)

pytestmark = pytest.mark.scenario


async def test_live_oauth_start_callback_state_recovery_and_disconnect(app):
    await app.start(public_url="https://assistant.example.test")
    async with oauth_provider(app) as (provider, mcp):
        runtime, connection = create_connection(app, provider)
        base = f"/api/system/mcp/connections/{connection.connection_id}/oauth"
        start = await app.request_async(base + "/start", "POST", {})
        assert start.status_code == 200, start.text
        started = start.json()
        assert provider.discovery
        callback, query = await authorize(app, provider, started["auth_url"])
        wrong = await app.request_async(
            callback, params={**query, "state": "wrong-state"}
        )
        assert wrong.status_code == 400
        assert provider.tokens == []
        completed = await app.request_async(callback, params=query)
        assert completed.status_code == 200, completed.text
        status = await app.request_async(base + "/status")
        assert status.json()["connected"] is True
        assert len(provider.tokens) == 1
        from validation.scenarios.support.mcp_oauth_provider import REFRESH, SECRET

        verifier = provider.tokens[0]["code_verifier"][0]
        app.evidence.register_secret(verifier)
        database = (app.root / "system/access.db").read_bytes()
        for private in (ACCESS, REFRESH, SECRET, verifier, started["state"]):
            assert private.encode() not in database
        assert any(
            row["rpc_method"] == "initialize"
            and row["authorization"] == "Bearer " + ACCESS
            for row in mcp.requests
        )
        replay = await app.request_async(callback, params=query)
        assert replay.status_code == 400 and len(provider.tokens) == 1
        disconnected = await app.request_async(base, "DELETE")
        assert disconnected.status_code == 200, disconnected.text
        assert (await app.request_async(base + "/status")).json()["connected"] is False
        import json

        activity = (app.root / "system/activity.log").read_text()
        events = [json.loads(line).get("data", {}) for line in activity.splitlines()]
        begun = [
            row for row in events if row.get("event") == "mcp_oauth_attempt_started"
        ]
        ended = [row for row in events if row.get("event") == "mcp_oauth_completed"]
        assert len(begun) == len(ended) == 1
        assert begun[0]["operation_id"] == ended[0]["operation_id"]
        for private in (ACCESS, REFRESH, SECRET, verifier, started["state"]):
            assert private not in activity


@pytest.mark.parametrize(
    "change", ["disconnect", "replace_secret", "delete", "supersede"]
)
async def test_live_completion_cannot_revive_a_revoked_authorization(app, change):
    await app.start()
    import asyncio

    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.mcp.oauth import MCPOAuthError
    from validation.scenarios.support.mcp_oauth_provider import authorization_journey

    async with oauth_provider(app) as (provider, _):
        async with authorization_journey(app, provider) as journey:
            first = await journey.start()
            _, query = await authorize(app, provider, first.auth_url)
            provider.token_release.clear()
            completing = asyncio.create_task(journey.complete(query))
            try:
                await asyncio.wait_for(provider.token_entered.wait(), 5)
                if change == "disconnect":
                    await journey.disconnect()
                elif change == "supersede":
                    newer = await journey.start()
                    assert newer.operation_id != first.operation_id
                else:
                    with use_execution_authority(LOCAL_USER_AUTHORITY):
                        if change == "replace_secret":
                            journey.runtime.mcp_connections.set_oauth_client_secret(
                                journey.connection.connection_id,
                                "replacement-client-secret",
                            )
                        else:
                            journey.runtime.mcp_connections.delete_connection(
                                journey.connection.connection_id
                            )
                provider.token_release.set()
                if change in ("disconnect", "supersede"):
                    with pytest.raises(asyncio.CancelledError):
                        await completing
                else:
                    with pytest.raises(MCPOAuthError):
                        await completing
                if change == "delete":
                    assert (
                        journey.runtime.mcp_connections.get_connection_for_authority(
                            LOCAL_USER_AUTHORITY, journey.connection.connection_id
                        )
                        is None
                    )
                else:
                    assert not (await journey.status()).connected
                if change == "supersede":
                    with pytest.raises(MCPOAuthError):
                        await journey.complete(query)
                    _, fresh = await authorize(app, provider, newer.auth_url)
                    result = await journey.complete(fresh)
                    assert (
                        result.connected and result.operation_id == newer.operation_id
                    )
                    cancelled = [
                        row
                        for row in journey.events()
                        if row.get("reason") == "superseded_by_new_attempt"
                    ]
                    assert (
                        len(cancelled) == 1
                        and cancelled[0]["operation_id"] == first.operation_id
                    )
                if change == "disconnect":
                    cancelled = [
                        row
                        for row in journey.events()
                        if row.get("reason") == "user_disconnect"
                    ]
                    assert (
                        len(cancelled) == 1
                        and cancelled[0]["operation_id"] == first.operation_id
                    )
            finally:
                provider.token_release.set()
                completing.cancel()
                await asyncio.gather(completing, return_exceptions=True)


@pytest.mark.parametrize("phase", ["start", "callback", "completion"])
async def test_actual_oauth_deadlines_fail_closed_and_record_the_operation(app, phase):
    await app.start()
    import asyncio

    from core.mcp.oauth import MCPOAuthError
    from validation.scenarios.support.mcp_oauth_provider import authorization_journey

    deadlines = {"start_seconds": 1, "callback_seconds": 2, "completion_seconds": 1}
    async with oauth_provider(app) as (provider, _):
        async with authorization_journey(app, provider, **deadlines) as journey:
            if phase == "start":
                provider.metadata_release.clear()
                starting = asyncio.create_task(journey.start())
                try:
                    await asyncio.wait_for(provider.metadata_entered.wait(), 3)
                    with pytest.raises(MCPOAuthError, match="did not start"):
                        await asyncio.wait_for(starting, 4)
                finally:
                    provider.metadata_release.set()
                    starting.cancel()
                    await asyncio.gather(starting, return_exceptions=True)
            else:
                started = await journey.start()
                _, query = await authorize(app, provider, started.auth_url)
                if phase == "callback":
                    await journey.wait_for_failure()
                    with pytest.raises(MCPOAuthError):
                        await journey.complete(query)
                    assert provider.tokens == []
                else:
                    provider.token_release.clear()
                    completing = asyncio.create_task(journey.complete(query))
                    try:
                        await asyncio.wait_for(provider.token_entered.wait(), 3)
                        with pytest.raises(MCPOAuthError, match="did not complete"):
                            await asyncio.wait_for(completing, 4)
                    finally:
                        provider.token_release.set()
                        completing.cancel()
                        await asyncio.gather(completing, return_exceptions=True)
                    assert len(provider.tokens) == 1
            assert not (await journey.status()).connected
            failures = [
                row
                for row in journey.events()
                if row.get("event") == "mcp_oauth_attempt_failed"
            ]
            assert failures and all(
                row["operation_id"] and row["error_type"] == "TimeoutError"
                for row in failures
            )


async def test_failed_live_supersede_retries_with_one_cancellation_and_a_new_operation(
    app,
):
    await app.start()
    import sqlite3

    from core.mcp.oauth import MCPOAuthError
    from validation.scenarios.support.mcp_oauth_inputs import reject_revocation
    from validation.scenarios.support.mcp_oauth_provider import authorization_journey

    async with oauth_provider(app) as (provider, _):
        async with authorization_journey(app, provider) as journey:
            first = await journey.start()
            _, old_query = await authorize(app, provider, first.auth_url)
            with reject_revocation(app), pytest.raises(sqlite3.IntegrityError):
                await journey.start()
            retry = await journey.start()
            assert retry.operation_id != first.operation_id
            with pytest.raises(MCPOAuthError):
                await journey.complete(old_query)
            _, query = await authorize(app, provider, retry.auth_url)
            result = await journey.complete(query)
            assert result.connected and result.operation_id == retry.operation_id
            cancellations = [
                row
                for row in journey.events()
                if row.get("reason") == "superseded_by_new_attempt"
            ]
            assert (
                len(cancellations) == 1
                and cancellations[0]["operation_id"] == first.operation_id
            )


async def test_shutdown_drains_completion_and_overlapping_supersede_and_rejects_new_work(
    app,
):
    await app.start()
    import asyncio

    from core.mcp.oauth import MCPOAuthError
    from validation.scenarios.support.mcp_oauth_provider import authorization_journey

    async with oauth_provider(app) as (provider, _):
        async with authorization_journey(app, provider) as journey:
            first = await journey.start()
            _, query = await authorize(app, provider, first.auth_url)
            provider.token_release.clear()
            completing = asyncio.create_task(journey.complete(query))
            superseding = None
            try:
                await asyncio.wait_for(provider.token_entered.wait(), 5)
                superseding = asyncio.create_task(journey.start())
                # Give the real start one loop turn to remove/cancel the held
                # old attempt and yield while its cancellation settles.
                await asyncio.sleep(0)
                await journey.coordinator.shutdown()
                with pytest.raises(asyncio.CancelledError):
                    await completing
                with pytest.raises(MCPOAuthError, match="closed"):
                    await superseding
                with pytest.raises(MCPOAuthError, match="closed"):
                    await journey.start()
                with pytest.raises(MCPOAuthError, match="closed"):
                    await journey.complete(query)
                provider.token_release.set()
                assert not (await journey.status()).connected
            finally:
                provider.token_release.set()
                for task in (completing, superseding):
                    if task is not None:
                        task.cancel()
                await asyncio.gather(
                    *(t for t in (completing, superseding) if t is not None),
                    return_exceptions=True,
                )


async def test_oauth_timeout_configuration_retains_defaults_and_rejects_unbounded_values(
    app,
):
    await app.start()
    from core.mcp.oauth import MCPOAuthTimeouts

    defaults = MCPOAuthTimeouts()
    assert (
        defaults.start_seconds,
        defaults.callback_seconds,
        defaults.completion_seconds,
    ) == (15, 600, 30)
    for field in ("start_seconds", "callback_seconds", "completion_seconds"):
        for value in (0, -1, float("nan"), float("inf")):
            with pytest.raises(ValueError, match="finite and positive"):
                MCPOAuthTimeouts(**{field: value})
