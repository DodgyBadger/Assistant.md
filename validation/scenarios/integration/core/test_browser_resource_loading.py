"""Real post-human navigation must retain the page's public CSS resources."""

import asyncio
import json
import socket
import time

import pytest

from validation.support.browser.human_transport import claimed_browser, fresh_frame
from validation.support.browser.retained_site import browser_turn

pytestmark = pytest.mark.scenario


async def test_post_human_navigation_applies_a_burst_of_public_stylesheets(
    app, monkeypatch
):
    async with claimed_browser(app, monkeypatch, session_id="stylesheet-handoff") as (
        site,
        client,
        operation,
        path,
        _state,
    ):
        for index in range(8):
            resource = f"/theme-{index}.css"
            site.content_types[resource] = "text/css; charset=utf-8"
            site.pages[resource] = f":root{{--theme-{index}:applied}}".encode()
        hosts = [f"cdn-{i}.resource-tests.example" for i in range(8)]
        site.dns_hosts.update(hosts)
        links = "".join(
            f'<link rel="stylesheet" href="{("http://" + hosts[index] + ":" + str(site.port)) if index >= 2 else ""}/theme-{index}.css">'
            for index in range(8)
        )
        site.submission_body = (
            "<!doctype html><html><head>"
            + links
            + """</head><body><main>Loading styles</main><script>
            addEventListener('load', () => {
                const style = getComputedStyle(document.documentElement);
                const complete = Array.from({length:8}, (_, i) =>
                    style.getPropertyValue('--theme-' + i).trim() === 'applied').every(Boolean);
                document.querySelector('main').textContent = complete
                    ? 'All stylesheet resources applied' : 'Stylesheet resources missing';
                fetch('/resources-finished');
            });
            </script></body></html>"""
        ).encode()
        original_dns = socket.getaddrinfo

        def delayed_public_dns(host, port, *args, **kwargs):
            # Only this declared external site's DNS latency is substituted.
            # Actual route admission, proxy, IP pinning and HTTP/CSS stay real.
            if host in site.dns_hosts:
                time.sleep(0.15)
            return original_dns(host, port, *args, **kwargs)

        monkeypatch.setattr(socket, "getaddrinfo", delayed_public_dns)
        metadata = await fresh_frame(client, path)
        response = await client.post(
            path + "/input", json={**metadata, "kind": "click", "x": 70, "y": 100}
        )
        assert response.status_code == 200
        assert await asyncio.to_thread(site.resource_load_complete.wait, 8)
        await fresh_frame(client, path)
        assert (await client.post(path + "/return")).status_code == 200
        assert isinstance(await operation, str)
        snapshot = await browser_turn(
            app, app.vault("BrowserTransportBoundaryVault").name, "stylesheet-handoff"
        )
        assert isinstance(snapshot, str)
        assert "All stylesheet resources applied" in json.loads(snapshot)["text"]
        assert sorted(site.resource_requests) == [
            f"/theme-{index}.css" for index in range(8)
        ]


async def test_shared_dns_validates_each_url_and_does_not_cache_or_cancel_other_callers(
    app, monkeypatch
):
    from threading import Event

    from core.browser.network import BrowserResolver
    from core.web.errors import WebUrlPolicyError

    await app.start()
    entered, release = Event(), Event()
    original_dns = socket.getaddrinfo
    addresses = ["93.184.215.14"]
    queries = []

    def controlled_dns(host, port, *args, **kwargs):
        if host != "shared-styles.example":
            return original_dns(host, port, *args, **kwargs)
        queries.append(host)
        entered.set()
        assert release.wait(10), "External DNS release was not supplied"
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (addresses[0], port or 443))
        ]

    monkeypatch.setattr(socket, "getaddrinfo", controlled_dns)
    resolver = BrowserResolver()
    first = asyncio.create_task(resolver.resolve("https://shared-styles.example/a.css"))
    second = None
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        second = asyncio.create_task(
            resolver.resolve("https://shared-styles.example/b.css")
        )
        await asyncio.sleep(
            0
        )  # Admit the second real caller before cancelling the first.
        for invalid in (
            "ftp://shared-styles.example/a.css",
            "https://user:synthetic-css-secret@shared-styles.example/a.css",
        ):
            with pytest.raises(WebUrlPolicyError):
                await resolver.resolve(invalid)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not second.done()
        release.set()
        assert (await second)[1] == ("93.184.215.14",)
        assert len(queries) == 1
        # A subsequent private answer must be rejected: shared work is not a
        # lasting DNS approval or permission to reach a private destination.
        addresses[0] = "127.0.0.1"
        with pytest.raises(WebUrlPolicyError):
            await resolver.resolve("https://shared-styles.example/c.css")
        assert len(queries) == 2
    finally:
        release.set()
        first.cancel()
        if second is not None:
            second.cancel()
        await asyncio.gather(
            first, *([second] if second is not None else []), return_exceptions=True
        )
        await resolver.close()


async def test_shared_dns_bounds_actual_jobs_and_waiting_callers_after_cancellation(
    app, monkeypatch
):
    from threading import Event, Lock

    from core.browser.network import BrowserNetworkError, BrowserResolver

    await app.start()
    entered, release, guard = Event(), Event(), Lock()
    queries = []
    original_dns = socket.getaddrinfo

    def controlled_dns(host, port, *args, **kwargs):
        if not host.endswith(".resource-budget.example"):
            return original_dns(host, port, *args, **kwargs)
        with guard:
            queries.append(host)
            if len(queries) == 4:
                entered.set()
        assert release.wait(10), "External DNS release was not supplied"
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", port or 443))
        ]

    monkeypatch.setattr(socket, "getaddrinfo", controlled_dns)
    resolver = BrowserResolver()
    owners = [
        asyncio.create_task(resolver.resolve(f"https://{i}.resource-budget.example/"))
        for i in range(32)
    ]
    callers = []
    closing = None
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        for task in owners:
            task.cancel()
        await asyncio.gather(*owners, return_exceptions=True)
        # Timing out a queued caller must leave its job owned as well.
        with pytest.raises(BrowserNetworkError, match="deadline"):
            await resolver.resolve("https://31.resource-budget.example/deadline")
        # Cancelled callers must not free running or queued job admissions.
        with pytest.raises(BrowserNetworkError):
            await resolver.resolve("https://overflow.resource-budget.example/")
        callers = [
            asyncio.create_task(
                resolver.resolve(f"https://0.resource-budget.example/{i}")
            )
            for i in range(32)
        ]
        await asyncio.sleep(0)  # Let the bounded set reach its real await points.
        with pytest.raises(BrowserNetworkError):
            await resolver.resolve("https://0.resource-budget.example/overflow")
        callers[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await callers[0]
        replacement = asyncio.create_task(
            resolver.resolve("https://0.resource-budget.example/replacement")
        )
        callers.append(replacement)
        await asyncio.sleep(0)
        assert not replacement.done(), "Cancellation did not release caller admission"
        assert len(queries) == 4
        # Shutdown must cancel all 28 queued lookups before they do external
        # work, while retaining the four real workers until they finish.
        closing = asyncio.create_task(resolver.close())
        await asyncio.sleep(0)
        assert not closing.done()
        assert len(queries) == 4
        release.set()
        values = await asyncio.gather(*callers[1:])
        await closing
        assert all(value[1] == ("93.184.215.14",) for value in values)
        assert len(queries) == 4
    finally:
        release.set()
        for task in owners + callers:
            task.cancel()
        await asyncio.gather(*owners, *callers, return_exceptions=True)
        if closing is not None:
            await closing
        else:
            await resolver.close()
