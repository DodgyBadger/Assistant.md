"""Controlled OpenAI-compatible model input and canonical discovery fixtures."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any
from uuid import uuid4

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.chat_tasks import chat_turn

PRIVATE_QUERY = "private search query"
PROVIDER_KEY = "session-ops-validation-provider-key"


class SessionOpsProvider(ThreadingHTTPServer):
    arguments: dict[str, Any]
    calls: list[dict[str, Any]]

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _ProviderRequest)
        self.arguments = {}
        self.calls = []


class _ProviderRequest(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        server: SessionOpsProvider = self.server
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        server.calls.append(request)
        assert self.path == "/v1/chat/completions", self.path
        # A model round after tool execution receives a tool message last. Every
        # new user turn requests the controlled real session_ops call instead.
        returning = request["messages"][-1]["role"] == "tool"
        delta = (
            {"role": "assistant", "content": "Lookup complete."}
            if returning
            else {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_" + uuid4().hex,
                        "type": "function",
                        "function": {
                            "name": "session_ops",
                            "arguments": json.dumps(server.arguments),
                        },
                    }
                ],
            }
        )
        chunks = [
            {
                "id": "chatcmpl-session-ops",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "session-ops-model",
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            },
            {
                "id": "chatcmpl-session-ops",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "session-ops-model",
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
def session_ops_provider():
    server = SessionOpsProvider()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def configure_provider(app: Application, server: SessionOpsProvider) -> None:
    app.evidence.register_secret(PROVIDER_KEY, PRIVATE_QUERY)
    url = f"http://127.0.0.1:{server.server_port}/v1"
    app.evidence.register_secret(url)
    for name, value in (
        ("SESSION_OPS_PROVIDER_URL", url),
        ("SESSION_OPS_PROVIDER_KEY", PROVIDER_KEY),
    ):
        response = app.request(
            "/api/system/secrets", method="PUT", data={"name": name, "value": value}
        )
        assert response.status_code == 200, response.text
    response = app.request(
        "/api/system/providers/session-ops-provider",
        method="PUT",
        data={
            "base_url": "SESSION_OPS_PROVIDER_URL",
            "api_key": "SESSION_OPS_PROVIDER_KEY",
        },
    )
    assert response.status_code == 200, response.text
    response = app.request(
        "/api/system/models/session-ops-model",
        method="PUT",
        data={
            "provider": "session-ops-provider",
            "model_string": "session-ops-model",
            "capabilities": ["text"],
        },
    )
    assert response.status_code == 200, response.text


def seed_session_ops_inputs(store, vault_name: str) -> dict[str, Any]:
    """Seed search sources and a map as retrieval inputs, not generated output."""
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from core.memory.session_map.checkpoints import build_session_map_context_message
    from core.memory.session_map.models import (
        SessionMapDraft,
        SessionMapTrajectory,
        SourceRange,
    )

    for session_id, workspace in (
        ("source", "Projects/WorkspaceA"),
        ("sibling", "Projects/WorkspaceB"),
    ):
        store.ensure_session(session_id, vault_name, owner_principal_id="local-user")
        store.set_session_workspace(
            session_id=session_id, vault_name=vault_name, workspace_path=workspace
        )
        store.add_messages(
            session_id,
            vault_name,
            [
                ModelRequest(
                    parts=[UserPromptPart(content="Wetland grants are exploratory.")]
                )
            ],
        )
    store.set_session_title("source", vault_name, "Albatross grant research")
    draft = SessionMapDraft(
        trajectory=SessionMapTrajectory(
            text="Marsh habitat grant exploration, with no commitment yet.",
            sources=(SourceRange(start=0, end=0),),
        )
    )
    message = build_session_map_context_message(draft)
    store.add_context_checkpoint(
        session_id="source",
        vault_name=vault_name,
        checkpoint_id="source-map",
        checkpoint_kind="session_map",
        source="validation",
        message_count_before=1,
        last_message_sequence_index=0,
        summary_message=message,
        replacement_history=[message],
        metadata={
            "map": draft.model_dump(mode="json"),
            "map_observed_through_sequence_index": 0,
        },
    )
    return draft.model_dump(mode="json")


async def call_session_ops(
    app: Application,
    server: SessionOpsProvider,
    vault_name: str,
    arguments: dict[str, Any],
):
    from core.runtime.state import get_runtime_context

    store = get_runtime_context().chat_store
    before = len(store.get_tool_events("controller", vault_name))
    server.arguments = arguments
    response = await chat_turn(
        app,
        {
            "vault_name": vault_name,
            "session_id": "controller",
            "workspace_path": "Projects/WorkspaceA",
            "prompt": "Use the requested session operation.",
            "model": "session-ops-model",
        },
    )
    assert response["terminal_event"]["event"] == "done"
    assert response["task"]["status"] == "completed"
    events = [
        event
        for event in store.get_tool_events("controller", vault_name)[before:]
        if event.event_type == "result" and event.tool_name == "session_ops"
    ]
    assert len(events) == 1
    app.evidence.append(
        "session-ops-results.jsonl",
        {
            "arguments": arguments,
            "result_text": events[0].result_text,
            "metadata": events[0].result_metadata_json,
        },
    )
    return events[0]


def break_discovery_index(app: Application) -> None:
    """Seed a readable schema whose derived FTS content lookup fails on a hit.

    SQLite reports a generic SQL logic error for missing external FTS content.
    This protects real failure handling and private-query absence; it does not
    prove sanitization of arbitrary private exception-message payloads.
    """
    with sqlite3.connect(app.root / "system/chat_sessions.db") as conn:
        source_row = conn.execute(
            "SELECT id FROM chat_compaction_checkpoints WHERE checkpoint_id = ?",
            ("source-map",),
        ).fetchone()
        assert source_row is not None
        conn.execute("DROP TABLE chat_session_maps_fts")
        conn.execute(
            "CREATE VIRTUAL TABLE chat_session_maps_fts "
            "USING fts5(text, content='private_database_payload')"
        )
        conn.execute(
            "INSERT INTO chat_session_maps_fts(rowid, text) VALUES (?, ?)",
            (source_row[0], PRIVATE_QUERY),
        )
        try:
            conn.execute(
                "SELECT snippet(chat_session_maps_fts, 0, '[', ']', '...', 32) "
                "FROM chat_session_maps_fts WHERE chat_session_maps_fts MATCH ?",
                (PRIVATE_QUERY,),
            ).fetchall()
        except sqlite3.OperationalError as error:
            app.evidence.save_json(
                "discovery-fault-input.json",
                {
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                    "scope": "Real external-content FTS read failure before tool invocation",
                    "parity_gap": "Generic SQLite error does not exercise private exception-payload sanitization",
                },
            )
        else:
            raise AssertionError(
                "Expected broken external FTS content to fail on retrieval"
            )
