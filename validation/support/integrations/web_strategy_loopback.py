"""Loopback HTTP endpoints for actual registry and curl transport probes."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlencode
from urllib.request import urlopen


class Endpoint(ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), Handler)
        self.paths: list[str] = []


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        server: Endpoint = self.server
        server.paths.append(self.path)
        body = json.dumps(
            {
                "title": "Loopback result",
                "url": "https://example.com/result",
                "snippet": "observed response",
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


@contextmanager
def endpoint():
    server = Endpoint()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@contextmanager
def proxy_environment(server: Endpoint):
    names = (
        "http_proxy",
        "HTTP_PROXY",
        "https_proxy",
        "HTTPS_PROXY",
        "all_proxy",
        "ALL_PROXY",
        "no_proxy",
        "NO_PROXY",
    )
    before = {name: os.environ.get(name) for name in names}
    proxy = f"http://127.0.0.1:{server.server_port}"
    try:
        for name in names:
            os.environ[name] = "" if name.lower() == "no_proxy" else proxy
        yield
    finally:
        for name, value in before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def search_handler(server: Endpoint, strategy: str):
    async def search(*, query: str, max_results: int):
        from core.web.models import WebSearchItem, WebSearchResult

        url = f"http://127.0.0.1:{server.server_port}/search?{urlencode({'q': query, 'limit': max_results})}"

        def fetch():
            with urlopen(url, timeout=5) as response:
                return json.load(response)

        data = await asyncio.to_thread(fetch)
        return WebSearchResult(
            strategy=strategy, query=query, items=[WebSearchItem(**data)]
        )

    return search
