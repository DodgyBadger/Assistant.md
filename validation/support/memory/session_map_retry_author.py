"""Real structured-output retries and a bounded in-flight author request."""

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread


class Server(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.requests = []
        self.started = Event()
        self.release = Event()
        self.mode = "schema_retry"


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        server = self.server
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        server.requests.append(request)
        if server.mode == "gate":
            server.started.set()
            if not server.release.wait(30):
                return
        first = len(server.requests) == 1
        if server.mode == "schema_retry" and first:
            draft = {"entries": [{}]}
        elif server.mode == "provenance_retry":
            source = 99 if first else 10
            draft = {
                "trajectory": {
                    "text": "Grounded trajectory.",
                    "sources": [{"start": source, "end": source}],
                },
                "entries": [],
            }
        else:
            draft = {"entries": []}
        tool = request["tools"][0]["function"]["name"]
        delta = {
            "role": "assistant",
            "tool_calls": [
                {
                    "index": 0,
                    "id": f"author-{len(server.requests)}",
                    "type": "function",
                    "function": {"name": tool, "arguments": json.dumps(draft)},
                }
            ],
        }
        chunks = []
        for item, finish in ((delta, None), ({}, "tool_calls")):
            data = {
                "id": "chatcmpl-author-retry",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "session-map-author",
                "choices": [{"index": 0, "delta": item, "finish_reason": finish}],
            }
            chunks.append("data: " + json.dumps(data) + "\n\n")
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
def retry_author():
    server = Server()
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
