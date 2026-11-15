"""Controlled external provider requests read, human handoff, then continuation."""

from __future__ import annotations

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import uuid4

from validation.support.chat.code_tool_chat import configure_provider


@contextmanager
def browser_handoff_provider(app, site):
    # Only remote model responses are simulated. Browser, chat orchestration,
    # persistence, authorization and native credential ingress remain real.
    # Raw requests remain in memory for the privacy oracle, never artifacts.
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            assert self.path == "/v1/chat/completions"
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(request)
            tools = [m for m in request["messages"] if m["role"] == "tool"]
            if len(tools) >= 2:
                delta = {"role": "assistant", "content": "Human handoff completed."}
                finish = "stop"
            else:
                args = (
                    {"interactive": True, "operation": "handoff"}
                    if tools
                    else {"interactive": True, "url": site.url("/login")}
                )
                delta = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "browser_" + uuid4().hex,
                            "type": "function",
                            "function": {
                                "name": "browser",
                                "arguments": json.dumps(args),
                            },
                        }
                    ],
                }
                finish = "tool_calls"
            body = (
                "".join(
                    "data: "
                    + json.dumps(
                        {
                            "id": "browser-handoff",
                            "object": "chat.completion.chunk",
                            "created": 1,
                            "model": "code-tool-model",
                            "choices": [
                                {"index": 0, "delta": item, "finish_reason": terminal}
                            ],
                        }
                    )
                    + "\n\n"
                    for item, terminal in ((delta, None), ({}, finish))
                )
                + "data: [DONE]\n\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        configure_provider(app, server)
        yield calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()
