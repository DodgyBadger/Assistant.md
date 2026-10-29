"""Real model dialogue settles a read before a transactional append fails."""

from __future__ import annotations

import json
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from threading import Thread
from uuid import uuid4

from validation.scenarios.support.code_tool_chat import configure_provider
from validation.scenarios.support.rollback_restart_workload import CHANGED, Handler


class ReadThenAppendHandler(Handler):
    def do_POST(self):
        server = self.server
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        server.calls.append(
            {"file_before_request": server.target.read_text(), "request": request}
        )
        messages = request["messages"]
        last_tool = None
        if messages[-1]["role"] == "tool":
            call_id = messages[-1]["tool_call_id"]
            last_tool = next(
                call["function"]["name"]
                for message in reversed(messages)
                for call in message.get("tool_calls", [])
                if call["id"] == call_id
            )
        if last_tool == "rollback_append_workload":
            delta = {"role": "assistant", "content": "APPEND_RECOVERY_COMPLETE"}
        else:
            name = (
                "rollback_append_workload" if last_tool == "file_read" else "file_read"
            )
            arguments = (
                {
                    "endpoint": f"http://127.0.0.1:{server.server_port}/effect",
                    "path": "notes/transactional.md",
                    "content": CHANGED,
                }
                if last_tool
                else {"operation": "read", "path": "notes/transactional.md"}
            )
            delta = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "read_append_" + uuid4().hex,
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                ],
            }
        chunks = [
            {
                "id": "settled-read-append",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "code-tool-model",
                "choices": [{"index": 0, "delta": item, "finish_reason": finish}],
            }
            for item, finish in (
                (delta, None),
                ({}, "tool_calls" if "tool_calls" in delta else "stop"),
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


@contextmanager
def settled_read_append_server(app, target):
    server = ThreadingHTTPServer(("127.0.0.1", 0), ReadThenAppendHandler)
    server.target, server.always_fail = target, False
    server.calls, server.effects = [], []
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        configure_provider(app, server)
        configuration = app.configuration()
        configuration["tools"]["rollback_append_workload"] = {
            "module": "validation.scenarios.support.rollback_append_extension",
            "description": "Controlled transactional append",
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
            "settled-read-append-inputs.json",
            {
                "model_requests": server.calls,
                "service_file_observations": server.effects,
            },
        )
