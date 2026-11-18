"""Task-bound human ownership and guarded handback through real APIs/tools."""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic_ai.messages import ToolReturn

from core.runtime.state import get_runtime_context
from validation.support.application import Application
from validation.support.browser.handoff import handoff_path, waiting_handoff
from validation.support.browser.human_transport import frame_metadata
from validation.support.browser.retained_site import browser_turn, retained_browser_site

pytestmark = pytest.mark.scenario


async def test_handoff_blocks_agent_controls_and_returns_the_same_waiting_task(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = app.vault("BrowserHandoffOwnershipVault")
    await app.start()
    assert (
        app.request(
            "/api/system/settings/general/browser_min_memory_headroom_mb",
            "PUT",
            {"value": "0"},
        ).status_code
        == 200
    )
    with retained_browser_site(app, monkeypatch) as site:
        site.pages["/handoff"] = b"<main>Authorize</main><button>Continue</button>"
        site.cookie_paths.add("/handoff")
        initial = await browser_turn(
            app, vault.name, "handoff-owner", url=site.url("/handoff")
        )
        assert isinstance(initial, str)
        old_reference = json.loads(initial)["elements"][0]["ref"]
        operation = asyncio.create_task(
            browser_turn(app, vault.name, "handoff-owner", operation="handoff")
        )
        try:
            state = await waiting_handoff(app, operation, "handoff-owner")
            assert state["status"] == "waiting"
            assert state["browser_id"] == json.loads(initial)["browser_id"]
            coordinator = get_runtime_context().task_coordinator
            task = await coordinator.get_task(state["task_id"])
            assert task is not None and task.status == "running"
            assert task.metadata["browser_handoff"]["handoff_id"] == state["handoff_id"]
            for kwargs in (
                {},
                {"operation": "click", "reference": old_reference},
                {"operation": "handoff"},
            ):
                refused = await browser_turn(app, vault.name, "handoff-owner", **kwargs)
                assert (
                    isinstance(refused, ToolReturn)
                    and refused.metadata["reason"] == "human_control_active"
                )
            path = handoff_path("handoff-owner") + "/" + state["handoff_id"]
            claimed = await app.request_async(path + "/claim", "POST")
            assert claimed.status_code == 200 and claimed.json()["status"] == "human"
            returned = await app.request_async(path + "/return", "POST")
            assert (
                returned.status_code == 200 and returned.json()["status"] == "returned"
            )
            async with asyncio.timeout(5):
                result = await operation
            assert (
                isinstance(result, str)
                and json.loads(result)["task_id"] == state["task_id"]
            )
            terminal = await coordinator.get_task(state["task_id"])
            assert terminal is not None and terminal.status == "completed"
            duplicate = await app.request_async(path + "/return", "POST")
            assert (
                duplicate.status_code == 200
                and duplicate.json()["status"] == "returned"
            )
            stale = await browser_turn(
                app,
                vault.name,
                "handoff-owner",
                operation="click",
                reference=old_reference,
            )
            assert (
                isinstance(stale, ToolReturn)
                and stale.metadata["reason"] == "stale_reference"
            )
            resumed = await browser_turn(
                app, vault.name, "handoff-owner", url=site.url("/state")
            )
            assert (
                isinstance(resumed, str) and json.loads(resumed)["text"] == "Signed in"
            )
        finally:
            if not operation.done():
                operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
            # A stopped handoff remains human-owned; fixture runtime shutdown
            # owns cleanup if the acceptance assertion failed before handback.
            if operation.done() and not operation.cancelled():
                await browser_turn(app, vault.name, "handoff-owner", operation="close")


async def test_handoff_idle_expiry_is_not_extended_by_polling_or_duplicate_claims(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    from validation.support.browser.browser_runtime import descendant_identities
    from validation.support.browser.retained_site import wait_processes_gone

    vault = app.vault("BrowserHandoffExpiryVault")
    await app.start()
    assert (
        app.request(
            "/api/system/settings/general/browser_min_memory_headroom_mb",
            "PUT",
            {"value": "0"},
        ).status_code
        == 200
    )
    with retained_browser_site(app, monkeypatch) as site:
        for claim in (False, True):
            baseline = {row["pid"] for row in descendant_identities()}
            assert isinstance(
                await browser_turn(
                    app, vault.name, "handoff-expiry", url=site.url("/prime")
                ),
                str,
            )
            owned = [
                row for row in descendant_identities() if row["pid"] not in baseline
            ]
            operation = asyncio.create_task(
                browser_turn(app, vault.name, "handoff-expiry", operation="handoff")
            )
            try:
                state = await waiting_handoff(app, operation, "handoff-expiry")
                path = handoff_path("handoff-expiry") + "/" + state["handoff_id"]
                if claim:
                    assert (
                        await app.request_async(path + "/claim", "POST")
                    ).status_code == 200
                assert (
                    app.request(
                        "/api/system/settings/general/browser_idle_timeout_seconds",
                        "PUT",
                        {"value": "1"},
                    ).status_code
                    == 200
                )
                async with asyncio.timeout(6):
                    while not operation.done():
                        await app.request_async(handoff_path("handoff-expiry"))
                        if claim:
                            await app.request_async(path + "/claim", "POST")
                            await app.request_async(path + "/frame")
                        await asyncio.sleep(0.05)
                    failed = await operation
                assert (
                    isinstance(failed, ToolReturn)
                    and failed.metadata["reason"] == "browser_expired"
                )
                assert (
                    await app.request_async(path + "/return", "POST")
                ).status_code == 404
                await wait_processes_gone(owned)
            finally:
                if not operation.done():
                    operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)


async def test_stopped_handoff_keeps_human_control_and_requires_a_new_task_generation(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = app.vault("BrowserHandoffStopVault")
    await app.start()
    assert (
        app.request(
            "/api/system/settings/general/browser_min_memory_headroom_mb",
            "PUT",
            {"value": "0"},
        ).status_code
        == 200
    )
    with retained_browser_site(app, monkeypatch) as site:
        assert isinstance(
            await browser_turn(app, vault.name, "handoff-stop", url=site.url("/prime")),
            str,
        )
        first = asyncio.create_task(
            browser_turn(app, vault.name, "handoff-stop", operation="handoff")
        )
        second = None
        try:
            state = await waiting_handoff(app, first, "handoff-stop")
            old_path = handoff_path("handoff-stop") + "/" + state["handoff_id"]
            assert (
                await app.request_async(old_path + "/claim", "POST")
            ).status_code == 200
            metadata = frame_metadata(await app.request_async(old_path + "/frame"))
            await get_runtime_context().task_coordinator.cancel_task(state["task_id"])
            with pytest.raises(asyncio.CancelledError):
                await first
            stopped = await app.request_async(handoff_path("handoff-stop"))
            assert stopped.status_code == 200 and stopped.json()["status"] == "stopped"
            assert (await app.request_async(old_path + "/frame")).status_code == 409
            assert (
                await app.request_async(
                    old_path + "/input",
                    "POST",
                    {**metadata, "kind": "text", "text": "late"},
                )
            ).status_code == 409
            assert (
                await app.request_async(old_path + "/return", "POST")
            ).status_code == 409
            blocked = await browser_turn(app, vault.name, "handoff-stop")
            assert (
                isinstance(blocked, ToolReturn)
                and blocked.metadata["reason"] == "human_control_active"
            )
            second = asyncio.create_task(
                browser_turn(app, vault.name, "handoff-stop", operation="handoff")
            )
            async with asyncio.timeout(5):
                while True:
                    current = (
                        await app.request_async(handoff_path("handoff-stop"))
                    ).json()
                    if current["handoff_id"] != state["handoff_id"]:
                        break
                    await asyncio.sleep(0.05)
            assert current["task_id"] != state["task_id"]
            assert current["browser_id"] == state["browser_id"]
            assert (
                await app.request_async(old_path + "/return", "POST")
            ).status_code == 409
            assert (
                await app.request_async(old_path + "/close", "POST")
            ).status_code == 409
            path = handoff_path("handoff-stop") + "/" + current["handoff_id"]
            assert (await app.request_async(path + "/claim", "POST")).status_code == 200
            assert (
                await app.request_async(path + "/return", "POST")
            ).status_code == 200
            assert isinstance(await second, str)
            resumed = await browser_turn(
                app, vault.name, "handoff-stop", url=site.url("/state")
            )
            assert (
                isinstance(resumed, str) and json.loads(resumed)["text"] == "Signed in"
            )
        finally:
            for operation in (first, second):
                if operation is not None:
                    if not operation.done():
                        operation.cancel()
                    await asyncio.gather(operation, return_exceptions=True)


@pytest.mark.parametrize("surface", ["password", "hidden_otp", "iframe", "shadow"])
async def test_handback_requires_recognized_credential_fields_to_be_submitted_or_cleared(
    app: Application, monkeypatch: pytest.MonkeyPatch, surface: str
) -> None:
    vault = app.vault("BrowserHandoffCredentialsVault")
    await app.start()
    assert (
        app.request(
            "/api/system/settings/general/browser_min_memory_headroom_mb",
            "PUT",
            {"value": "0"},
        ).status_code
        == 200
    )
    sentinel = "synthetic-human-credential-remains-in-browser-314159"
    app.evidence.register_secret(sentinel)
    with retained_browser_site(app, monkeypatch) as site:
        field = f'<label>Password<input id="secret" type="password" value="{sentinel}"></label>'
        expression = 'document.querySelector("#secret")'
        if surface == "hidden_otp":
            field = f'<input id="secret" name="otp" autocomplete="one-time-code" style="display:none" value="{sentinel}">'
        if surface == "shadow":
            field = (
                '<div id="host"></div><script>document.querySelector("#host").attachShadow({mode:"open"}).innerHTML='
                + json.dumps(field)
                + ";</script>"
            )
            expression = (
                'document.querySelector("#host").shadowRoot.querySelector("#secret")'
            )
        page = (
            "<main>Authorize</main>"
            + field
            + '<script>fetch("/wait-mutation").then(()=>{'
            + expression
            + '.value="";fetch("/mutation-applied")});</script>'
        )
        if surface == "iframe":
            site.pages["/credential-frame"] = page.encode()
            page = '<main>Authorize</main><iframe src="/credential-frame"></iframe>'
        site.pages["/credential-form"] = page.encode()
        initial = await browser_turn(
            app, vault.name, "handoff-credentials", url=site.url("/credential-form")
        )
        assert isinstance(initial, str) and sentinel not in initial
        operation = asyncio.create_task(
            browser_turn(app, vault.name, "handoff-credentials", operation="handoff")
        )
        try:
            state = await waiting_handoff(app, operation, "handoff-credentials")
            path = handoff_path("handoff-credentials") + "/" + state["handoff_id"]
            assert (await app.request_async(path + "/claim", "POST")).status_code == 200
            refused = await app.request_async(path + "/return", "POST")
            assert (
                refused.status_code == 409
                and refused.json()["detail"] == "credentials_pending"
            )
            assert not operation.done()
            assert (
                await app.request_async(handoff_path("handoff-credentials"))
            ).json()["status"] == "human"
            # Controlled website behavior, through real HTTP and page script,
            # clears the field. No test or agent CDP input channel is used.
            site.mutate.set()
            assert await asyncio.to_thread(site.mutation_applied.wait, 3)
            assert (
                await app.request_async(path + "/return", "POST")
            ).status_code == 200
            assert isinstance(await operation, str)
        finally:
            site.mutate.set()
            if not operation.done():
                operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)


@pytest.mark.parametrize("proxied", [False, True])
async def test_handoff_api_enforces_owner_session_csrf_origin_and_chat_generation(
    app: Application, monkeypatch: pytest.MonkeyPatch, proxied: bool
) -> None:
    from core.authentication import CSRF_HEADER, OWNER_SESSION_COOKIE, OwnerSessionCodec
    from core.identity import (
        LOCAL_USER_AUTHORITY,
        ExecutionAuthority,
        use_execution_authority,
    )
    from validation.support.runtime.authentication import (
        OWNER_SECRET,
        authentication_application,
        authentication_client,
        owner_policy,
    )

    vault = app.vault("BrowserHandoffAuthorityVault")
    await app.start()
    assert (
        app.request(
            "/api/system/settings/general/browser_min_memory_headroom_mb",
            "PUT",
            {"value": "0"},
        ).status_code
        == 200
    )
    runtime = get_runtime_context()
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        runtime.chat_session_access.ensure_session("other-handoff-chat", vault.name)
    with use_execution_authority(ExecutionAuthority("handoff-other-owner")):
        runtime.chat_session_access.ensure_session("foreign-handoff-chat", vault.name)
    application = authentication_application(
        app, "owner_token", ASSISTANTMD_AUTH_SECRET=OWNER_SECRET
    )
    issued = OwnerSessionCodec(owner_policy(app)).issue()
    app.evidence.register_secret(issued.cookie_value, issued.csrf_token)
    with retained_browser_site(app, monkeypatch) as site:
        assert isinstance(
            await browser_turn(app, vault.name, "handoff-auth", url=site.url("/prime")),
            str,
        )
        operation = asyncio.create_task(
            browser_turn(app, vault.name, "handoff-auth", operation="handoff")
        )
        try:
            state = await waiting_handoff(app, operation, "handoff-auth")
            path = handoff_path("handoff-auth") + "/" + state["handoff_id"]
            async with authentication_client(app, application) as client:
                if proxied:
                    # TLS terminates at the external proxy; the container sees
                    # HTTP with its internal host, not the public HTTPS origin.
                    client.base_url = "http://assistant:8000"
                assert (
                    await client.get(handoff_path("handoff-auth"))
                ).status_code == 401
                assert (await client.post(path + "/close")).status_code == 401
                assert (await client.get(path + "/frame")).status_code == 401
                assert (await client.post(path + "/input", json={})).status_code == 401
                client.cookies.set(OWNER_SESSION_COOKIE, issued.cookie_value)
                viewed = await client.get(handoff_path("handoff-auth"))
                assert (
                    viewed.status_code == 200
                    and "no-store" in viewed.headers["cache-control"]
                )
                # External proxy rewriting only: real auth, routing and browser
                # services execute against the application under test.
                ingress = (
                    {"Host": "127.0.0.1:8000", "Sec-Fetch-Site": "same-origin"}
                    if proxied
                    else {}
                )
                for action in ("/claim", "/close"):
                    assert (
                        await client.post(path + action, headers=ingress)
                    ).status_code == 403
                headers = {
                    CSRF_HEADER: issued.csrf_token,
                    "Origin": "https://assistant.example.test",
                    **ingress,
                }
                assert (
                    await client.post(
                        path + "/claim",
                        headers={
                            CSRF_HEADER: issued.csrf_token,
                            "Origin": "https://other.example.test",
                            **({"Host": "127.0.0.1:8000"} if proxied else {}),
                        },
                    )
                ).status_code == 403
                assert (
                    await client.get(handoff_path("foreign-handoff-chat"))
                ).status_code == 404
                other_path = (
                    handoff_path("other-handoff-chat") + "/" + state["handoff_id"]
                )
                assert (
                    await client.post(other_path + "/return", headers=headers)
                ).status_code == 404
                assert (
                    await client.post(other_path + "/close", headers=headers)
                ).status_code == 404
                stale_path = handoff_path("handoff-auth") + "/" + "0" * 32
                assert (
                    await client.post(stale_path + "/return", headers=headers)
                ).status_code == 409
                assert (
                    await client.post(stale_path + "/close", headers=headers)
                ).status_code == 409
                assert not operation.done()
                assert (
                    await client.post(path + "/claim", headers=headers)
                ).status_code == 200
                pixels = await client.get(path + "/frame")
                metadata = frame_metadata(pixels)
                assert pixels.headers["cross-origin-resource-policy"] == "same-origin"
                assert pixels.headers["x-content-type-options"] == "nosniff"
                payload = {**metadata, "kind": "click", "x": 40, "y": 40}
                assert (
                    await client.post(path + "/input", json=payload, headers=ingress)
                ).status_code == 403
                wrong_origin = {
                    CSRF_HEADER: issued.csrf_token,
                    "Origin": "https://other.example.test",
                }
                assert (
                    await client.post(
                        path + "/input", json=payload, headers=wrong_origin
                    )
                ).status_code == 403
                assert (
                    await client.post(path + "/input", json=payload, headers=headers)
                ).status_code == 200
                for site in ("cross-site", "same-site", "none", "unknown"):
                    for action in ("/return", "/close", "/input"):
                        assert (
                            await client.post(
                                path + action,
                                json=payload if action == "/input" else None,
                                headers={**headers, "Sec-Fetch-Site": site},
                            )
                        ).status_code == 403
                assert not operation.done()
                frame_metadata(await client.get(path + "/frame"))
                assert (
                    await client.post(path + "/return", headers=headers)
                ).status_code == 200
                assert (await client.get(path + "/frame")).status_code == 409
                assert (
                    await client.post(path + "/input", json=payload, headers=headers)
                ).status_code == 409
            assert isinstance(await operation, str)
        finally:
            if not operation.done():
                operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)


