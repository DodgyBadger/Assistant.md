"""Real streaming model input with reasoning and a file-read tool call."""

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
        if len(server.requests) == 1:
            deltas = [
                {"role": "assistant", "reasoning_content": "thinking start "},
                {"reasoning_content": "thinking end"},
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "read-projection",
                            "type": "function",
                            "function": {
                                "name": "file_read",
                                "arguments": json.dumps(
                                    {"operation": "read", "path": "notes/private.md"}
                                ),
                            },
                        }
                    ]
                },
            ]
            finish = "tool_calls"
        else:
            deltas = [{"role": "assistant", "content": "Projection complete."}]
            finish = "stop"
        chunks = []
        for delta in [*deltas, {}]:
            payload = {
                "id": "chatcmpl-projection",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "code-tool-model",
                "choices": [
                    {
                        "index": 0,
                        "delta": delta,
                        "finish_reason": finish if not delta else None,
                    }
                ],
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
def projection_provider(app):
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
        app.evidence.save_json("projection-provider-requests.json", server.requests)
