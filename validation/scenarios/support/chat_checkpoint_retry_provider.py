"""Real loopback provider with two completed reads before stream interruption."""

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
        number = len(server.requests)
        if number <= 2:
            delta = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": f"read-{number}",
                        "type": "function",
                        "function": {
                            "name": "file_read",
                            "arguments": json.dumps(
                                {"operation": "read", "path": f"notes/{number}.md"}
                            ),
                        },
                    }
                ],
            }
            finish = "tool_calls"
        elif number == 3:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", "100000")
            self.end_headers()
            self.wfile.write(
                chunk(
                    {"role": "assistant", "content": "discarded partial response"}, None
                )
            )
            self.wfile.flush()
            self.close_connection = True
            return
        else:
            delta = {"role": "assistant", "content": "Recovered after checkpoint."}
            finish = "stop"
        body = chunk(delta, None) + chunk({}, finish) + b"data: [DONE]\n\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def chunk(delta, finish):
    value = {
        "id": "chatcmpl-checkpoint",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "code-tool-model",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return ("data: " + json.dumps(value) + "\n\n").encode()


@contextmanager
def checkpoint_retry_provider(app):
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
            "checkpoint-retry-provider-requests.json", server.requests
        )