@pytest.mark.parametrize("surface", ["frames", "nodes"])
async def test_uninspectable_credential_surface_does_not_grant_agent_control(
    app: Application, monkeypatch: pytest.MonkeyPatch, surface: str
) -> None:
    vault = app.vault("BrowserHandoffInspectionLimitVault")
    await app.start()
    assert (
        app.request(
            "/api/system/settings/general/browser_min_memory_headroom_mb",
            "PUT",
            {"value": "0"},
        ).status_code
        == 200
    )
    with retained_browser_site(app, monkeypatch) as site:
        site.pages["/excessive"] = (
            "<main>Authorize</main>"
            + (
                "<iframe srcdoc='<p>Frame</p>'></iframe>" * 9
                if surface == "frames"
                else "<i></i>" * 4100
            )
        ).encode()
        assert isinstance(
            await browser_turn(
                app, vault.name, "handoff-limit", url=site.url("/excessive")
            ),
            str,
        )
        operation = asyncio.create_task(
            browser_turn(app, vault.name, "handoff-limit", operation="handoff")
        )
        try:
            state = await waiting_handoff(app, operation, "handoff-limit")
            path = handoff_path("handoff-limit") + "/" + state["handoff_id"]
            assert (await app.request_async(path + "/claim", "POST")).status_code == 200
            response = await app.request_async(path + "/return", "POST")
            assert (
                response.status_code == 409
                and response.json()["detail"] == "handback_unavailable"
            )
            assert not operation.done()
            refused = await browser_turn(app, vault.name, "handoff-limit")
            assert (
                isinstance(refused, ToolReturn)
                and refused.metadata["reason"] == "human_control_active"
            )
        finally:
            if not operation.done():
                operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)


