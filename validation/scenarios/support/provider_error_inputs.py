"""Loopback HTTP failure inputs for built-in Tavily and configured model APIs."""

from __future__ import annotations

import json
from contextlib import contextmanager
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread


class Provider(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.requests = []
        self.status = 503
        self.retry_after = "11"


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        server: Provider = self.server
        body = self.rfile.read(int(self.headers["Content-Length"]))
        server.requests.append(
            {"path": self.path, "headers": dict(self.headers), "body": json.loads(body)}
        )
        payload = json.dumps(
            {
                "error": {
                    "message": "Our servers are currently overloaded. Please try again later.",
                    "type": "server_error",
                }
            }
        ).encode()
        self.send_response(server.status)
        self.send_header("Retry-After", server.retry_after)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        pass


@contextmanager
def provider():
    server = Provider()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def tavily_service(server, query):
    from core.web.registry import WebStrategyRegistry, WebStrategySpec
    from core.web.service import WebCapabilityService
    from core.web.strategies.search_tavily import search_with_tavily

    registry = WebStrategyRegistry()
    endpoint = f"http://127.0.0.1:{server.server_port}/search?query={query}"
    registry.register(
        WebStrategySpec(
            "web_search",
            "tavily",
            partial(search_with_tavily, endpoint=endpoint),
            ("TAVILY_API_KEY",),
        )
    )
    return WebCapabilityService(registry)
