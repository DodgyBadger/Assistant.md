"""Real completed/failed effects and provider disconnects across recovery runs."""

from __future__ import annotations

import json
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from threading import Thread
from uuid import uuid4

from validation.scenarios.support.code_tool_chat import configure_provider
from validation.scenarios.support.rollback_restart_workload import CHANGED, Handler

MODULES = {
    "external_post_workload": "validation.scenarios.support.external_post_extension",
    "unknown_external_post_workload": "validation.scenarios.support.unknown_external_post_extension",
    "replay_safe_http_workload": "validation.scenarios.support.replay_safe_http_extension",
    "rollback_append_workload": "validation.scenarios.support.rollback_append_extension",
}


class EffectPolicyHandler(Handler):
    def do_POST(self):
        server = self.server
        if self.path == "/external":
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            server.external_posts.append(body)
            status = (
                200
                if server.mode == "cross_run_vault" or len(server.external_posts) > 1
                else 503
            )
            encoded = b"external operation committed"
            self.send_response(status)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            return
        assert self.path == "/v1/chat/completions"
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        server.calls.append(
            {"file_before_request": server.target.read_text(), "request": request}
        )
        messages = request["messages"]
        previous = None
        if messages[-1]["role"] == "tool":
            call_id = messages[-1]["tool_call_id"]
            previous = next(
                call["function"]["name"]
                for item in reversed(messages)
                for call in item.get("tool_calls", [])
                if call["id"] == call_id
            )
        if (
            server.mode == "cross_run_vault"
            and previous == "external_post_workload"
            and not server.disconnected
        ):
            # A truncated real response after headers/body delivery bypasses SDK
            # request retry and reaches the application's settled-snapshot path.
            server.disconnected = True
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
        if server.mode == "cross_run_vault":
            name = (
                "external_post_workload"
                if previous is None
                else "rollback_append_workload"
            )
            finished = previous == "rollback_append_workload"
        else:
            name = "file_read" if previous is None else server.target_tool
            finished = previous == server.target_tool
        if finished:
            delta = {"role": "assistant", "content": "EFFECT_POLICY_COMPLETE"}
        else:
            endpoint = f"http://127.0.0.1:{server.server_port}"
            arguments = (
                {"operation": "read", "path": "notes/transactional.md"}
                if name == "file_read"
                else {
                    "endpoint": endpoint
                    + ("/external" if "external_post" in name else "/effect")
                }
            )
            if name == "rollback_append_workload":
                arguments.update(path="notes/transactional.md", content=CHANGED)
            delta = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "policy_" + uuid4().hex,
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                ],
            }
        chunks = [
            {
                "id": "effect-policy",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "code-tool-model",
                "choices": [{"index": 0, "delta": item, "finish_reason": finish}],
            }
            for item, finish in (
                (delta, None),
                ({}, "stop" if finished else "tool_calls"),
            )
        ]
        encoded = (
            "".join("data: " + json.dumps(item) + "\n\n" for item in chunks)
            + "data: [DONE]\n\n"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


@contextmanager
def effect_policy_server(app, target, mode):
    server = ThreadingHTTPServer(("127.0.0.1", 0), EffectPolicyHandler)
    server.target, server.mode, server.always_fail = target, mode, False
    server.target_tool = {
        "failed_manual": "external_post_workload",
        "failed_unknown": "unknown_external_post_workload",
        "failed_readonly": "replay_safe_http_workload",
    }.get(mode)
    server.disconnected = False
    server.calls, server.effects, server.external_posts = [], [], []
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        configure_provider(app, server)
        configuration = app.configuration()
        for name, module in MODULES.items():
            configuration["tools"][name] = {
                "module": module,
                "description": "Controlled real effect-policy workload",
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
            "recovery-policy-real-effects.json",
            {
                "mode": mode,
                "model_requests": server.calls,
                "external_posts": server.external_posts,
                "get_file_observations": server.effects,
                "provider_disconnected": server.disconnected,
            },
        )