@pytest.mark.parametrize(
    ("mode", "peer", "credential", "admitted"),
    [
        ("disabled", "172.20.0.5", "none", True),
        ("loopback", "127.0.0.1", "none", True),
        ("loopback", "172.20.0.5", "none", False),
        ("owner_token", "172.20.0.5", "bearer", True),
        ("owner_token", "172.20.0.5", "none", False),
        ("trusted_proxy", "172.20.0.5", "assertion", True),
        ("trusted_proxy", "172.20.0.5", "none", False),
        ("trusted_proxy", "172.20.0.5", "wrong", False),
        ("trusted_proxy", "192.0.2.5", "assertion", False),
    ],
)
async def test_proxied_handoff_preserves_each_authentication_mode(
    app: Application,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    peer: str,
    credential: str,
    admitted: bool,
) -> None:
    from validation.support.browser.human_transport import human_transport
    from validation.support.runtime.authentication import (
        OWNER_SECRET,
        authentication_application,
    )

    vault = app.vault("BrowserProxyAuthVault")
    await app.start()
    assert (
        app.request(
            "/api/system/settings/general/browser_min_memory_headroom_mb",
            "PUT",
            {"value": "0"},
        ).status_code
        == 200
    )
    settings = {}
    if mode in {"owner_token", "trusted_proxy"}:
        settings["ASSISTANTMD_AUTH_SECRET"] = OWNER_SECRET
    if mode == "trusted_proxy":
        settings["ASSISTANTMD_AUTH_TRUSTED_PROXY_NETWORKS"] = "172.20.0.0/24"
    application = authentication_application(app, mode, **settings)
    with retained_browser_site(app, monkeypatch) as site:
        assert isinstance(
            await browser_turn(app, vault.name, "proxy-auth", url=site.url("/prime")),
            str,
        )
        operation = asyncio.create_task(
            browser_turn(app, vault.name, "proxy-auth", operation="handoff")
        )
        try:
            state = await waiting_handoff(app, operation, "proxy-auth")
            path = handoff_path("proxy-auth") + "/" + state["handoff_id"]
            # Only external ingress/peer metadata is controlled. The same real
            # runtime, middleware, identity fences and handoff services execute.
            async with human_transport(
                app, application=application, peer=peer
            ) as client:
                client.headers.clear()
                client.cookies.clear()
                client.headers.update(
                    {
                        "Host": "assistant:8000",
                        "Origin": "https://assistant.example.test",
                        "Sec-Fetch-Site": "same-origin",
                        # Untrusted forwarding claims must never turn a Docker
                        # bridge or unrelated peer into an admitted loopback.
                        "X-Forwarded-For": "127.0.0.1",
                    }
                )
                if credential == "bearer":
                    client.headers["Authorization"] = f"Bearer {OWNER_SECRET}"
                elif credential in {"assertion", "wrong"}:
                    client.headers["X-AssistantMD-Proxy-Assertion"] = (
                        OWNER_SECRET if credential == "assertion" else "invalid"
                    )
                claimed = await client.post(path + "/claim")
                assert claimed.status_code == (200 if admitted else 401)
                if not admitted:
                    assert (await client.get(path + "/frame")).status_code == 401
                    assert (await client.post(path + "/return")).status_code == 401
                    assert not operation.done()
                    continue_state = await app.request_async(handoff_path("proxy-auth"))
                    assert continue_state.json()["status"] == "waiting"
                    return
                assert claimed.json()["status"] == "human"
                assert not operation.done()
                # A valid credential must not bypass browser-origin protection.
                assert (
                    await client.post(
                        path + "/return", headers={"Sec-Fetch-Site": "cross-site"}
                    )
                ).status_code == 403
                assert not operation.done()
                assert (await client.post(path + "/return")).status_code == 200
                assert isinstance(await operation, str)
        finally:
            if not operation.done():
                operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
