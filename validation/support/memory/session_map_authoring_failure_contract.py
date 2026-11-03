"""A real provider that repeatedly authors an unsupported source citation."""

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread


class Server(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.requests = []
        self.mode = "provenance"


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        server = self.server
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        server.requests.append(request)
        tool = request["tools"][0]["function"]["name"]
        draft = {
            "trajectory": {
                "text": "Unsupported citation.",
                "sources": [{"start": 99, "end": 99}],
            },
            "entries": [],
        }
        if server.mode == "schema":
            draft = {"entries": [{}]}
        delta = {
            "role": "assistant",
            "tool_calls": [
                {
                    "index": 0,
                    "id": f"invalid-{len(server.requests)}",
                    "type": "function",
                    "function": {"name": tool, "arguments": json.dumps(draft)},
                }
            ],
        }
        chunks = []
        for value, finish in ((delta, None), ({}, "tool_calls")):
            payload = {
                "id": "chatcmpl-invalid",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "session-map-author",
                "choices": [{"index": 0, "delta": value, "finish_reason": finish}],
            }
            chunks.append("data: " + json.dumps(payload) + "\n\n")
        body = ("".join(chunks) + "data: [DONE]\n\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


@contextmanager
def invalid_author():
    server = Server()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
