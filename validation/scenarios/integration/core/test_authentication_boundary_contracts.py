"""Real installation policy and ingress reject malformed or insufficient proof."""

from datetime import datetime, timedelta

import pytest

from validation.scenarios.support.authentication import (
    OWNER_SECRET,
    authentication_application,
    authentication_client,
    owner_policy,
)
from validation.scenarios.support.authentication_boundaries import websocket_result

pytestmark = pytest.mark.scenario


async def test_authentication_configuration_fails_closed_and_file_secret_admits(app):
    await app.start()
    from api.application import create_application
    from core.authentication import (
        AuthenticationConfigurationError,
        load_authentication_policy,
    )
    from core.runtime.state import get_runtime_context
    from core.settings import AppSettings

    secret_file = app.root / "ingress-secret"
    secret_file.write_text(OWNER_SECRET + "\n")
    common = {"ASSISTANTMD_AUTH_MODE": "owner_token"}
    invalid = [
        {},
        common,
        {**common, "ASSISTANTMD_AUTH_SECRET": "short"},
        {**common, "ASSISTANTMD_AUTH_SECRET": "é" * 32},
        {**common, "ASSISTANTMD_AUTH_SECRET": OWNER_SECRET + "\n"},
        {
            **common,
            "ASSISTANTMD_AUTH_SECRET": OWNER_SECRET,
            "ASSISTANTMD_AUTH_SECRET_FILE": str(secret_file),
        },
        {"ASSISTANTMD_AUTH_MODE": "disabled", "ASSISTANTMD_AUTH_SECRET": OWNER_SECRET},
        {"ASSISTANTMD_AUTH_MODE": "loopback", "ASSISTANTMD_AUTH_SECRET": OWNER_SECRET},
        {"ASSISTANTMD_AUTH_MODE": "trusted_proxy"},
        {
            "ASSISTANTMD_AUTH_MODE": "trusted_proxy",
            "ASSISTANTMD_AUTH_SECRET": OWNER_SECRET,
            "ASSISTANTMD_AUTH_PROXY_ASSERTION_HEADER": "bad header",
        },
        {
            "ASSISTANTMD_AUTH_MODE": "trusted_proxy",
            "ASSISTANTMD_AUTH_SECRET": OWNER_SECRET,
            "ASSISTANTMD_AUTH_TRUSTED_PROXY_NETWORKS": "172.20.0.1/24",
        },
    ]
    app.evidence.register_secret(OWNER_SECRET)
    for settings in invalid:
        with pytest.raises(AuthenticationConfigurationError) as failure:
            load_authentication_policy(AppSettings.model_validate(settings))
        assert OWNER_SECRET not in str(failure.value)
    policy = load_authentication_policy(
        AppSettings.model_validate(
            {**common, "ASSISTANTMD_AUTH_SECRET_FILE": str(secret_file)}
        )
    )
    assert OWNER_SECRET not in repr(policy)
    assert len(policy.derive_session_key()) == 32
    assert policy.derive_session_key() != OWNER_SECRET.encode()
    application = create_application(authentication_policy=policy)
    application.state.runtime = get_runtime_context()
    async with authentication_client(app, application) as client:
        assert (
            await client.get(
                "/api/status", headers={"Authorization": "Bearer " + OWNER_SECRET}
            )
        ).status_code == 200
        assert (
            await client.get(
                "/api/status", headers={"Authorization": "Bearer " + OWNER_SECRET + "x"}
            )
        ).status_code == 401


async def test_malformed_headers_cookies_and_websocket_admission(app):
    await app.start()
    from core.authentication import OWNER_SESSION_COOKIE, OwnerSessionCodec

    application = authentication_application(
        app, "owner_token", ASSISTANTMD_AUTH_SECRET=OWNER_SECRET
    )
    issued = OwnerSessionCodec(owner_policy(app)).issue()
    app.evidence.register_secret(issued.cookie_value, issued.csrf_token)
    invalid = [
        [("Authorization", value)]
        for value in [
            "Bearer",
            "Basic abc",
            "Bearer a b",
            "Bearer\tbad",
            "Bearer wrong",
        ]
    ]
    invalid += [
        [
            ("Authorization", "Bearer " + OWNER_SECRET),
            ("Authorization", "Bearer " + OWNER_SECRET),
        ],
        [
            (
                "Cookie",
                f"{OWNER_SESSION_COOKIE}={issued.cookie_value}; {OWNER_SESSION_COOKIE}={issued.cookie_value}",
            )
        ],
    ]
    for index, headers in enumerate(invalid):
        async with authentication_client(
            app, application, f"192.0.2.{index + 10}"
        ) as client:
            assert (await client.get("/api/status", headers=headers)).status_code == 401
    for index, cookie in enumerate(
        ["", "a", ".", "a.b.c", "a." + "x" * 5000, issued.cookie_value + "x"]
    ):
        async with authentication_client(
            app, application, f"192.0.2.{index + 40}"
        ) as client:
            assert (
                await client.get(
                    "/api/status",
                    headers={"Cookie": f"{OWNER_SESSION_COOKIE}={cookie}"},
                )
            ).status_code == 401
    from core.authentication import SessionVerificationError

    with pytest.raises(SessionVerificationError):
        OwnerSessionCodec(owner_policy(app)).verify("é.x")
    denied = await websocket_result(application)
    assert denied[0]["type"] == "websocket.close" and denied[0]["code"] == 4401
    accepted = await websocket_result(
        application, [(b"authorization", ("Bearer " + OWNER_SECRET).encode())]
    )
    # There is no WebSocket route at this API path. Authorized ingress reaches
    # the real router (ordinary unrouted close); this does not claim a WS feature.
    assert accepted[0]["type"] == "websocket.close" and accepted[0]["code"] == 1000
    app.evidence.save_json(
        "websocket-authentication.json",
        {"denied": denied, "authorized_unrouted": accepted},
    )
    for lifetime in [timedelta(0), timedelta(seconds=-1)]:
        with pytest.raises(ValueError):
            OwnerSessionCodec(owner_policy(app), lifetime=lifetime)
    with pytest.raises(ValueError):
        OwnerSessionCodec(owner_policy(app)).issue(now=datetime(2026, 1, 1))


