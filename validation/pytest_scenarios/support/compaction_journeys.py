"""Real local recovery-card author and canonical compaction inputs."""

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from validation.pytest_scenarios.support.session_ops_chat import configure_provider


class AuthorProvider(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.requests = []


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        server = self.server
        assert self.path == "/v1/chat/completions"
        server.requests.append(
            json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        )
        result = f"Recovery card round {len(server.requests)}"
        chunks = []
        for delta, finish in (
            ({"role": "assistant", "content": result}, None),
            ({}, "stop"),
        ):
            payload = {
                "id": "chatcmpl-compaction",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "session-ops-model",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
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
def compaction_provider():
    server = AuthorProvider()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def configure_compaction(app, server, *, mode="suggested"):
    configure_provider(app, server)
    for key, value in (
        ("compaction_strategy", "recovery_card"),
        ("compaction_type", mode),
        ("compaction_retained_turns", "1"),
        ("compaction_high_watermark_tokens", "1"),
        ("compaction_author_model", "session-ops-model"),
        ("compaction_author_thinking", "low"),
    ):
        response = app.request(
            f"/api/system/settings/general/{key}", method="PUT", data={"value": value}
        )
        assert response.status_code == 200, response.text


def add_turn(store, vault, session, user, assistant):
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        UserPromptPart,
    )

    store.add_messages(
        session,
        vault,
        [
            ModelRequest(parts=[UserPromptPart(content=user)]),
            ModelResponse(parts=[TextPart(content=assistant)]),
        ],
    )


def author_payload(server, index):
    request = server.requests[index]
    for message in request["messages"]:
        content = message.get("content")
        if isinstance(content, str):
            try:
                payload = json.loads(content)
            except ValueError:
                continue
            if isinstance(payload, dict) and "older_history" in payload:
                return payload
    raise AssertionError(
        "Provider request did not include the structured compaction payload"
    )
