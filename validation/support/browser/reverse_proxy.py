"""Private real HTTP forwarding for proxy-facing browser journeys."""

from contextlib import asynccontextmanager
from dataclasses import replace
from urllib.parse import urlsplit

import httpx2
from starlette.requests import Request
from starlette.responses import StreamingResponse

from validation.support.browser.browser_runtime import app_http_listener


@asynccontextmanager
async def proxied_browser_application(application):
    # Only the external proxy is controlled. Requests cross two real HTTP
    # sockets; production middleware, streaming, routing and services stay real.
    # Do not retain bodies, private headers, browser pixels or provider data.
    upstream = urlsplit(application.url)
    seen = {"claim_forwarded": False, "same_origin_metadata": False}
    async with httpx2.AsyncClient(timeout=30, trust_env=False) as client:

        async def proxy(scope, receive, send):
            assert scope["type"] == "http"
            request = Request(scope, receive)
            headers = [
                (key, value)
                for key, value in scope["headers"]
                if key not in {b"host", b"connection"}
            ]
            headers.append((b"host", upstream.netloc.encode("ascii")))
            if request.url.path.endswith("/claim"):
                seen["claim_forwarded"] = True
                seen["same_origin_metadata"] = (
                    request.headers.get("sec-fetch-site") == "same-origin"
                )
            target = application.url + request.url.path
            if request.url.query:
                target += "?" + request.url.query
            async with client.stream(
                request.method, target, headers=headers, content=await request.body()
            ) as response:
                # StreamingResponse owns disconnect cancellation, including
                # an SSE stream whose browser disappears during a handoff.
                outgoing = StreamingResponse(
                    response.aiter_raw(), status_code=response.status_code
                )
                outgoing.raw_headers = [
                    (key, value)
                    for key, value in response.headers.raw
                    if key.lower() not in {b"connection", b"transfer-encoding"}
                ]
                await outgoing(scope, receive, send)

        async with app_http_listener(application.app, application=proxy) as url:
            yield replace(application, url=url)
    application.app.evidence.save_json("proxy-browser-ingress.json", seen)
    # Observe the actual wire at the receiving proxy: route-intercepted CDP
    # request objects omit browser-added Fetch Metadata even in all_headers().
    if seen["claim_forwarded"]:
        assert seen["same_origin_metadata"], "Browser omitted same-origin metadata"