async def test_real_ingress_throttle_expires_clears_on_success_and_bounds_peers(app):
    await app.start()
    from core.authentication import (
        AuthenticationFailureLimiter,
        AuthenticationMiddleware,
    )

    # Public middleware composition around the actual API/runtime. The inner
    # application's disabled policy leaves this outer production policy owning
    # admission. The supported clock input models monotonic time; no app service
    # or global clock is replaced, and real requests produce every outcome.
    now = [100.0]
    limiter = AuthenticationFailureLimiter(
        failure_limit=2,
        window_seconds=10,
        maximum_tracked_peers=2,
        clock=lambda: now[0],
    )
    api = authentication_application(app, "disabled")
    application = AuthenticationMiddleware(
        api, policy=owner_policy(app), failure_limiter=limiter
    )
    async with authentication_client(app, application, "192.0.2.1") as first:
        assert (await first.get("/api/status")).status_code == 401
        assert (
            await first.get(
                "/api/status", headers={"Authorization": "Bearer " + OWNER_SECRET}
            )
        ).status_code == 200
        assert (await first.get("/api/status")).status_code == 401
        assert (await first.get("/api/status")).status_code == 401
        assert (await first.get("/api/status")).status_code == 429
        now[0] += 11
        assert (await first.get("/api/status")).status_code == 401
        assert (await first.get("/api/status")).status_code == 401
        for peer in ["192.0.2.2", "192.0.2.3"]:
            async with authentication_client(app, application, peer) as other:
                assert (await other.get("/api/status")).status_code == 401
        assert (await first.get("/api/status")).status_code == 401


async def test_peer_admission_and_session_payload_claims_are_bounded(app):
    await app.start()
    import base64
    import json

    from core.authentication import OwnerSessionCodec

    loopback = authentication_application(app, "loopback")
    for peer, expected in (
        ("127.0.0.1", 200),
        ("::1", 200),
        ("127.0.0.2", 401),
        ("172.20.0.8", 401),
        ("192.168.1.4", 401),
        ("localhost", 401),
        ("", 401),
    ):
        async with authentication_client(app, loopback, peer) as client:
            response = await client.get(
                "/api/status", headers={"X-Forwarded-For": "127.0.0.1"}
            )
            assert response.status_code == expected, (peer, response.text)
    for allowlist in ("", "172.20.0.0/24,10.4.0.9/32"):
        proxy = authentication_application(
            app,
            "trusted_proxy",
            ASSISTANTMD_AUTH_SECRET=OWNER_SECRET,
            ASSISTANTMD_AUTH_TRUSTED_PROXY_NETWORKS=allowlist,
        )
        for peer, expected in (
            ("172.20.0.4", 200),
            ("10.4.0.9", 200),
            ("172.21.0.4", 401 if allowlist else 200),
        ):
            async with authentication_client(app, proxy, peer) as client:
                headers = {"X-AssistantMD-Proxy-Assertion": OWNER_SECRET}
                assert (
                    await client.get("/api/status", headers=headers)
                ).status_code == expected
                assert (
                    await client.get(
                        "/api/status",
                        headers={"X-AssistantMD-Proxy-Assertion": "wrong"},
                    )
                ).status_code == 401
    disabled = authentication_application(app, "disabled")
    accepted = await websocket_result(disabled)
    assert accepted[0]["type"] == "websocket.close" and accepted[0]["code"] == 1000
    issued = OwnerSessionCodec(owner_policy(app)).issue()
    payload_segment = issued.cookie_value.split(".")[0]
    payload = json.loads(
        base64.urlsafe_b64decode(payload_segment + "=" * (-len(payload_segment) % 4))
    )
    assert set(payload) == {"v", "iat", "exp", "csrf", "nonce"}
    assert OWNER_SECRET not in json.dumps(
        payload
    ) and issued.csrf_token not in json.dumps(payload)
    assert len(issued.cookie_value.encode()) <= 4096
