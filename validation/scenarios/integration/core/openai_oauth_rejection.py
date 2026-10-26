"""Verify provider OAuth rejection details at the real API boundary."""

import httpx

from core.llm.openai_oauth import (
    OpenAIOAuthHTTPTokenAdapter,
    set_openai_oauth_token_adapter,
)
from validation.core.base_scenario import BaseScenario


class OpenAIOAuthRejectionScenario(BaseScenario):
    """Keep a useful provider rejection while protecting submitted credentials."""

    async def test_scenario(self) -> None:
        vault = self.create_vault("OpenAIOAuthErrorSanitizationVault")
        requests: list[httpx.Request] = []

        def reject(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if b"code=SUCCESS_CODE" in request.content:
                return httpx.Response(
                    200,
                    json={
                        "access_token": "PRIVATE_SAVED_ACCESS",
                        "refresh_token": "PRIVATE_REFRESH",
                        "expires_in": 1,
                        "account_id": "validation-account",
                    },
                )
            if b"grant_type=refresh_token" in request.content:
                return httpx.Response(
                    400,
                    json={
                        "error": "invalid_grant",
                        "error_description": "Refresh expired PRIVATE_REFRESH",
                        "access_token": "PRIVATE_RESPONSE_ACCESS",
                    },
                )
            return httpx.Response(
                400,
                json={
                    "error": "invalid_grant",
                    "error_description": "Authorization expired PRIVATE_CODE",
                    "access_token": "PRIVATE_ACCESS",
                },
            )

        try:
            await self.start_system()
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(reject)
            ) as client:
                set_openai_oauth_token_adapter(
                    OpenAIOAuthHTTPTokenAdapter(http_client=client)
                )
                enabled = self.call_api(
                    "/api/system/settings/general/openai_oauth_enabled",
                    method="PUT",
                    data={"value": "true"},
                )
                assert enabled.status_code == 200
                started = self.call_api(
                    "/api/system/providers/openai/oauth/start", method="POST", data={}
                )
                assert started.status_code == 200
                rejected = await self.call_api_async(
                    "/api/system/providers/openai/oauth/complete",
                    method="POST",
                    data={"state": started.json()["state"], "code": "PRIVATE_CODE"},
                )
                assert len(requests) == 1 and b"PRIVATE_CODE" in requests[0].content
                assert 400 <= rejected.status_code < 600
                assert "Authorization expired" in rejected.text
                assert "PRIVATE_CODE" not in rejected.text
                assert "PRIVATE_ACCESS" not in rejected.text
                status = self.call_api("/api/system/providers/openai/oauth/status")
                assert status.status_code == 200
                assert status.json()["oauth_account_id"] is None
                assert "PRIVATE_CODE" not in status.text
                assert "PRIVATE_ACCESS" not in status.text
                started = self.call_api(
                    "/api/system/providers/openai/oauth/start", method="POST", data={}
                )
                assert started.status_code == 200
                connected = await self.call_api_async(
                    "/api/system/providers/openai/oauth/complete",
                    method="POST",
                    data={"state": started.json()["state"], "code": "SUCCESS_CODE"},
                )
                assert connected.status_code == 200
                assert connected.json()["oauth_account_id"] == "validation-account"
                assert (
                    "PRIVATE_REFRESH" not in connected.text
                    and "PRIVATE_SAVED_ACCESS" not in connected.text
                )
                allowed = self.call_api(
                    "/api/system/settings/general/editable_builtin_providers",
                    method="PUT",
                    data={"value": '["openai"]'},
                )
                assert allowed.status_code == 200
                configured = self.call_api(
                    "/api/system/providers/openai",
                    method="PUT",
                    data={
                        "auth_mode": "oauth",
                        "oauth_api_key_fallback_enabled": False,
                    },
                )
                assert configured.status_code == 200
                result = await self.run_chat_task(
                    {
                        "vault_name": vault.name,
                        "prompt": "Check the provider.",
                        "session_id": "oauth_refresh_rejection",
                        "tools": [],
                        "model": "gpt-mini",
                    }
                )
                assert result["start_response"].status_code == 200
                assert result["terminal_event"].get("event") == "error"
                assert any(
                    b"grant_type=refresh_token" in request.content
                    for request in requests
                )
                status = self.call_api("/api/system/providers/openai/oauth/status")
                assert status.status_code == 200
                assert "Refresh expired" in status.json()["oauth_last_refresh_error"]
                for secret in (
                    "PRIVATE_REFRESH",
                    "PRIVATE_SAVED_ACCESS",
                    "PRIVATE_RESPONSE_ACCESS",
                ):
                    assert secret not in status.text
                    assert secret not in str(result["terminal_event"])
        finally:
            set_openai_oauth_token_adapter(None)
            await self.stop_system()
            self.teardown_scenario()
