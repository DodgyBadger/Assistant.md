"""Local structured-output provider for real session-map authoring."""

from __future__ import annotations

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any

from validation.scenarios.application import Application


class AuthorServer(ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), AuthorHandler)
        self.requests: list[dict[str, Any]] = []
        self.payloads: list[dict[str, Any]] = []


class AuthorHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        server: AuthorServer = self.server
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        server.requests.append(request)
        assert self.path == "/v1/chat/completions"
        payload = next(
            json.loads(message["content"])
            for message in request["messages"]
            if message.get("role") == "user" and isinstance(message.get("content"), str)
        )
        server.payloads.append(payload)
        sources = [item["source_range"] for item in payload["new_evidence_envelopes"]]
        draft = {
            "schema_version": 3,
            "trajectory": {
                "text": "The consulting offer advanced by reconciling a durable note with the current launch plan.",
                "sources": sources,
            },
            "entries": [
                *payload["previous_map"]["entries"],
                {
                    "id": "reconciled_legacy_evidence",
                    "kind": "orientation",
                    "state": "active",
                    "basis": "mixed",
                    "text": "Legacy and newly evicted evidence were reconciled.",
                    "sources": sources,
                },
            ],
        }
        output_tool = request["tools"][0]["function"]["name"]
        chunks = [
            {
                "id": "chatcmpl-author",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "session-map-author",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_author",
                                    "type": "function",
                                    "function": {
                                        "name": output_tool,
                                        "arguments": json.dumps(draft),
                                    },
                                }
                            ],
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chatcmpl-author",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "session-map-author",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
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
def author_provider():
    server = AuthorServer()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def configure_author(app: Application, server: AuthorServer) -> None:
    key = "session-map-author-fixture-key"
    url = f"http://127.0.0.1:{server.server_port}/v1"
    app.evidence.register_secret(key, url)
    for name, value in (
        ("SESSION_MAP_AUTHOR_URL", url),
        ("SESSION_MAP_AUTHOR_KEY", key),
    ):
        response = app.request(
            "/api/system/secrets", "PUT", {"name": name, "value": value}
        )
        assert response.status_code == 200, response.text
    response = app.request(
        "/api/system/providers/session-map-author-provider",
        "PUT",
        {"base_url": "SESSION_MAP_AUTHOR_URL", "api_key": "SESSION_MAP_AUTHOR_KEY"},
    )
    assert response.status_code == 200, response.text
    response = app.request(
        "/api/system/models/session-map-author",
        "PUT",
        {
            "provider": "session-map-author-provider",
            "model_string": "session-map-author",
            "capabilities": ["text"],
        },
    )
    assert response.status_code == 200, response.text
    response = app.request(
        "/api/system/settings/general/compaction_author_model",
        "PUT",
        {"value": "session-map-author"},
    )
    assert response.status_code == 200, response.text


async def run_reduction(
    store: Any, session: str, vault_name: str, readiness: Any
) -> Any:
    from core.chat.compaction import _run_stepped_session_map_reduction
    from core.identity import LOCAL_USER_AUTHORITY

    return await _run_stepped_session_map_reduction(
        session_id=session,
        vault_name=vault_name,
        readiness=readiness,
        authority=LOCAL_USER_AUTHORITY,
        store=store,
    )
