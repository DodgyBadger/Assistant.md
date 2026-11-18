"""Real chat tasks publish bounded handoff state without private input projection."""

from __future__ import annotations

import asyncio
import json

import pytest

from validation.support.application import Application
from validation.support.browser.handoff import handoff_path
from validation.support.browser.handoff_provider import browser_handoff_provider
from validation.support.browser.human_transport import fresh_frame, human_transport
from validation.support.browser.retained_site import retained_browser_site
from validation.support.chat.chat_failure_recovery_contracts import set_setting
from validation.support.chat.chat_persistence import PERSISTENCE_CONTEXT

pytestmark = pytest.mark.scenario


async def test_real_chat_handoff_survives_replay_and_excludes_human_input_from_provider(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = app.vault("BrowserChatHandoffVault")
    app.write(vault, "AssistantMD/Authoring/full_history.md", PERSISTENCE_CONTEXT)
    await app.start()
    set_setting(app, "browser_min_memory_headroom_mb", "0")
    set_setting(app, "compaction_type", "none")
    sentinel = "synthetic-private-chat-handoff-password-90421"
    app.evidence.register_secret(sentinel)
    with retained_browser_site(app, monkeypatch) as site:
        site.sign_in_on_submit = True
        site.pages["/login"] = (
            b'<style>body{margin:0}input{position:absolute;left:20px;top:20px;width:200px;height:40px}</style><form method="post" action="/submit"><input type="password" name="password"></form>'
        )
        with browser_handoff_provider(app, site) as calls:
            response = await app.request_async(
                "/api/chat/tasks",
                "POST",
                {
                    "vault_name": vault.name,
                    "session_id": "handoff-chat",
                    "prompt": "Open the controlled website, request human sign-in, then report completion.",
                    "model": "code-tool-model",
                    "context_template": "full_history.md",
                },
            )
            assert response.status_code == 200
            task_id = response.json()["task"]["task_id"]
            from core.chat.task_execution import stream_chat_task_sse
            from core.runtime.state import get_runtime_context

            try:
                async with asyncio.timeout(15):
                    while True:
                        state = await app.request_async(handoff_path("handoff-chat"))
                        if state.status_code == 200:
                            break
                        await asyncio.sleep(0.03)
                state = state.json()
                assert state["task_id"] == task_id and state["status"] == "waiting"
                replay_url = f"/api/chat/tasks/{task_id}/replay-snapshot"
                snapshot = (await app.request_async(replay_url)).json()
                handoffs = [
                    e for e in snapshot["events"] if e["event"] == "browser_handoff"
                ]
                assert (
                    len(handoffs) == 1
                ), "A real waiting handoff must reach reconnecting chat"
                assert handoffs[0]["browser_handoff"] == state
                assert snapshot["available"] and (not snapshot["terminal"])
                stream = stream_chat_task_sse(task_id=task_id)
                try:
                    async with asyncio.timeout(2):
                        async for chunk in stream:
                            if chunk.startswith("data: "):
                                payload = json.loads(chunk[6:])
                                if payload["event"] == "browser_handoff":
                                    assert payload["browser_handoff"] == state
                                    break
                finally:
                    await stream.aclose()
                path = handoff_path("handoff-chat") + "/" + state["handoff_id"]
                async with human_transport(app) as client:
                    assert (await client.post(path + "/claim")).status_code == 200
                    snapshot = (await app.request_async(replay_url)).json()
                    handoffs = [
                        e for e in snapshot["events"] if e["event"] == "browser_handoff"
                    ]
                    assert (
                        len(handoffs) == 1
                        and handoffs[0]["browser_handoff"]["status"] == "human"
                    )
                    metadata = await fresh_frame(client, path)
                    assert (
                        await client.post(
                            path + "/input",
                            json={**metadata, "kind": "click", "x": 40, "y": 40},
                        )
                    ).status_code == 200
                    metadata = await fresh_frame(client, path)
                    assert (
                        await client.post(
                            path + "/input",
                            json={**metadata, "kind": "text", "text": sentinel},
                        )
                    ).status_code == 200
                    metadata = await fresh_frame(client, path)
                    assert (
                        await client.post(
                            path + "/input",
                            json={**metadata, "kind": "key", "key": "Enter"},
                        )
                    ).status_code == 200
                    async with asyncio.timeout(5):
                        while not site.submissions:
                            await asyncio.sleep(0.03)
                    await fresh_frame(client, path)
                    assert (await client.post(path + "/return")).status_code == 200
                task = await app.wait_task(task_id)
                assert task["status"] == "completed"
                snapshot = (await app.request_async(replay_url)).json()
                assert snapshot["terminal"] and snapshot["available"]
                handoffs = [
                    e for e in snapshot["events"] if e["event"] == "browser_handoff"
                ]
                assert (
                    len(handoffs) == 1
                    and handoffs[0]["browser_handoff"]["status"] == "returned"
                )
                assert handoffs[0]["browser_handoff"]["task_id"] == task_id
                assert len(calls) == 3
                store = get_runtime_context().chat_store
                history = repr(store.get_stored_messages("handoff-chat", vault.name))
                tool_events = repr(store.get_tool_events("handoff-chat", vault.name))
                leaked = any(
                    sentinel in value
                    for value in (
                        json.dumps(calls),
                        json.dumps(snapshot),
                        history,
                        tool_events,
                    )
                )
                assert (
                    not leaked
                ), "Human credential reached provider requests or chat replay"
                assert site.submissions == [{"password": sentinel}]
            finally:
                await get_runtime_context().task_coordinator.cancel_task(task_id)
