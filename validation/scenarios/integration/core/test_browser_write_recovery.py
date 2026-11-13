"""Real browser writes are not replayed through task/model failure recovery."""

from __future__ import annotations

import json

import pytest
from pydantic_ai.messages import ToolReturn, ToolReturnPart

from validation.support.application import Application
from validation.support.browser.retained_site import browser_turn, retained_browser_site
from validation.support.browser.write_provider import browser_write_provider
from validation.support.chat.chat_failure_recovery_contracts import set_setting
from validation.support.chat.chat_persistence import PERSISTENCE_CONTEXT
from validation.support.chat.recovery_activity import require_recovery_activity
from validation.support.chat.rollback_restart_workload import chat_with_redirects

pytestmark = pytest.mark.scenario


@pytest.mark.parametrize("uncertain", [False, True])
async def test_browser_write_is_not_replayed_after_provider_or_action_failure(
    app: Application, monkeypatch: pytest.MonkeyPatch, uncertain: bool
) -> None:
    vault = app.vault("BrowserWriteRecoveryVault")
    app.write(vault, "AssistantMD/Authoring/full_history.md", PERSISTENCE_CONTEXT)
    await app.start()
    for key, value in (
        ("default_model", "test"),
        ("compaction_type", "none"),
        ("model_stream_retries", "2"),
        ("model_stream_retry_base_delay_seconds", "0"),
        ("model_stream_retry_max_delay_seconds", "0"),
        ("task_rollback_enabled", "true"),
        ("browser_min_memory_headroom_mb", "0"),
        ("browser_navigation_timeout_seconds", "1"),
    ):
        set_setting(app, key, value)
    with retained_browser_site(app, monkeypatch) as site:
        site.pages["/write-form"] = (
            b'<form method="post" action="/submit"><button>Commit</button></form>'
        )
        # A label truncated through a non-BMP character must survive the real
        # provider UTF-8 request path, not just local JSON construction.
        site.pages["/write-form"] += (
            '<button type="button" aria-label="a' + "🚀" * 60 + '">Unicode</button>'
        ).encode()
        site.hold_submission = uncertain
        with browser_write_provider(app, site) as provider:
            try:
                result = await chat_with_redirects(
                    app,
                    {
                        "vault_name": vault.name,
                        "session_id": "browser-write-recovery",
                        "prompt": "Commit the controlled browser form once, then report the result.",
                        "model": "code-tool-model",
                        "context_template": "full_history.md",
                    },
                )
                assert site.submissions == [{}]
                assert len(result["task_ids"]) == 1
                assert not [
                    event
                    for event in result["events"]
                    if event["event"] == "chat_retry_redirect"
                ]
                assert provider.disconnected
                assert result["terminal"]["event"] == "done"
                require_recovery_activity(
                    app, "chat_retry_scheduled", strategy="resume_snapshot"
                )
                assert len(app.events.select("browser_read_started")) == 1
                assert len(app.events.select("browser_action_started")) == 1
                returned = []
                clicked_reference = None
                for request in provider.calls:
                    if request["messages"][-1]["role"] != "tool":
                        continue
                    message = request["messages"][-1]
                    call = next(
                        call
                        for row in reversed(request["messages"])
                        for call in row.get("tool_calls", [])
                        if call["id"] == message["tool_call_id"]
                    )
                    arguments = json.loads(call["function"]["arguments"])
                    if arguments.get("operation") == "click":
                        returned.append(message)
                        clicked_reference = arguments["reference"]
                assert len(returned) >= 2 and all(
                    message == returned[0] for message in returned
                )
                if uncertain:
                    assert "uncertain" in returned[0]["content"]
                    from core.runtime.state import get_runtime_context

                    history = (
                        get_runtime_context().chat_store.get_history(
                            "browser-write-recovery", vault.name, mode="raw"
                        )
                        or []
                    )
                    uncertain_parts = [
                        part
                        for message in history
                        for part in message.parts
                        if isinstance(part, ToolReturnPart)
                        and part.tool_name == "browser"
                        and isinstance(part.metadata, dict)
                        and part.metadata.get("completion_uncertain") is True
                    ]
                    assert len(uncertain_parts) == 1
                    assert uncertain_parts[0].metadata["requires_inspection"] is True
                    rejected = await browser_turn(
                        app,
                        vault.name,
                        "browser-write-recovery",
                        operation="click",
                        reference=clicked_reference,
                    )
                    assert (
                        isinstance(rejected, ToolReturn)
                        and rejected.metadata["reason"] == "stale_reference"
                    )
                    assert site.submissions == [{}]
            finally:
                site.release_submission.set()
                await browser_turn(
                    app, vault.name, "browser-write-recovery", operation="close"
                )
