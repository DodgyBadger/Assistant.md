"""A bounded human sign-in window shares authentication, never agent authority."""

from __future__ import annotations

import asyncio

import pytest

from validation.support.application import Application
from validation.support.browser.human_transport import (
    claimed_browser,
    frame_metadata,
    fresh_frame,
)
from validation.support.browser.retained_site import browser_turn

pytestmark = pytest.mark.scenario


async def test_human_sign_in_popup_restores_original_page_and_authentication(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    html = b"<style>body{margin:0}button{position:absolute;left:20px;top:20px;width:180px;height:40px}</style><button onclick=\"window.open('/popup-login','sign-in')\">Sign in</button>"
    html = html.replace(
        b"window.open('/popup-login','sign-in')",
        b"let popup=window.open('about:blank','sign-in');setTimeout(()=>popup.location='/popup-login',100)",
    )
    async with claimed_browser(
        app, monkeypatch, session_id="popup-sign-in", html=html
    ) as (site, client, operation, path, state):
        site.sign_in_on_submit = True
        site.pages["/popup-login"] = (
            b'<style>body{margin:0}input{position:absolute;left:20px;top:20px;width:200px;height:40px}</style><form method="post" action="/submit"><input type="password" name="password"></form>'
        )
        site.submission_body = (
            b'<script>opener.location.href="/after";window.close()</script>'
        )
        original = await fresh_frame(client, path)
        assert (
            await client.post(
                path + "/input", json={**original, "kind": "click", "x": 50, "y": 40}
            )
        ).status_code == 200
        async with asyncio.timeout(5):
            while True:
                response = await client.get(path + "/frame")
                if (
                    response.status_code == 200
                    and response.headers["x-browser-page"] != state["page_id"]
                ):
                    break
                await asyncio.sleep(0.05)
        popup = frame_metadata(response)
        assert response.headers["x-browser-root"] == state["page_id"]
        assert response.headers["x-browser-popup"] == popup["page_id"]
        assert response.headers["x-browser-window"] == "sign_in"
        sentinel = "synthetic-popup-password-71320"
        app.evidence.register_secret(sentinel)
        assert (
            await client.post(
                path + "/input", json={**popup, "kind": "click", "x": 50, "y": 40}
            )
        ).status_code == 200
        popup = await fresh_frame(client, path)
        assert (
            await client.post(
                path + "/input", json={**popup, "kind": "text", "text": sentinel}
            )
        ).status_code == 200
        popup = await fresh_frame(client, path)
        refused = await client.post(path + "/return")
        assert (
            refused.status_code == 409
            and refused.json()["detail"] == "credentials_pending"
        )
        assert (
            await client.post(path + "/page/" + state["page_id"])
        ).status_code == 200
        root_response = await client.get(path + "/frame")
        root = frame_metadata(root_response)
        assert root["page_id"] == state["page_id"]
        assert root_response.headers["x-browser-window"] == "original"
        assert (
            await client.post(
                path + "/input", json={**original, "kind": "click", "x": 50, "y": 40}
            )
        ).status_code == 409
        refused = await client.post(path + "/return")
        assert (
            refused.status_code == 409
            and refused.json()["detail"] == "credentials_pending"
        )
        assert (
            await client.post(path + "/page/" + popup["page_id"])
        ).status_code == 200
        assert (
            await client.post(
                path + "/input", json={**popup, "kind": "key", "key": "Enter"}
            )
        ).status_code == 409
        popup = await fresh_frame(client, path)
        assert (
            await client.post(
                path + "/input", json={**popup, "kind": "key", "key": "Enter"}
            )
        ).status_code == 200
        async with asyncio.timeout(5):
            while True:
                restored_response = await client.get(path + "/frame")
                if (
                    restored_response.status_code == 200
                    and restored_response.headers["x-browser-page"] == state["page_id"]
                ):
                    break
                await asyncio.sleep(0.05)
        frame_metadata(restored_response)
        assert restored_response.headers["x-browser-popup"] == ""
        assert (
            await client.post(path + "/page/" + popup["page_id"])
        ).status_code == 409
        assert site.submissions == [{"password": sentinel}]
        assert (await client.post(path + "/return")).status_code == 200
        assert isinstance(await operation, str)
        signed_in = await browser_turn(
            app,
            "BrowserTransportBoundaryVault",
            "popup-sign-in",
            url=site.url("/after"),
        )
        assert isinstance(signed_in, str) and "Signed in" in signed_in


async def test_extra_and_nested_popups_make_no_initial_website_request(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    html = b"""<style>body{margin:0}button{position:absolute;left:20px;width:180px;height:40px}</style><button style="top:20px" onclick="window.open('/popup-login','sign-in')">Sign in</button><button style="top:80px" onclick="window.open('/popup-extra','extra')">Extra</button>"""
    async with claimed_browser(
        app, monkeypatch, session_id="popup-limit", html=html
    ) as (site, client, operation, path, state):
        site.pages["/popup-login"] = (
            b"""<style>body{margin:0}button{position:absolute;left:20px;top:20px;width:180px;height:40px}</style><button onclick="window.open('/popup-nested','nested')">Nested</button>"""
        )
        first = await fresh_frame(client, path)
        assert (
            await client.post(
                path + "/input", json={**first, "kind": "click", "x": 50, "y": 40}
            )
        ).status_code == 200
        async with asyncio.timeout(5):
            while True:
                response = await client.get(path + "/frame")
                if (
                    response.status_code == 200
                    and response.headers["x-browser-page"] != state["page_id"]
                ):
                    break
                await asyncio.sleep(0.05)
        popup_id = response.headers["x-browser-page"]
        for opener in ("original", "sign_in"):
            if opener == "original":
                assert (
                    await client.post(path + "/page/" + state["page_id"])
                ).status_code == 200
            else:
                assert (
                    await client.post(path + "/page/" + popup_id)
                ).status_code == 200
            current = await fresh_frame(client, path)
            assert (
                await client.post(
                    path + "/input",
                    json={
                        **current,
                        "kind": "click",
                        "x": 50,
                        "y": 100 if opener == "original" else 40,
                    },
                )
            ).status_code == 200
            current = await fresh_frame(client, path)
            assert current["page_id"] == (
                state["page_id"] if opener == "original" else popup_id
            )
            assert (
                site.private_requests == []
            ), "Denied windows contacted their website before admission"
        assert (await client.post(path + "/return")).status_code == 200
        assert isinstance(await operation, str)


async def test_initial_post_popup_is_not_replayed_as_a_get_or_submitted(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    html = b"""<style>body{margin:0}button{position:absolute;left:20px;top:20px;width:180px;height:40px}</style><form method="post" action="/submit" target="sign-in"><input type="hidden" name="assertion" value="synthetic-unsupported-assertion"><button>Sign in</button></form>"""
    app.evidence.register_secret("synthetic-unsupported-assertion")
    async with claimed_browser(
        app, monkeypatch, session_id="popup-post", html=html
    ) as (site, client, operation, path, state):
        root = await fresh_frame(client, path)
        assert (
            await client.post(
                path + "/input", json={**root, "kind": "click", "x": 50, "y": 40}
            )
        ).status_code == 200
        root = await fresh_frame(client, path)
        assert root["page_id"] == state["page_id"]
        response = await client.get(path + "/frame")
        assert response.status_code == 200
        assert response.headers["x-browser-popup"] == ""
        assert response.headers["x-browser-popup-notice"] == "unsupported_navigation"
        assert site.submissions == []
        assert (await client.post(path + "/return")).status_code == 200
        assert isinstance(await operation, str)


async def test_private_popup_target_retires_its_context_without_contacting_target(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic_ai.messages import ToolReturn

    from validation.support.browser.human_transport import driver_processes
    from validation.support.browser.retained_site import wait_processes_gone

    html = b"""<style>body{margin:0}button{position:absolute;left:20px;top:20px;width:180px;height:40px}</style><button onclick="window.open('http://127.0.0.1:'+location.port+'/denied-target')">Private</button>"""
    async with claimed_browser(
        app, monkeypatch, session_id="popup-private", html=html
    ) as (site, client, operation, path, _state):
        root = await fresh_frame(client, path)
        _, owned = driver_processes()
        await client.post(
            path + "/input", json={**root, "kind": "click", "x": 50, "y": 40}
        )
        async with asyncio.timeout(10):
            failed = await operation
        assert (
            isinstance(failed, ToolReturn)
            and failed.metadata["reason"] == "browser_closed"
        )
        assert site.private_requests == []
        assert (await client.get(path + "/frame")).status_code == 404
        await wait_processes_gone(owned)


async def test_inactive_original_credentials_block_return_from_sign_in_window(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    html = b"""<style>body{margin:0}button{position:absolute;left:20px;top:20px;width:180px;height:40px}input{position:absolute;left:20px;top:80px;width:180px;height:40px}</style><button onclick="window.open('/popup-login','sign-in')">Sign in</button><input type="password">"""
    async with claimed_browser(
        app, monkeypatch, session_id="popup-root-credential", html=html
    ) as (site, client, operation, path, state):
        site.pages["/popup-login"] = b"<main>Provider sign-in</main>"
        sentinel = "synthetic-inactive-original-password"
        app.evidence.register_secret(sentinel)
        for values in (
            {"kind": "click", "x": 50, "y": 100},
            {"kind": "text", "text": sentinel},
            {"kind": "click", "x": 50, "y": 40},
        ):
            metadata = await fresh_frame(client, path)
            assert (
                await client.post(path + "/input", json={**metadata, **values})
            ).status_code == 200
        async with asyncio.timeout(5):
            while True:
                response = await client.get(path + "/frame")
                if (
                    response.status_code == 200
                    and response.headers["x-browser-page"] != state["page_id"]
                ):
                    break
                await asyncio.sleep(0.05)
        refused = await client.post(path + "/return")
        assert (
            refused.status_code == 409
            and refused.json()["detail"] == "credentials_pending"
        )
        assert (
            await client.post(path + "/page/" + state["page_id"])
        ).status_code == 200
        for values in (
            {"kind": "click", "x": 50, "y": 100},
            {"kind": "key", "key": "SelectAll"},
            {"kind": "key", "key": "Backspace"},
        ):
            metadata = await fresh_frame(client, path)
            assert (
                await client.post(path + "/input", json={**metadata, **values})
            ).status_code == 200
        await fresh_frame(client, path)
        assert (await client.post(path + "/return")).status_code == 200
        assert isinstance(await operation, str)
