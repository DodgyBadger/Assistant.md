"""MCP OAuth adapters preserve owner isolation and connection revision fences."""

from __future__ import annotations

import pytest

from validation.pytest_scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_mcp_oauth_storage_is_encrypted_and_fenced(app: Application) -> None:
    for secret in (
        "owner-access-token",
        "owner-refresh-token",
        "other-access-token",
        "discard-me",
        "preserve-after-rotation",
        "must-not-persist",
        "wrong-owner",
    ):
        app.evidence.register_secret(secret)
    await app.start()
    from fastmcp.client.auth.oauth import TokenStorageAdapter
    from mcp.shared.auth import OAuthToken

    from core.identity import ExecutionAuthority
    from core.mcp import (
        ConnectedMCPOAuth,
        EncryptedMCPOAuthStorage,
        MCPAuthMode,
        MCPConnectionCreate,
    )
    from core.mcp.oauth_storage import (
        has_mcp_oauth_tokens,
        mcp_oauth_http_client_factory,
    )
    from core.runtime.state import get_runtime_context
    from core.secrets import SecretGuardMismatchError, get_encrypted_secrets_service

    service = get_runtime_context().mcp_connections
    assert service is not None
    owner, other = ExecutionAuthority("oauth-owner"), ExecutionAuthority("oauth-other")
    url = "https://mail.example/mcp"
    first = service.create_connection_for_authority(
        owner,
        MCPConnectionCreate(
            display_name="OAuth owner", url=url, auth_mode=MCPAuthMode.OAUTH
        ),
    )
    second = service.create_connection_for_authority(
        other,
        MCPConnectionCreate(
            display_name="OAuth other", url=url, auth_mode=MCPAuthMode.OAUTH
        ),
    )
    owner_store = service.oauth_storage(owner, first.connection_id)
    other_store = service.oauth_storage(other, second.connection_id)
    foreign = EncryptedMCPOAuthStorage(
        secrets=get_encrypted_secrets_service(),
        authority=other,
        connection_id=first.connection_id,
        fence_token=owner_store._fence_token,
    )
    with pytest.raises(SecretGuardMismatchError):
        foreign.put_sync("forbidden", {"token": "wrong-owner"})
    with pytest.raises(SecretGuardMismatchError):
        foreign.get_sync("forbidden")
    owner_adapter = TokenStorageAdapter(async_key_value=owner_store, server_url=url)
    other_adapter = TokenStorageAdapter(async_key_value=other_store, server_url=url)
    await owner_adapter.set_tokens(
        OAuthToken(
            access_token="owner-access-token",
            refresh_token="owner-refresh-token",
            expires_in=3600,
        )
    )
    await other_adapter.set_tokens(
        OAuthToken(access_token="other-access-token", expires_in=1800)
    )
    assert (await owner_adapter.get_tokens()).access_token == "owner-access-token"
    assert (await other_adapter.get_tokens()).access_token == "other-access-token"
    assert await owner_adapter.get_token_expiry() is not None
    assert await has_mcp_oauth_tokens(storage=owner_store, mcp_url=url)
    database = (app.root / "system/access.db").read_bytes()
    assert all(
        secret not in database
        for secret in (
            b"owner-access-token",
            b"owner-refresh-token",
            b"other-access-token",
        )
    )
    await owner_adapter.clear()
    assert await owner_adapter.get_tokens() is None
    assert not await has_mcp_oauth_tokens(storage=owner_store, mcp_url=url)
    assert (await other_adapter.get_tokens()).access_token == "other-access-token"
    await owner_store.put("expired", {"secret": "discard-me"}, ttl=-1)
    assert await owner_store.get("expired") is None
    connected = ConnectedMCPOAuth(
        mcp_url=url, token_storage=other_store, allow_private_http=False
    )
    with pytest.raises(ValueError, match="Connect this server in System"):
        await connected.redirect_handler("https://identity.example/authorize")
    factory = mcp_oauth_http_client_factory(allow_private_http=False)
    async with factory(follow_redirects=True) as client:
        assert not client.follow_redirects and not client.trust_env
    service.disconnect_oauth(other, second.connection_id)
    rotated = service.oauth_storage(other, second.connection_id)
    await rotated.put("guarded-delete", {"token": "preserve-after-rotation"})
    with pytest.raises(SecretGuardMismatchError):
        await other_store.delete("guarded-delete")
    assert await rotated.get("guarded-delete") == {"token": "preserve-after-rotation"}
    with pytest.raises(SecretGuardMismatchError):
        await other_store.put("stale-write", {"token": "must-not-persist"})
    assert await rotated.get("stale-write") is None
    with pytest.raises(SecretGuardMismatchError):
        await other_store.get("guarded-delete")
