"""Structured author input that cites a verified historical fragment."""

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread


class Server(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.requests = []
        self.payloads = []


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        server = self.server
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        server.requests.append(request)
        payload = next(
            json.loads(row["content"])
            for row in request["messages"]
            if row.get("role") == "user" and isinstance(row.get("content"), str)
        )
        server.payloads.append(payload)
        draft = {
            "schema_version": 3,
            "trajectory": {
                "text": "Verified historical evidence informs the current turn.",
                "sources": [{"start": 0, "end": 0}, {"start": 6, "end": 6}],
            },
            "entries": [],
        }
        tool = request["tools"][0]["function"]["name"]
        delta = {
            "role": "assistant",
            "tool_calls": [
                {
                    "index": 0,
                    "id": "retrieved-author",
                    "type": "function",
                    "function": {"name": tool, "arguments": json.dumps(draft)},
                }
            ],
        }
        chunks = []
        for item, finish in ((delta, None), ({}, "tool_calls")):
            data = {
                "id": "chatcmpl-retrieved",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "session-map-author",
                "choices": [{"index": 0, "delta": item, "finish_reason": finish}],
            }
            chunks.append("data: " + json.dumps(data) + "\n\n")
        body = ("".join(chunks) + "data: [DONE]\n\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


@contextmanager
def retrieved_author():
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
