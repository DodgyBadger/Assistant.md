"""Actual malformed SSE provider response followed by valid streaming output."""

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from validation.scenarios.support.session_ops_chat import configure_provider


class Server(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.requests = []


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        server = self.server
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        server.requests.append(request)
        if len(server.requests) == 1:
            body = b'data: {"private-sse-marker":\n\n'
        else:
            data = {
                "id": "chatcmpl-good",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "session-ops-model",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": "Recovered."},
                        "finish_reason": None,
                    }
                ],
            }
            done = {
                **data,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            body = (
                "data: "
                + json.dumps(data)
                + "\n\ndata: "
                + json.dumps(done)
                + "\n\ndata: [DONE]\n\n"
            ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


@contextmanager
def malformed_stream_provider(app):
    app.evidence.register_secret("private-sse-marker")
    server = Server()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        configure_provider(app, server)
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()
        app.evidence.save_json("malformed-stream-requests.json", server.requests)
