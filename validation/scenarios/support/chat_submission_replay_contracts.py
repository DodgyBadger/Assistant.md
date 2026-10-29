"""Bounded loopback provider for queued approvals and retained event streams."""

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread

from validation.scenarios.support.code_tool_chat import configure_provider


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.requests = []
        self.started = Event()
        self.release = Event()
        self.mode = "queue"


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        server = self.server
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        server.requests.append(request)
        user_messages = [
            row for row in request["messages"] if row.get("role") == "user"
        ]
        prompt = str(user_messages[-1].get("content", "")) if user_messages else ""
        if server.mode == "queue" and "FIRST_GATE" in prompt:
            server.started.set()
            if not server.release.wait(30):
                return
        if server.mode == "queue" and "QUEUED_WRITE" in prompt:
            deltas = [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "queued-write-call",
                            "type": "function",
                            "function": {
                                "name": "file_write",
                                "arguments": json.dumps(
                                    {
                                        "operation": "write",
                                        "path": "queued.md",
                                        "content": "QUEUED_EFFECT",
                                    }
                                ),
                            },
                        }
                    ],
                }
            ]
            finish = "tool_calls"
        elif server.mode == "overflow":
            deltas = [{"role": "assistant", "content": "x"}] + [
                {"content": "x"} for _ in range(699)
            ]
            finish = "stop"
        else:
            deltas = [{"role": "assistant", "content": "Controlled response."}]
            finish = "stop"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            for delta in deltas:
                self.wfile.write(self.chunk(delta, None))
                self.wfile.flush()
            self.wfile.write(self.chunk({}, finish))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    @staticmethod
    def chunk(delta, finish):
        payload = {
            "id": "chatcmpl-submission",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "code-tool-model",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        return ("data: " + json.dumps(payload) + "\n\n").encode()

    def log_message(self, format, *args):
        pass


@contextmanager
def submission_provider(app):
    server = Server()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        configure_provider(app, server)
        yield server
    finally:
        server.release.set()
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()
        app.evidence.save_json(
            "submission-replay-provider-requests.json", server.requests
        )


async def start_turn(app, vault, session, prompt, mode="normal"):
    response = await app.request_async(
        "/api/chat/tasks",
        method="POST",
        data={
            "vault_name": vault,
            "session_id": session,
            "prompt": prompt,
            "model": "code-tool-model",
            "chat_mode": mode,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["task"]["task_id"]
