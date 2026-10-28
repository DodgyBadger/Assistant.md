"""Real local model workloads and public bound-tool inputs for delegate journeys."""

from __future__ import annotations

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from uuid import uuid4

from validation.scenarios.support.code_tool_chat import configure_provider


class DelegateProvider(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.calls = []
        self.arguments = {"prompt": "Return child output", "model": "code-tool-model"}
        self.child_call_count = 1
        self.error_status = None
        self.block = False
        self.entered = Event()
        self.release = Event()


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        server = self.server
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        server.calls.append(request)
        names = {item["function"]["name"] for item in request.get("tools", [])}
        returning = request["messages"][-1]["role"] == "tool"
        child = "delegate" not in names
        if child and server.block:
            server.entered.set()
            assert server.release.wait(15)
        if child and server.error_status:
            body = json.dumps(
                {
                    "error": {
                        "message": "controlled provider rejection",
                        "type": (
                            "insufficient_quota"
                            if server.error_status == 402
                            else "invalid_request_error"
                        ),
                        "code": (
                            "insufficient_quota"
                            if server.error_status == 402
                            else "invalid_request"
                        ),
                    }
                }
            ).encode()
            self.send_response(server.error_status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if returning or (child and "file_read" not in names):
            delta = {"role": "assistant", "content": "DELEGATE_LOCAL_COMPLETE"}
        else:
            delta = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": index,
                        "id": "call_" + uuid4().hex,
                        "type": "function",
                        "function": {
                            "name": "file_read" if child else "delegate",
                            "arguments": json.dumps(
                                {"operation": "read", "path": f"notes/{index}.md"}
                                if child
                                else server.arguments
                            ),
                        },
                    }
                    for index in range(server.child_call_count if child else 1)
                ],
            }
        chunks = [
            {
                "id": "delegate-local",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "code-tool-model",
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            },
            {
                "id": "delegate-local",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "code-tool-model",
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": (
                            "tool_calls" if "tool_calls" in delta else "stop"
                        ),
                    }
                ],
            },
        ]
        body = (
            "".join("data: " + json.dumps(item) + "\n\n" for item in chunks)
            + "data: [DONE]\n\n"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass


@contextmanager
def delegate_provider(app):
    server = DelegateProvider()
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
        app.evidence.save_json("delegate-model-http-inputs.json", server.calls)


async def invoke_delegate(vault, session_id, arguments):
    from core.authoring.helpers.runtime_common import (
        invoke_bound_tool,
        normalize_tool_result,
    )
    from core.authoring.shared.tool_binding import resolve_tool_binding
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority

    with use_execution_authority(LOCAL_USER_AUTHORITY):
        binding = resolve_tool_binding(["delegate"], vault_path=str(vault))
        result = await invoke_bound_tool(
            binding.tool_functions[0],
            tool_name="delegate",
            arguments=arguments,
            run_buffers={},
            session_buffers={},
            session_id=session_id,
            vault_name=vault.name,
        )
        return normalize_tool_result("delegate", result, vault_path=str(vault))
