"""Loopback SSE overload and deterministic connection-refusal inputs."""

from __future__ import annotations

import json
import socket
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread


class StreamServer(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.requests = []
        self.healthy = False


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        server: StreamServer = self.server
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        server.requests.append({"body": request, "headers": dict(self.headers)})
        if server.healthy:
            chunks = [
                {
                    "id": "local-healthy",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "local-failure-model",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": "Recovered."},
                            "finish_reason": None,
                        }
                    ],
                },
                {
                    "id": "local-healthy",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "local-failure-model",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                },
            ]
        else:
            chunks = [
                {
                    "error": {
                        "message": "model overloaded",
                        "type": "overloaded_error",
                        "code": "overloaded_error",
                    }
                }
            ]
        body = (
            "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
            + "data: [DONE]\n\n"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


@contextmanager
def stream_failure_provider():
    server = StreamServer()
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
def refused_loopback_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    try:
        yield sock.getsockname()[1]
    finally:
        sock.close()


def configure_provider(app, port: int):
    url = f"http://127.0.0.1:{port}/v1"
    key = "stream-failure-provider-key"
    app.evidence.register_secret(url, key)
    for name, value in (("FAILURE_PROVIDER_URL", url), ("FAILURE_PROVIDER_KEY", key)):
        assert (
            app.request(
                "/api/system/secrets", "PUT", {"name": name, "value": value}
            ).status_code
            == 200
        )
    assert (
        app.request(
            "/api/system/providers/failure-provider",
            "PUT",
            {"base_url": "FAILURE_PROVIDER_URL", "api_key": "FAILURE_PROVIDER_KEY"},
        ).status_code
        == 200
    )
    assert (
        app.request(
            "/api/system/models/failure-model",
            "PUT",
            {
                "provider": "failure-provider",
                "model_string": "local-failure-model",
                "capabilities": ["text"],
            },
        ).status_code
        == 200
    )
    assert (
        app.request(
            "/api/system/settings/general/model_stream_retries", "PUT", {"value": "0"}
        ).status_code
        == 200
    )
