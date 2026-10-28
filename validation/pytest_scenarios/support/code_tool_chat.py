"""Configured OpenAI-compatible input for real code and approval tool calls."""

from __future__ import annotations

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any
from uuid import uuid4

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.chat_tasks import chat_turn

PROVIDER_KEY = "code-tool-validation-provider-key"


class CodeToolProvider(ThreadingHTTPServer):
    arguments: dict[str, Any]
    calls: list[dict[str, Any]]

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _CodeProviderRequest)
        self.arguments = {}
        self.calls = []
        self.tool_name = "code_execution"
        self.fail_after_tool = False
        self.rollback_path = None
        self.observed_mutation = None


class _CodeProviderRequest(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        server: CodeToolProvider = self.server
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        server.calls.append(request)
        assert self.path == "/v1/chat/completions", self.path
        # A model round after tool execution receives a tool message last. Every
        # new user turn requests the controlled real code_execution call instead.
        returning = request["messages"][-1]["role"] == "tool"
        if returning and server.fail_after_tool:
            if server.rollback_path is not None:
                server.observed_mutation = server.rollback_path.read_text(
                    encoding="utf-8"
                )
            body = json.dumps(
                {
                    "error": {
                        "type": "invalid_request_error",
                        "code": "invalid_request",
                        "message": "Deterministic provider rejection after actual tool mutation",
                    }
                }
            ).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        delta = (
            {
                "role": "assistant",
                "content": str(request["messages"][-1].get("content", "")),
            }
            if returning
            else {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_" + uuid4().hex,
                        "type": "function",
                        "function": {
                            "name": server.tool_name,
                            "arguments": json.dumps(server.arguments),
                        },
                    }
                ],
            }
        )
        chunks = [
            {
                "id": "chatcmpl-code-tool",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "code-tool-model",
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            },
            {
                "id": "chatcmpl-code-tool",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "code-tool-model",
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop" if returning else "tool_calls",
                    }
                ],
            },
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

    def log_message(self, format: str, *args: object) -> None:
        pass


@contextmanager
def code_tool_provider():
    server = CodeToolProvider()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def configure_provider(app: Application, server: CodeToolProvider) -> None:
    app.evidence.register_secret(PROVIDER_KEY)
    url = f"http://127.0.0.1:{server.server_port}/v1"
    app.evidence.register_secret(url)
    for name, value in (
        ("CODE_TOOL_PROVIDER_URL", url),
        ("CODE_TOOL_PROVIDER_KEY", PROVIDER_KEY),
    ):
        response = app.request(
            "/api/system/secrets", method="PUT", data={"name": name, "value": value}
        )
        assert response.status_code == 200, response.text
    response = app.request(
        "/api/system/providers/code-tool-provider",
        method="PUT",
        data={
            "base_url": "CODE_TOOL_PROVIDER_URL",
            "api_key": "CODE_TOOL_PROVIDER_KEY",
        },
    )
    assert response.status_code == 200, response.text
    response = app.request(
        "/api/system/models/code-tool-model",
        method="PUT",
        data={
            "provider": "code-tool-provider",
            "model_string": "code-tool-model",
            "capabilities": ["text"],
        },
    )
    assert response.status_code == 200, response.text


async def call_code_tool(app, server, vault, case_name, data):
    server.tool_name = "code_execution"
    server.arguments = code_case_arguments(case_name)
    payload = {key: value for key, value in data.items() if key != "tools"}
    payload["model"] = "code-tool-model"
    return await chat_turn(app, payload)


def code_case_arguments(case_name):
    if case_name == "allow_memory_read":
        return {
            "code": 'history = await retrieve_history(scope="session", limit=1)\nstr(history.item_count)'
        }
    if case_name == "discovery":
        return {}
    if case_name == "allow_cache_read":
        return {
            "code": 'artifact = await read_cache(ref="tool/web_extract/call_seeded")\nartifact.content if artifact.exists else "CACHE_NOT_FOUND"'
        }
    if case_name == "allow_file_read":
        return {
            "code": 'doc = await file_read(operation="read", path="notes/blocked.md")\ndoc.return_value'
        }
    if case_name == "dynamic_attribute_helpers":
        return {
            "code": 'doc = await file_read(operation="read", path="notes/blocked.md")\nhasattr(doc, "return_value") and getattr(doc, "return_value") == "BLOCKED_CONTENT"'
        }
    if case_name == "registered_values":
        return {
            "code": f'parsed = await parse_markdown(value={STRUCTURED_NOTE!r})\nhistory = await retrieve_history(scope="session", limit=1)\ndate.today("%Y") + ":" + parsed.sections[1].heading + ":" + history.items[0].to_text()'
        }
    if case_name == "private_method":
        return {"code": 'date._resolve("today", None)'}
    if case_name == "streaming_detail":
        return {
            "code": 'padding = "' + "a" * 260 + '"\n"STREAM_RESULT_' + "b" * 300 + '"'
        }
    if case_name == "allow_write":
        return {
            "code": 'await file_write(operation="write", path="notes/derived.md", content="DERIVED_RESULT")\n"WRITE_OK"'
        }
    if case_name == "allow_image_input":
        return {
            "code": 'draft = await delegate(\n    prompt="Read images/test_image.jpg and reply with IMAGE_OK.",\n    tools=["file_read"],\n    model="test",\n)\ndraft.return_value'
        }
    if case_name == "full_surface":
        return {
            "code": f'''cached = await read_cache(ref="tool/web_extract/call_seeded_full_surface")\nawait file_read(operation="read", path="notes/structured.md")\nparsed = await parse_markdown(value={STRUCTURED_NOTE!r})\nlisted = await file_read(operation="list", path="tasks")\npending = await pending_files(\n    operation="get",\n    items=listed,\n)\nawait pending_files(operation="complete", items=(pending.items[0],))\nhistory = await retrieve_history(scope="session", limit=1)\nassembled = await assemble_context(\n    history=history.items,\n    instructions="Keep the output concise.",\n)\ndraft = await delegate(\n    prompt=(\n        f"heading={{parsed.sections[1].heading}}; "\n        f"cached={{cached.content}}; "\n        f"messages={{len(assembled.messages)}}; "\n        f"listed={{listed.metadata.get('file_count')}}"\n    ),\n    instructions="Return one short deterministic line.",\n    model="test",\n)\nawait file_write(operation="write", path="notes/full-surface.md", content=draft.return_value)\nawait finish(status="completed", reason="full-surface-ok")\n"UNREACHABLE"'''
        }
    raise AssertionError(f"Unexpected scenario case: {case_name}")


STRUCTURED_NOTE = """---
name: Skill Example
description: Deterministic markdown structure fixture
---

# Overview

Intro text for the structured note.

## AI In Fiction

This section is about fictional AI examples.

```python
print("hello")
```

![Fixture image](../images/test_image.jpg)
"""

ROLLBACK_CODE = """
await file_write(
    operation="write",
    path="notes/code-execution-write.md",
    content="created inside code_execution\\n",
)
"CODE_EXECUTION_WRITE_OK"
"""
