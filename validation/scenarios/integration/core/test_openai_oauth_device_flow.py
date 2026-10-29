"""Real device-code HTTP adapter completes pending-to-connected authorization."""

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.openai_device_oauth import (
    ACCESS,
    API_KEY,
    AUTH_CODE,
    DEVICE_ID,
    REFRESH,
    USER_CODE,
    VERIFIER,
    device_provider,
    send_provider_request,
)

pytestmark = pytest.mark.scenario


async def test_device_oauth_pending_and_connected(app: Application) -> None:
    app.vault("OpenAIDeviceOAuthVault")
    await app.start()
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.llm.openai_oauth import (
        OPENAI_OAUTH_CLIENT_ID,
        OPENAI_OAUTH_DEVICE_REDIRECT_URI,
        OpenAIOAuthHTTPTokenAdapter,
        load_openai_oauth_device_pending_state,
        load_openai_oauth_token_state,
        set_openai_oauth_token_adapter,
    )
    from core.llm.openai_runtime import (
        DefaultOpenAIOAuthRuntimeAdapter,
        set_openai_oauth_runtime_adapter,
    )

    editable = app.request(
        "/api/system/settings/general/editable_builtin_providers",
        method="PUT",
        data={"value": '["openai"]'},
    )
    assert editable.status_code == 200
    enabled = app.request(
        "/api/system/settings/general/openai_oauth_enabled",
        method="PUT",
        data={"value": "true"},
    )
    assert enabled.status_code == 200
    with device_provider(app) as server, use_execution_authority(LOCAL_USER_AUTHORITY):
        base = f"http://127.0.0.1:{server.server_port}"
        app.evidence.register_secret(base)
        adapter = OpenAIOAuthHTTPTokenAdapter(
            token_url=base + "/token",
            device_user_code_url=base + "/usercode",
            device_token_url=base + "/poll",
            device_verification_url=base + "/verify",
        )
        set_openai_oauth_token_adapter(adapter)
        set_openai_oauth_runtime_adapter(
            DefaultOpenAIOAuthRuntimeAdapter(base_url=base)
        )
        for name, value in (
            ("DEVICE_TEST_OPENAI_KEY", API_KEY),
            ("DEVICE_TEST_OPENAI_URL", base),
        ):
            response = app.request(
                "/api/system/secrets", method="PUT", data={"name": name, "value": value}
            )
            assert response.status_code == 200
        configured = app.request(
            "/api/system/providers/openai",
            method="PUT",
            data={
                "api_key": "DEVICE_TEST_OPENAI_KEY",
                "base_url": "DEVICE_TEST_OPENAI_URL",
                "auth_mode": "oauth",
                "oauth_api_key_fallback_enabled": True,
            },
        )
        assert configured.status_code == 200, configured.text
        try:
            start = await app.request_async(
                "/api/system/providers/openai/oauth/device/start", method="POST"
            )
            assert start.status_code == 200 and start.json()["user_code"] == USER_CODE
            assert (
                start.json()["verification_url"] == base + "/verify"
                and start.json()["poll_interval_seconds"] == 1
            )
            pending = await app.request_async(
                "/api/system/providers/openai/oauth/device/check", method="POST"
            )
            assert pending.status_code == 200 and pending.json()["status"] == "pending"
            assert load_openai_oauth_device_pending_state() is not None
            connected = await app.request_async(
                "/api/system/providers/openai/oauth/device/check", method="POST"
            )
            assert (
                connected.status_code == 200
                and connected.json()["status"] == "connected"
            )
            assert (
                connected.json()["provider"]["oauth_account_id"] == "validation-account"
            )
            assert load_openai_oauth_device_pending_state() is None
            token = load_openai_oauth_token_state()
            assert (
                token is not None
                and token.access_token == ACCESS
                and token.refresh_token == REFRESH
            )
            assert [row["path"] for row in server.requests] == [
                "/usercode",
                "/poll",
                "/poll",
                "/token",
            ]
            assert server.requests[0]["payload"] == {
                "client_id": OPENAI_OAUTH_CLIENT_ID
            }
            assert server.requests[1]["payload"] == {
                "device_auth_id": DEVICE_ID,
                "user_code": USER_CODE,
            }
            form = server.requests[-1]["payload"]
            assert (
                form["code"] == [AUTH_CODE]
                and form["code_verifier"] == [VERIFIER]
                and form["redirect_uri"] == [OPENAI_OAUTH_DEVICE_REDIRECT_URI]
            )
            assert (
                ACCESS not in connected.text
                and REFRESH not in connected.text
                and DEVICE_ID not in connected.text
            )
            resolution = await send_provider_request()
            assert (
                resolution.effective_auth_mode == "oauth"
                and resolution.status == "oauth_connected"
            )
            assert server.requests[-1]["authorization"] == f"Bearer {ACCESS}"
            assert server.requests[-1]["account_id"] == "validation-account"
            disconnected = app.request(
                "/api/system/providers/openai/oauth", method="DELETE"
            )
            assert disconnected.status_code == 200
            resolution = await send_provider_request()
            assert (
                resolution.effective_auth_mode == "api_key"
                and resolution.status == "oauth_fallback"
            )
            assert server.requests[-1]["authorization"] == f"Bearer {API_KEY}"
            assert server.requests[-1]["account_id"] is None
        finally:
            set_openai_oauth_runtime_adapter(None)
            set_openai_oauth_token_adapter(None)
