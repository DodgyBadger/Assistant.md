"""Configuration and seeded conversation for real post-turn map reduction."""

import json

from validation.pytest_scenarios.support.session_map_author_http import configure_author


def configure_post_turn(app, server):
    configure_author(app, server)
    for key, value in (
        ("compaction_strategy", "session_map"),
        ("compaction_author_thinking", "low"),
        ("compaction_low_watermark_tokens", "1"),
        ("compaction_retained_turns", "1"),
        ("compaction_high_watermark_tokens", "2"),
        ("compaction_type", "auto"),
    ):
        response = app.request(
            f"/api/system/settings/general/{key}", method="PUT", data={"value": value}
        )
        assert response.status_code == 200, response.text
    metadata = app.request("/api/metadata")
    assert metadata.status_code == 200
    disabled = app.request(
        "/api/system/settings/general/disabled_tools",
        method="PUT",
        data={"value": json.dumps([row["name"] for row in metadata.json()["tools"]])},
    )
    assert disabled.status_code == 200, disabled.text


def seed_post_turn(store, vault, session):
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        UserPromptPart,
    )

    store.ensure_session(session, vault, owner_principal_id="local-user")
    store.add_messages(
        session,
        vault,
        [
            ModelRequest(
                parts=[
                    UserPromptPart(
                        content="Earlier question about the redevelopment proposal."
                    )
                ]
            ),
            ModelResponse(
                parts=[TextPart(content="Earlier analysis of the proposal.")]
            ),
            ModelRequest(
                parts=[UserPromptPart(content="The City owns the underlying land.")]
            ),
            ModelResponse(
                parts=[TextPart(content="That fact should be checked with counsel.")]
            ),
        ],
    )
