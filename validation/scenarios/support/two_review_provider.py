"""OpenAI-compatible loopback provider proposes two actual file-write calls."""

from __future__ import annotations

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread


class TwoReviewServer(ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), TwoReviewHandler)
        self.calls = []


class TwoReviewHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        server: TwoReviewServer = self.server
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        server.calls.append(request)
        assert self.path == "/v1/chat/completions"
        returning = request["messages"][-1]["role"] == "tool"
        delta = (
            {"role": "assistant", "content": "Review handled."}
            if returning
            else {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": index,
                        "id": f"review-call-{index + 1}",
                        "type": "function",
                        "function": {
                            "name": "file_write",
                            "arguments": json.dumps(arguments),
                        },
                    }
                    for index, arguments in enumerate(
                        (
                            {
                                "operation": "write",
                                "path": "Draft.md",
                                "content": "Proposed draft",
                                "overwrite": True,
                            },
                            {
                                "operation": "write",
                                "path": "Notes.md",
                                "content": "Proposed notes",
                                "overwrite": True,
                            },
                        )
                    )
                ],
            }
        )
        chunks = [
            {
                "id": "chatcmpl-review",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "code-tool-model",
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            },
            {
                "id": "chatcmpl-review",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "code-tool-model",
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop" if returning else "tool_calls",
                    }
                ],
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

    def log_message(self, format: str, *args: object) -> None:
        pass


@contextmanager
def two_review_provider():
    server = TwoReviewServer()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
