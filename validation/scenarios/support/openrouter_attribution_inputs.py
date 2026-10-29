"""Controlled local provider and scoped OpenRouter attribution environment."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread


class AttributionServer(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.requests = []


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        server: AttributionServer = self.server
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        server.requests.append(
            {"path": self.path, "headers": dict(self.headers), "body": body}
        )
        chunks = [
            {
                "id": "local-openrouter",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "openai/gpt-4o-mini",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": "Provider response."},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "local-openrouter",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "openai/gpt-4o-mini",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            },
        ]
        data = (
            "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
            + "data: [DONE]\n\n"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        pass


@contextmanager
def attribution_provider():
    server = AttributionServer()
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
def attribution_environment(enabled: bool):
    values = {
        "OPENROUTER_APP_URL": "https://assistant.example",
        "OPENROUTER_APP_TITLE": "Assistant.md",
    }
    previous = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if enabled:
                os.environ[key] = value
            else:
                os.environ.pop(key, None)
        yield values
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
