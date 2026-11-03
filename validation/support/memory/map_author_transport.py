"""Loopback structured map author that cites real requested canonical ranges."""

from __future__ import annotations

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread


class MapServer(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.requests = []
        self.payloads = []


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        server: MapServer = self.server
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        server.requests.append(request)
        payload = next(
            json.loads(message["content"])
            for message in request["messages"]
            if message.get("role") == "user" and isinstance(message.get("content"), str)
        )
        server.payloads.append(payload)
        ranges = [item["source_range"] for item in payload["new_evidence_envelopes"]]
        previous = payload["previous_map"].get("trajectory")
        start = previous["sources"][0]["start"] if previous else ranges[0]["start"]
        end = ranges[-1]["end"]
        draft = {
            "schema_version": 3,
            "trajectory": {
                "text": "The session preserves its cumulative working narrative.",
                "sources": [{"start": start, "end": end}],
            },
            "entries": payload["previous_map"]["entries"],
        }
        name = request["tools"][0]["function"]["name"]
        delta = {
            "role": "assistant",
            "tool_calls": [
                {
                    "index": 0,
                    "id": "call_map",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(draft)},
                }
            ],
        }
        chunks = [
            {
                "id": "chatcmpl-map",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "session-map-author",
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            },
            {
                "id": "chatcmpl-map",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "session-map-author",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
            },
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
def map_author():
    server = MapServer()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
