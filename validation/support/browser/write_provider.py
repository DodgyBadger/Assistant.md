"""External model responses drive real browser writes and real recovery failures."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import uuid4

from validation.support.application import Application
from validation.support.browser.retained_site import BrowserSite
from validation.support.chat.code_tool_chat import configure_provider


@dataclass
class BrowserProvider:
    calls: list[dict] = field(default_factory=list)
    disconnected: bool = False


@contextmanager
def browser_write_provider(
    app: Application, site: BrowserSite
) -> Iterator[BrowserProvider]:
    """Only the external provider is simulated; browser/task/recovery stay real."""
    provider = BrowserProvider()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            assert self.path == "/v1/chat/completions"
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            provider.calls.append(request)
            tools = [
                message for message in request["messages"] if message["role"] == "tool"
            ]
            previous = None
            if tools:
                call = next(
                    call
                    for message in reversed(request["messages"])
                    for call in message.get("tool_calls", [])
                    if call["id"] == tools[-1]["tool_call_id"]
                )
                previous = json.loads(call["function"]["arguments"]).get(
                    "operation", "read"
                )
            if previous == "click" and not provider.disconnected:
                provider.disconnected = True
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", "100000")
                self.end_headers()
                self.wfile.write(
                    b'data: {"id":"broken","object":"chat.completion.chunk","created":1,"model":"code-tool-model","choices":[{"index":0,"delta":{"content":"partial"},"finish_reason":null}]}\n\n'
                )
                self.wfile.flush()
                self.close_connection = True
                return
            if previous == "click":
                delta = {"role": "assistant", "content": "Browser write recorded."}
                finish = "stop"
            else:
                args = {"interactive": True, "url": site.url("/write-form")}
                if tools:
                    # The real tool wrapper encloses web output in an untrusted
                    # data marker; parse the JSON payload actually sent to us.
                    content = tools[-1]["content"]
                    observed, _ = json.JSONDecoder().raw_decode(
                        content[content.index("{") :]
                    )
                    matches = [
                        item["ref"]
                        for item in observed["elements"]
                        if item["label"] == "Commit"
                    ]
                    assert len(matches) == 1
                    args = {
                        "interactive": True,
                        "operation": "click",
                        "reference": matches[0],
                    }
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
            chunks = [
                {
                    "id": "browser-write",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "code-tool-model",
                    "choices": [{"index": 0, "delta": item, "finish_reason": terminal}],
                }
                for item, terminal in ((delta, None), ({}, finish))
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

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        configure_provider(app, server)
        yield provider
    finally:
        site.release_submission.set()
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()
        app.evidence.save_json(
            "browser-write-provider.json",
            {
                "requests": provider.calls,
                "disconnected": provider.disconnected,
                "submissions": site.submissions,
            },
        )
