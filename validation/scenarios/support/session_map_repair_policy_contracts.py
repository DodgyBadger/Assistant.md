"""Supported v1 map checkpoints, verified retrieval inputs, and real author HTTP."""

from __future__ import annotations

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from validation.scenarios.support.compaction_journeys import add_turn
from validation.scenarios.support.session_map_retrieval_inputs import seed_retrieval


def settings(app, **values):
    for key, value in values.items():
        response = app.request(
            f"/api/system/settings/general/{key}", "PUT", {"value": str(value)}
        )
        assert response.status_code == 200, response.text


def configure_map_policy(app, *, low=1, high=2, mode="auto"):
    settings(
        app,
        compaction_strategy="session_map",
        compaction_low_watermark_tokens=low,
        compaction_high_watermark_tokens=high,
        compaction_retained_turns=1,
        compaction_type=mode,
        compaction_author_thinking="low",
    )


def disable_chat_tools(app):
    metadata = app.request("/api/metadata")
    assert metadata.status_code == 200
    settings(
        app,
        disabled_tools=json.dumps([row["name"] for row in metadata.json()["tools"]]),
    )


def seed_legacy_pending_map(store, vault, session):
    from core.chat.compaction import build_canonical_evidence_range
    from core.memory.session_map.checkpoints import build_session_map_context_message
    from core.memory.session_map.models import SessionMapDraft

    # v1 entries without a trajectory are the oldest format explicitly accepted
    # by SessionMapDraft. Native retrieval messages are controlled inputs, not a
    # claim that this fixture generated a retrieval through the model.
    seed_retrieval(store, vault, session)
    add_turn(
        store,
        vault,
        session,
        "Preserve the pending ceiling discussion.",
        "It remains relevant.",
    )
    add_turn(
        store, vault, session, "Develop the next phase.", "The next phase is open."
    )
    revision = store.get_session_history_revision(session, vault)
    evidence = build_canonical_evidence_range(
        store=store,
        session_id=session,
        vault_name=vault,
        source_start_sequence_index=2,
        source_end_sequence_index=5,
        history_revision=revision,
    )
    assert evidence.status == "resolved" and len(evidence.envelopes) == 1
    envelope = evidence.envelopes[0]
    draft = SessionMapDraft.model_validate(
        {
            "schema_version": 1,
            "entries": [
                {
                    "id": "legacy_ceiling",
                    "kind": "orientation",
                    "state": "active",
                    "basis": "mixed",
                    "text": "The original ceiling was recorded.",
                    "sources": [{"start": 1, "end": 1}],
                }
            ],
        }
    )
    context = build_session_map_context_message(draft)
    store.add_context_checkpoint(
        session_id=session,
        vault_name=vault,
        checkpoint_id="oldest-supported-map",
        checkpoint_kind="session_map",
        source="validation",
        message_count_before=10,
        last_message_sequence_index=5,
        summary_message=context,
        replacement_history=[context],
        replacement_source_sequence_indexes=[None],
        expected_history_revision=revision,
        metadata={
            "map": draft.model_dump(mode="json"),
            "map_observed_through_sequence_index": 1,
            "pending_evidence": {
                "start_sequence_index": 2,
                "end_sequence_index": 5,
                "source_digest": envelope.source_digest,
                "estimated_tokens": envelope.estimated_tokens,
            },
        },
    )


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(request)
        payload = next(
            json.loads(item["content"])
            for item in request["messages"]
            if item.get("role") == "user" and isinstance(item.get("content"), str)
        )
        self.server.payloads.append(payload)
        source = (
            {"start": 99999, "end": 99999}
            if self.server.invalid
            else {"start": 0, "end": 0}
        )
        draft = {
            "schema_version": 3,
            "trajectory": {
                "text": "The historical ceiling remains authoritative.",
                "sources": [source],
            },
            "entries": [
                {
                    "id": "historical_ceiling",
                    "kind": "orientation",
                    "state": "active",
                    "basis": "mixed",
                    "text": "The verified original ceiling informs the next phase.",
                    "sources": [source],
                }
            ],
        }
        name = request["tools"][0]["function"]["name"]
        delta = {
            "role": "assistant",
            "tool_calls": [
                {
                    "index": 0,
                    "id": "repair-map",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(draft)},
                }
            ],
        }
        chunks = [
            {
                "id": "map-repair",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "session-map-author",
                "choices": [{"index": 0, "delta": item, "finish_reason": finish}],
            }
            for item, finish in ((delta, None), ({}, "tool_calls"))
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
def repair_author():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.requests, server.payloads, server.invalid = [], [], False
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()
