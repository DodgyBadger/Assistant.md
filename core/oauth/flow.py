"""Provider-neutral OAuth authorization-code flow helpers."""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse


class OAuthCompletionError(ValueError):
    """Raised when OAuth callback or redirect input is invalid."""


@dataclass(frozen=True)
class OAuthPKCEState:
    """One generated OAuth state value and its PKCE parameters."""

    state: str
    code_verifier: str
    code_challenge: str
    code_challenge_method: str = "S256"

    @classmethod
    def generate(cls) -> OAuthPKCEState:
        """Generate cryptographically secure state and S256 PKCE parameters."""
        code_verifier = secrets.token_urlsafe(64)
        challenge_digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        code_challenge = (
            base64.urlsafe_b64encode(challenge_digest).rstrip(b"=").decode()
        )
        return cls(
            state=secrets.token_urlsafe(32),
            code_verifier=code_verifier,
            code_challenge=code_challenge,
        )

    def matches_state(self, returned_state: str) -> bool:
        """Compare returned state without leaking timing information."""
        return bool(returned_state) and secrets.compare_digest(
            returned_state, self.state
        )


def parse_oauth_completion(
    *, redirect_url: str | None, code: str | None, state: str | None
) -> tuple[str, str]:
    """Parse one authorization callback from a URL or explicit values."""
    if redirect_url:
        parsed = urlparse(redirect_url)
        query = parse_qs(parsed.query)
        code = _single_query_value(query, "code")
        state = _single_query_value(query, "state")
    if not code or not state:
        raise OAuthCompletionError("OAuth completion requires both code and state.")
    return code, state


def validate_redirect_uri(value: str) -> str:
    """Validate an absolute fragment-free HTTP OAuth redirect URI."""
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise OAuthCompletionError("OAuth redirect URI must be an absolute HTTP URL.")
    if parsed.username or parsed.password or parsed.fragment:
        raise OAuthCompletionError("OAuth redirect URI is invalid.")
    return parsed.geturl()


def required_query_value(url: str, key: str) -> str:
    """Return one required, non-empty authorization URL query value."""
    value = _single_query_value(parse_qs(urlparse(url).query), key)
    if value is None:
        raise OAuthCompletionError(f"OAuth authorization URL omitted {key}.")
    return value


def _single_query_value(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key, [])
    return values[0] if len(values) == 1 and values[0] else None
