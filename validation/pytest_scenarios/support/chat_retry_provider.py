"""Local streaming provider for transport failures and bounded cancellation gates."""

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread


class RetryProvider(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.mode = "disconnect"
        self.requests = []
        self.started = Event()
        self.release = Event()


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        server = self.server
        assert self.path == "/v1/chat/completions"
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        server.requests.append(request)
        if (
            server.mode == "tool_then_gate"
            and request["messages"][-1]["role"] != "tool"
        ):
            delta = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "write-call",
                        "type": "function",
                        "function": {
                            "name": "code_execution",
                            "arguments": json.dumps(
                                {
                                    "code": 'await file_write(operation="write", path="notes/cancelled-chat-write.md", content="created before cancellation\\n")\n"DONE"'
                                }
                            ),
                        },
                    }
                ],
            }
            self.send_stream(
                [chunk(delta, None), chunk({}, "tool_calls"), b"data: [DONE]\n\n"]
            )
            return
        if server.mode == "disconnect":
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
        if server.mode == "fail_once":
            server.mode = "success"
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
        if server.mode == "tool_then_gate":
            server.started.set()
            if not server.release.wait(30):
                return
        self.send_stream(
            [
                chunk(
                    {"role": "assistant", "content": "recovered primary response"}, None
                ),
                chunk({}, "stop"),
                b"data: [DONE]\n\n",
            ]
        )

    def send_stream(self, chunks):
        body = b"".join(chunks)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, format, *args):
        pass


def chunk(delta, finish):
    payload = {
        "id": "chatcmpl-retry",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "session-ops-model",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return ("data: " + json.dumps(payload) + "\n\n").encode()


@contextmanager
def retry_provider():
    server = RetryProvider()
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
