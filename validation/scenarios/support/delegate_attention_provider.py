"""A local child model requests the same missing file twice per delegate."""

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from validation.scenarios.support.code_tool_chat import configure_provider


class Server(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.requests = []


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        server = self.server
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        server.requests.append(request)
        round_number = (len(server.requests) - 1) % 3
        if round_number < 2:
            delta = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": f"attention-{len(server.requests)}",
                        "type": "function",
                        "function": {
                            "name": "file_read",
                            "arguments": json.dumps(
                                {
                                    "operation": "invalid-guard-operation",
                                    "path": "notes/exists.md",
                                }
                            ),
                        },
                    }
                ],
            }
        else:
            delta = {"role": "assistant", "content": "Child completed."}
        chunks = []
        for item, finish in (
            (delta, None),
            ({}, "tool_calls" if "tool_calls" in delta else "stop"),
        ):
            payload = {
                "id": "attention-child",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "code-tool-model",
                "choices": [{"index": 0, "delta": item, "finish_reason": finish}],
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
def attention_provider(app):
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
        app.evidence.save_json(
            "delegate-attention-provider-requests.json", server.requests
        )
