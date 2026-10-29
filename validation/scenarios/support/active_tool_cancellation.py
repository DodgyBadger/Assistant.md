"""Real local provider and vault lock barriers for active-tool cancellation."""

import asyncio
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
        self.entered = Event()
        self.release = Event()
        self.mode = "active_tool"
        self.tool_name = "file_write"
        self.arguments = {}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        server = self.server
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        server.requests.append(request)
        is_child = "CHILD_GATE_REQUEST" in json.dumps(request["messages"])
        if server.mode == "active_tool" and is_child:
            server.entered.set()
            if not server.release.wait(30):
                return
        if server.mode == "review" and request["messages"][-1]["role"] != "tool":
            delta = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "review-file-call",
                        "type": "function",
                        "function": {
                            "name": server.tool_name,
                            "arguments": json.dumps(server.arguments),
                        },
                    }
                ],
            }
        elif server.mode == "active_tool" and not is_child:
            delta = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "active-code-call",
                        "type": "function",
                        "function": {
                            "name": "code_execution",
                            "arguments": json.dumps(
                                {
                                    "code": 'await file_write(operation="write", path="notes/active-effect.md", content="ACTIVE_EFFECT")\nawait delegate(prompt="CHILD_GATE_REQUEST", model="code-tool-model", tools=[])\n"COMPLETE"'
                                }
                            ),
                        },
                    }
                ],
            }
        else:
            delta = {"role": "assistant", "content": "Controlled follow-up completed."}
        chunks = []
        for value, finish in (
            (delta, None),
            ({}, "tool_calls" if "tool_calls" in delta else "stop"),
        ):
            payload = {
                "id": "chatcmpl-active-cancel",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "code-tool-model",
                "choices": [{"index": 0, "delta": value, "finish_reason": finish}],
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
def active_provider(app):
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
            "active-cancellation-provider-requests.json", server.requests
        )


@contextmanager
def hold_vault_file_lock(vault, target):
    from core.vault_state.file_mutations import vault_file_mutation_lock

    acquired, release = Event(), Event()

    def hold():
        with vault_file_mutation_lock(vault, target):
            acquired.set()
            release.wait(30)

    thread = Thread(target=hold, daemon=True)
    thread.start()
    assert acquired.wait(5)
    try:
        yield release
    finally:
        release.set()
        thread.join(5)
        assert not thread.is_alive()


async def wait_active_tool(app, task_id, name):
    async with asyncio.timeout(30):
        while True:
            response = await app.request_async(f"/api/tasks/{task_id}")
            assert response.status_code == 200
            task = response.json()
            if name in task["metadata"].get("active_tools", []):
                return task
            assert task["status"] not in {
                "completed",
                "failed",
                "cancelled",
                "timed_out",
            }, task
            await asyncio.sleep(0.05)
