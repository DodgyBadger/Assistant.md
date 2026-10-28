"""Real streaming recovery-card provider gated during authoring."""

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


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        server = self.server
        server.requests.append(
            json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        )
        server.started.set()
        if not server.release.wait(30):
            return
        chunks = []
        for delta, finish in (
            ({"role": "assistant", "content": "Recovery card."}, None),
            ({}, "stop"),
        ):
            payload = {
                "id": "chatcmpl-gated",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "session-ops-model",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            chunks.append("data: " + json.dumps(payload) + "\n\n")
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
def gated_author():
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


def seed_conversation(store, vault, session):
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        UserPromptPart,
    )

    pairs = (
        ("Earlier decision to preserve.", "Earlier decision recorded."),
        ("Continue reviewing the plan.", "The review remains open."),
        ("Most recent question.", "Most recent response."),
    )
    messages = []
    for user, assistant in pairs:
        messages.extend(
            [
                ModelRequest(parts=[UserPromptPart(content=user)]),
                ModelResponse(parts=[TextPart(content=assistant)]),
            ]
        )
    store.add_messages(session, vault, messages)
