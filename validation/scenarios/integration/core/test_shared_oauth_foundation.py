"""Provider-neutral OAuth state is encrypted, isolated and expires."""

from __future__ import annotations

import base64
import hashlib

import pytest

from validation.scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_shared_oauth_storage_and_pkce(app: Application) -> None:
    for secret in ("owner-access-token", "pending-state"):
        app.evidence.register_secret(secret)
    await app.start()
    from core.identity import ExecutionAuthority
    from core.oauth import (
        EncryptedOAuthStorage,
        OAuthCompletionError,
        OAuthPKCEState,
        parse_oauth_completion,
        validate_redirect_uri,
    )
    from core.secrets import get_encrypted_secrets_service

    secrets = get_encrypted_secrets_service()
    owner = EncryptedOAuthStorage(
        secrets=secrets,
        authority=ExecutionAuthority("oauth-owner"),
        namespace="oauth.validation",
    )
    other = EncryptedOAuthStorage(
        secrets=secrets,
        authority=ExecutionAuthority("oauth-other"),
        namespace="oauth.validation",
    )
    await owner.put(
        "tokens", {"access_token": "owner-access-token"}, collection="provider"
    )
    assert await owner.get("tokens", collection="provider") == {
        "access_token": "owner-access-token"
    }
    assert await other.get("tokens", collection="provider") is None
    assert b"owner-access-token" not in (app.root / "system/access.db").read_bytes()
    await owner.put("pending", {"state": "pending-state"}, ttl=-1)
    assert await owner.get("pending") is None
    pkce = OAuthPKCEState.generate()
    app.evidence.register_secret(pkce.code_verifier)
    app.evidence.register_secret(pkce.state)
    challenge = (
        base64.urlsafe_b64encode(
            hashlib.sha256(pkce.code_verifier.encode("ascii")).digest()
        )
        .rstrip(b"=")
        .decode()
    )
    assert (pkce.code_challenge, pkce.code_challenge_method) == (challenge, "S256")
    assert pkce.matches_state(pkce.state) and not pkce.matches_state("wrong")
    assert parse_oauth_completion(
        redirect_url="https://assistant.example/callback?code=abc&state=xyz",
        code=None,
        state=None,
    ) == ("abc", "xyz")
    assert (
        validate_redirect_uri("https://assistant.example/callback")
        == "https://assistant.example/callback"
    )
    with pytest.raises(OAuthCompletionError):
        validate_redirect_uri("https://user:secret@assistant.example/callback")
