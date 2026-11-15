"""Real private HTTP ingress without retaining frame or input bodies."""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx2 as httpx

from validation.support.application import Application


@asynccontextmanager
async def human_transport(app: Application, *, application=None):
    client = app.controller.get_api_test_client()
    # Same production application and middleware; only the network transport is
    # in-process. No evidence response hook may persist pixels or input text.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application or client.app, client=("testclient", 50000)
        ),
        base_url=str(client.base_url),
        headers={**client.headers, "Origin": str(client.base_url).rstrip("/")},
        cookies=client.cookies,
    ) as ingress:
        yield ingress


def frame_metadata(response: httpx.Response) -> dict:
    assert response.status_code == 200, "Human owner must receive browser pixels"
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content.startswith(b"\xff\xd8")
    assert 0 < len(response.content) <= 2_000_000
    assert "no-store" in response.headers["cache-control"]
    return {
        "page_id": response.headers["x-browser-page"],
        "view_ref": response.headers["x-browser-view"],
        "input_ref": response.headers["x-browser-input"],
        "sequence": int(response.headers["x-browser-sequence"]) + 1,
    }


@asynccontextmanager
async def claimed_browser(
    app: Application, monkeypatch, *, session_id: str, html: bytes | None = None
):
    import asyncio

    from validation.support.browser.handoff import handoff_path, waiting_handoff
    from validation.support.browser.retained_site import (
        browser_turn,
        retained_browser_site,
    )

    vault = app.vault("BrowserTransportBoundaryVault")
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
        site.pages["/human"] = (
            html
            or b'<style>body{margin:0}input{position:absolute;left:20px;top:20px;width:200px;height:40px}button{position:absolute;left:20px;top:80px;width:140px;height:40px}</style><form method="post" action="/submit"><input type="password" name="password"><button>Sign in</button></form>'
        )
        assert isinstance(
            await browser_turn(app, vault.name, session_id, url=site.url("/human")), str
        )
        operation = asyncio.create_task(
            browser_turn(app, vault.name, session_id, operation="handoff")
        )
        try:
            state = await waiting_handoff(app, operation, session_id)
            path = handoff_path(session_id) + "/" + state["handoff_id"]
            async with human_transport(app) as client:
                assert (await client.post(path + "/claim")).status_code == 200
                yield site, client, operation, path, state
        finally:
            if not operation.done():
                operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)


async def fresh_frame(client, path: str) -> dict:
    import asyncio

    async with asyncio.timeout(5):
        while True:
            response = await client.get(path + "/frame")
            if response.status_code == 200:
                return frame_metadata(response)
            assert response.status_code == 409 and response.json()["detail"] in {
                "frame_unavailable",
                "browser_busy",
            }, "Only unresolved read-only capture may be retried"
            await asyncio.sleep(0.05)


def driver_processes():
    import os
    from pathlib import Path

    from validation.support.browser.browser_runtime import descendant_identities

    rows = descendant_identities()
    drivers = [
        row
        for row in rows
        if row["parent"] == os.getpid()
        and b"run-driver"
        in Path(f"/proc/{row['pid']}/cmdline").read_bytes().split(b"\0")
    ]
    assert len(drivers) == 1
    owned = {drivers[0]["pid"]}
    while additions := {row["pid"] for row in rows if row["parent"] in owned} - owned:
        owned.update(additions)
    return drivers[0], [row for row in rows if row["pid"] in owned]


async def assert_viewer_tasks_settled() -> None:
    """Observe real actor coroutine resources without retaining task payloads."""
    import asyncio
    from pathlib import Path

    from core.browser import viewer

    source = str(Path(viewer.__file__).resolve())
    deadline = asyncio.get_running_loop().time() + 2
    count = 0
    while asyncio.get_running_loop().time() < deadline:
        count = sum(
            getattr(getattr(task.get_coro(), "cr_code", None), "co_filename", None)
            == source
            for task in asyncio.all_tasks()
        )
        if count == 0:
            return
        await asyncio.sleep(0.02)
    assert count == 0, "Private browser actor tasks remain after native I/O teardown"
