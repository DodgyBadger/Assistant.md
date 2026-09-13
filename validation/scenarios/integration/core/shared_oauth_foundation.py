"""Validate provider-neutral OAuth state and encrypted storage contracts."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

_direct_run_root: tempfile.TemporaryDirectory[str] | None = None
if __name__ == "__main__":
    from core.runtime.paths import set_bootstrap_roots

    _direct_run_root = tempfile.TemporaryDirectory(prefix="assistantmd-shared-oauth-")
    direct_root = Path(_direct_run_root.name)
    data_root = direct_root / "data"
    bootstrap_system_root = direct_root / "system"
    data_root.mkdir()
    bootstrap_system_root.mkdir()
    set_bootstrap_roots(data_root=data_root, system_root=bootstrap_system_root)

from core.identity import ExecutionAuthority  # noqa: E402
from core.oauth import (  # noqa: E402
    EncryptedOAuthStorage,
    OAuthCompletionError,
    OAuthPKCEState,
    parse_oauth_completion,
    validate_redirect_uri,
)
from core.secrets import EncryptedSecretsService, SecretKeyring  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class SharedOAuthFoundationScenario(BaseScenario):
    """Prove shared OAuth primitives are provider-neutral and owner-scoped."""

    async def test_scenario(self) -> None:
        system_root = self.run_path / "system"
        system_root.mkdir()
        secrets = EncryptedSecretsService(
            system_root=str(system_root),
            keyring=SecretKeyring(keys={1: bytes(range(32))}, active_version=1),
        )
        owner = ExecutionAuthority("oauth-owner")
        other = ExecutionAuthority("oauth-other")
        owner_storage = EncryptedOAuthStorage(
            secrets=secrets,
            authority=owner,
            namespace="oauth.validation",
        )
        other_storage = EncryptedOAuthStorage(
            secrets=secrets,
            authority=other,
            namespace="oauth.validation",
        )

        await owner_storage.put(
            "tokens",
            {"access_token": "owner-access-token"},
            collection="provider",
        )
        self.soft_assert_equal(
            await owner_storage.get("tokens", collection="provider"),
            {"access_token": "owner-access-token"},
            "OAuth state should round-trip for its owner and namespace",
        )
        self.soft_assert_equal(
            await other_storage.get("tokens", collection="provider"),
            None,
            "OAuth state must remain isolated between principals",
        )
        self.soft_assert(
            b"owner-access-token" not in (system_root / "access.db").read_bytes(),
            "OAuth state must remain encrypted at rest",
        )

        with patch("core.oauth.storage.time.time", return_value=100.0):
            await owner_storage.put("pending", {"state": "one"}, ttl=5)
        with patch("core.oauth.storage.time.time", return_value=106.0):
            self.soft_assert_equal(
                await owner_storage.get("pending"),
                None,
                "Expired pending OAuth state should delete itself on read",
            )

        pkce = OAuthPKCEState.generate()
        expected_challenge = (
            base64.urlsafe_b64encode(
                hashlib.sha256(pkce.code_verifier.encode("ascii")).digest()
            )
            .rstrip(b"=")
            .decode()
        )
        self.soft_assert_equal(
            (pkce.code_challenge, pkce.code_challenge_method),
            (expected_challenge, "S256"),
            "Shared OAuth PKCE generation should use the S256 contract",
        )
        self.soft_assert(
            pkce.matches_state(pkce.state) and not pkce.matches_state("wrong"),
            "OAuth state comparison should accept only the generated value",
        )

        self.soft_assert_equal(
            parse_oauth_completion(
                redirect_url="https://assistant.example/callback?code=abc&state=xyz",
                code=None,
                state=None,
            ),
            ("abc", "xyz"),
            "Shared completion parsing should support a pasted callback URL",
        )
        self.soft_assert_equal(
            validate_redirect_uri("https://assistant.example/callback"),
            "https://assistant.example/callback",
            "Shared redirect validation should preserve a safe callback",
        )
        try:
            validate_redirect_uri("https://user:secret@assistant.example/callback")
        except OAuthCompletionError:
            pass
        else:
            self.soft_assert(False, "Credential-bearing redirects must be rejected")

        self.assert_no_failures()
        self.teardown_scenario()


if __name__ == "__main__":
    asyncio.run(SharedOAuthFoundationScenario().test_scenario())
