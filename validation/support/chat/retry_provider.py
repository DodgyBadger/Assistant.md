"""Controlled real HTTP model replies for validation retries and pending-tool replay."""

from __future__ import annotations

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any


class RetryProvider(ThreadingHTTPServer):
    def __init__(self, mode: str) -> None:
        super().__init__(("127.0.0.1", 0), _RetryRequest)
        self.mode = mode
        self.calls: list[dict[str, Any]] = []


class _RetryRequest(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        server: RetryProvider = self.server
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        server.calls.append(request)
        count = len(server.calls)
        assert self.path == "/v1/chat/completions", self.path
        if server.mode == "structured":
            tool_name = request["tools"][0]["function"]["name"]
            arguments = {} if count == 1 else {"summary": "retried output"}
            delta = tool_delta(tool_name, arguments, count)
            finish_reason = "tool_calls"
        elif server.mode == "tool_retry" and count <= 2:
            arguments = (
                {"operation": "list_sessions", "limit": 0}
                if count == 1
                else {"operation": "list_sessions"}
            )
            delta = tool_delta("session_ops", arguments, count)
            finish_reason = "tool_calls"
        else:
            delta = {
                "role": "assistant",
                "content": "continued" if server.mode == "replay" else "Recovered",
            }
            finish_reason = "stop"
        body = "".join(
            "data: "
            + json.dumps(
                {
                    "id": "retry-model",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "code-tool-model",
                    "choices": [{"index": 0, "delta": value, "finish_reason": reason}],
                }
            )
            + "\n\n"
            for value, reason in ((delta, None), ({}, finish_reason))
        )
        encoded = (body + "data: [DONE]\n\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        pass


def tool_delta(name: str, arguments: dict[str, Any], count: int) -> dict[str, Any]:
    return {
        "role": "assistant",
        "tool_calls": [
            {
                "index": 0,
                "id": f"retry-call-{count}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    }


@contextmanager
def retry_provider(mode: str):
    server = RetryProvider(mode)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
