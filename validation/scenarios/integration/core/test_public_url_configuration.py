"""Canonical public origin parsing and configured application startup."""

import pytest

from validation.scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_public_origin_and_runtime_configuration(app: Application) -> None:
    app.evidence.register_secret("do-not-log", "secret")
    await app.start(public_url="https://Assistant.Example.com/")
    from core.mcp.oauth import resolve_mcp_oauth_redirect
    from core.runtime.config import RuntimeConfig
    from core.runtime.public_url import PublicOrigin, PublicUrlError
    from core.runtime.state import get_runtime_context
    from core.settings import AppSettings

    expected = {
        "https://Assistant.Example.com/": "https://assistant.example.com",
        "https://assistant.example.com:8443": "https://assistant.example.com:8443",
        "http://localhost:8000/": "http://localhost:8000",
        "http://127.42.0.1:8000": "http://127.42.0.1:8000",
        "http://[::1]:8000": "http://[::1]:8000",
    }
    for raw, normalized in expected.items():
        assert PublicOrigin.parse(raw).value == normalized
    for raw in (
        "http://assistant.example.com",
        "ftp://assistant.example.com",
        "https://user:secret@assistant.example.com",
        "https://assistant.example.com/app",
        "https://assistant.example.com?next=elsewhere",
        "https://assistant.example.com/#fragment",
        "https://assistant.example.com:invalid",
        "https://bad host.example.com",
        "https://bad_host.example.com",
    ):
        with pytest.raises(PublicUrlError):
            PublicOrigin.parse(raw)
    origin = PublicOrigin.parse("https://assistant.example.com")
    assert (
        origin.build_url("/api/system/callback")
        == "https://assistant.example.com/api/system/callback"
    )
    for path in (
        "api/callback",
        "//attacker.example/callback",
        "/../callback",
        "/%2e%2e/callback",
        "/callback?code=secret",
        "/callback#fragment",
        "/callback\\elsewhere",
        "/callback%5celsewhere",
        "/callback%0aelsewhere",
    ):
        with pytest.raises(PublicUrlError):
            origin.build_url(path)
    settings = AppSettings(ASSISTANTMD_PUBLIC_URL="https://Assistant.Example.com/")
    assert settings.public_url == "https://Assistant.Example.com/"
    production = RuntimeConfig.for_production(
        data_root=str(app.root / "data"),
        system_root=str(app.root / "system"),
        public_url=settings.public_url,
    )
    assert production.public_origin == origin
    with pytest.raises(PublicUrlError) as error:
        RuntimeConfig.for_production(
            data_root=str(app.root / "invalid-data"),
            system_root=str(app.root / "invalid-system"),
            public_url="https://user:do-not-log@example.com",
        )
    assert "do-not-log" not in str(error.value)
    assert (
        RuntimeConfig.for_validation(
            app.root, app.root / "validation-data"
        ).public_origin
        is None
    )
    assert get_runtime_context().config.public_origin == origin
    await app.controller.restart_system()
    assert get_runtime_context().config.public_origin == origin
    configured = resolve_mcp_oauth_redirect(
        connection_id="connection-1",
        public_origin=get_runtime_context().config.public_origin,
        fallback_uri="https://attacker.example/api/system/mcp/connections/connection-1/oauth/callback",
    )
    assert (configured.redirect_uri, configured.source) == (
        "https://assistant.example.com/api/system/mcp/connections/connection-1/oauth/callback",
        "configured",
    )
    fallback = resolve_mcp_oauth_redirect(
        connection_id="connection-1",
        public_origin=None,
        fallback_uri="https://browser.example/api/system/mcp/connections/connection-1/oauth/callback",
    )
    assert (fallback.redirect_uri, fallback.source) == (
        "https://browser.example/api/system/mcp/connections/connection-1/oauth/callback",
        "browser_fallback",
    )
