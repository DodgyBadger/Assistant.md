"""Loopback model provider gated before any streamed model event."""

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread


class IdleProvider(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.started = Event()
        self.release = Event()
        self.requests = []


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        server = self.server
        assert self.path == "/v1/chat/completions"
        server.requests.append(
            json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        )
        server.started.set()
        if not server.release.wait(30):
            return
        chunks = []
        for delta, finish in (
            ({"role": "assistant", "content": "idle response"}, None),
            ({}, "stop"),
        ):
            payload = {
                "id": "chatcmpl-idle",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "session-ops-model",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            chunks.append("data: " + json.dumps(payload) + "\n\n")
        body = ("".join(chunks) + "data: [DONE]\n\n").encode()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, format, *args):
        pass


@contextmanager
def idle_provider():
    server = IdleProvider()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
