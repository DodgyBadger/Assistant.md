"""Real local model/service inputs and redirect observation for rollback restart."""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import uuid4

from validation.scenarios.support.code_tool_chat import configure_provider

ORIGINAL = "original durable contents\n"
CHANGED = "real transactional tool mutation\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        server = self.server
        assert self.path == "/effect"
        server.effects.append(server.target.read_text())
        status = 503 if server.always_fail or len(server.effects) == 1 else 200
        body = b"temporarily unavailable" if status == 503 else b"side effect confirmed"
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        server = self.server
        assert self.path == "/v1/chat/completions"
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        server.calls.append(
            {"file_before_request": server.target.read_text(), "request": request}
        )
        returning = request["messages"][-1]["role"] == "tool"
        delta = (
            {"role": "assistant", "content": "TRANSACTIONAL_RESTART_COMPLETED"}
            if returning
            else {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "effect_" + uuid4().hex,
                        "type": "function",
                        "function": {
                            "name": "rollback_restart_workload",
                            "arguments": json.dumps(
                                {
                                    "endpoint": f"http://127.0.0.1:{server.server_port}/effect",
                                    "path": "notes/transactional.md",
                                    "content": CHANGED,
                                }
                            ),
                        },
                    }
                ],
            }
        )
        chunks = [
            {
                "id": "rollback-restart",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "code-tool-model",
                "choices": [{"index": 0, "delta": item, "finish_reason": finish}],
            }
            for item, finish in (
                (delta, None),
                ({}, "stop" if returning else "tool_calls"),
            )
        ]
        body = (
            "".join("data: " + json.dumps(item) + "\n\n" for item in chunks)
            + "data: [DONE]\n\n"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@contextmanager
def rollback_restart_server(app, target, *, always_fail=False):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.target, server.always_fail = target, always_fail
    server.calls, server.effects = [], []
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        configure_provider(app, server)
        configuration = app.configuration()
        configuration["tools"]["rollback_restart_workload"] = {
            "module": "validation.scenarios.support.rollback_restart_extension",
            "description": "Controlled transactional extension workload",
            "chat_visible": True,
        }
        app.install_configuration(configuration)
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()
        app.evidence.save_json(
            "rollback-restart-http-inputs.json",
            {
                "model_requests": server.calls,
                "service_mutation_observations": server.effects,
            },
        )


async def chat_with_redirects(app, data):
    from core.chat.task_execution import CHAT_TASK_EVENT_BUFFER

    response = await app.request_async("/api/chat/tasks", "POST", data)
    assert response.status_code == 200, response.text
    task_id = response.json()["task"]["task_id"]
    ids, events = [task_id], []
    cursor = 0
    async with asyncio.timeout(40):
        while True:
            buffered = await CHAT_TASK_EVENT_BUFFER.events_after(task_id, cursor)
            redirected = False
            for event in buffered:
                cursor = event.sequence
                row = {"task_id": task_id, "event": event.event, "data": event.data}
                events.append(row)
                if event.event == "chat_retry_redirect":
                    task_id = event.data["replacement_task_id"]
                    ids.append(task_id)
                    cursor, redirected = 0, True
                    break
                if event.is_terminal:
                    tasks = [await app.wait_task(identifier) for identifier in ids]
                    result = {
                        "task_ids": ids,
                        "tasks": tasks,
                        "events": events,
                        "terminal": row,
                    }
                    app.evidence.save_json("rollback-restart-chat-events.json", result)
                    return result
            if not redirected:
                await asyncio.sleep(0.01)
