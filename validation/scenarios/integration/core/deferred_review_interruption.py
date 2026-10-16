"""A stopped or failed approval resume must leave the next chat turn usable."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai import DeferredToolRequests, DeferredToolResults, Tool
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel

from core.chat import executor as chat_executor
from core.chat.chat_store import ChatStore
from core.chat.deferred_reviews import (
    DeferredReviewError,
    create_deferred_review,
    get_deferred_review,
    has_pending_deferred_review,
    mark_deferred_review_submitted,
    mark_deferred_review_terminal,
)
from core.chat.tool_history import analyze_tool_history
from core.runtime.state import get_runtime_context
from core.vault_state.file_mutations import write_vault_file
from validation.core.base_scenario import BaseScenario


def _reviewed_tool(vault, session_id, executions, started, release):
    async def reviewed_tool() -> str:
        executions.append(session_id)
        write_vault_file(
            vault_path=vault,
            path=f"{session_id}.md",
            content="uncommitted reviewed write\n",
        )
        started.set()
        await release.wait()
        raise RuntimeError("Approval resume failed deliberately")

    return reviewed_tool


def _assert_atomic_settlement(vault_name):
    store = ChatStore()
    for case in ("partial", "portable", "append_failure", "unrelated"):
        session_id = f"review-settlement-{case}"
        store.ensure_session(session_id, vault_name, owner_principal_id="local-user")
        calls = [
            ToolCallPart("reviewed_tool", {}, tool_call_id="settled"),
            ToolCallPart("reviewed_tool", {}, tool_call_id="pending"),
        ]
        history = [
            ModelRequest(parts=[UserPromptPart("review these")]),
            ModelResponse(parts=calls),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        "reviewed_tool", "original result", tool_call_id="settled"
                    )
                ]
            ),
        ]
        store.add_messages(session_id, vault_name, history)
        review = create_deferred_review(
            vault_name=vault_name,
            session_id=session_id,
            originating_task_id="fixture-origin",
            requests=DeferredToolRequests(
                approvals=(
                    calls[:1]
                    if case == "unrelated"
                    else (
                        [
                            calls[0],
                            replace(calls[1], tool_call_id="pending|provider-item"),
                        ]
                        if case == "portable"
                        else calls
                    )
                )
            ),
            resume_messages=history,
            resume_config={},
        )
        mark_deferred_review_submitted(
            vault_name=vault_name,
            session_id=session_id,
            artifact_ref=review.artifact_ref,
            results=DeferredToolResults(
                approvals={call.tool_call_id: True for call in calls}
            ),
            resumed_task_id="fixture-resume",
        )
        assert has_pending_deferred_review(vault_name=vault_name, session_id=session_id)

        def settle(session_id=session_id, review=review):
            return mark_deferred_review_terminal(
                vault_name=vault_name,
                session_id=session_id,
                artifact_ref=review.artifact_ref,
                status="cancelled",
            )

        if case == "unrelated":
            try:
                settle()
            except DeferredReviewError as exc:
                assert exc.code == "DeferredReviewHistoryConflict"
            else:
                raise AssertionError("Unrelated pending calls must not be synthesized")
        elif case == "append_failure":
            original_add = ChatStore.add_messages

            def fail_after_append(self, *args, _original_add=original_add, **kwargs):
                _original_add(self, *args, **kwargs)
                raise RuntimeError("deliberate failure after appending closure")

            with patch.object(ChatStore, "add_messages", fail_after_append):
                try:
                    settle()
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("Injected append failure should propagate")
        else:
            settle()
            repaired = store.get_history(session_id, vault_name) or []
            assert analyze_tool_history(repaired).ok
            assert (
                repaired[:-1] == history
            ), "Existing calls and results must be preserved"
            assert repaired[-1].parts[0].tool_call_id == "pending"
            assert repaired[-1].parts[0].outcome == "interrupted"
            try:
                settle()
            except DeferredReviewError as exc:
                assert exc.code == "DeferredReviewStateConflict"
            else:
                raise AssertionError(
                    "A repeated terminal hook must not append duplicate replies"
                )
            assert store.get_history(session_id, vault_name) == repaired
            continue
        after = get_deferred_review(
            vault_name=vault_name,
            session_id=session_id,
            artifact_ref=review.artifact_ref,
        )
        assert after.status == "resuming", "State and closure must roll back together"
        assert store.get_history(session_id, vault_name) == history
        if case == "append_failure":
            settle()
            assert analyze_tool_history(
                store.get_history(session_id, vault_name) or []
            ).ok


class DeferredReviewInterruptionScenario(BaseScenario):
    async def test_scenario(self) -> None:
        vault = self.create_vault("DeferredReviewInterruptionVault")
        await self.start_system()
        original_config = chat_executor._prepare_agent_config
        try:
            _assert_atomic_settlement(vault.name)
            for terminal_status in ("cancelled", "failed"):
                session_id = f"review-interruption-{terminal_status}"
                started = asyncio.Event()
                release = asyncio.Event()
                executions = []

                reviewed_tool = _reviewed_tool(
                    vault, session_id, executions, started, release
                )

                def configure(*args, _tool=reviewed_tool, **kwargs):
                    return (
                        "",
                        "",
                        TestModel(call_tools=["reviewed_tool"]),
                        [Tool(_tool, requires_approval=True)],
                    )

                chat_executor._prepare_agent_config = configure
                initial = await self.run_chat_task(
                    {
                        "vault_name": vault.name,
                        "session_id": session_id,
                        "prompt": "Run the reviewed operation.",
                        "model": "test",
                        "tools": [],
                        "chat_mode": "inline_edit",
                    }
                )
                review_event = next(
                    event
                    for event in initial["events"]
                    if event.get("event") == "review_required"
                )
                artifact_ref = review_event["artifact_ref"]
                call_ids = [call["tool_call_id"] for call in review_event["approvals"]]
                assert not executions, "Approval must precede tool execution"
                checkpoint = self.event_checkpoint()
                runtime = get_runtime_context()
                original_start = runtime.task_runner.start_background
                resume_hooks = {}

                async def capture_resume_hooks(
                    *args,
                    _original_start=original_start,
                    _resume_hooks=resume_hooks,
                    **kwargs,
                ):
                    task = await _original_start(*args, **kwargs)
                    _resume_hooks[task.task_id] = kwargs["hooks"]
                    return task

                with patch.object(
                    runtime.task_runner, "start_background", capture_resume_hooks
                ):
                    submitted = self.call_api(
                        f"/api/vaults/{vault.name}/chat/{session_id}/deferred-reviews/{artifact_ref}/submit",
                        method="POST",
                        data={
                            "decisions": [
                                {"tool_call_id": call_id, "decision": "approve"}
                                for call_id in call_ids
                            ]
                        },
                    )
                assert submitted.status_code == 200
                task_id = submitted.json()["task"]["task_id"]
                await asyncio.wait_for(started.wait(), timeout=10)
                if terminal_status == "cancelled":
                    cancelled = self.call_api(
                        f"/api/chat/sessions/{session_id}/cancel", method="POST"
                    )
                    assert cancelled.status_code == 200
                else:
                    release.set()
                for _ in range(100):
                    review = get_deferred_review(
                        vault_name=vault.name,
                        session_id=session_id,
                        artifact_ref=artifact_ref,
                    )
                    if review is not None and review.status == terminal_status:
                        break
                    await asyncio.sleep(0.05)
                assert review is not None and review.status == terminal_status
                task = await get_runtime_context().task_coordinator.get_task(task_id)
                assert task is not None and task.status == terminal_status
                assert not (
                    vault / f"{session_id}.md"
                ).exists(), (
                    "Interrupted reviewed writes must retain normal task rollback"
                )
                history = ChatStore().get_history(session_id, vault.name) or []
                assert analyze_tool_history(
                    history
                ).ok, "Terminal approval resumes must close their saved pending tool calls"
                replies = [
                    part
                    for message in history
                    for part in message.parts
                    if isinstance(part, ToolReturnPart)
                    and part.tool_call_id in call_ids
                ]
                assert len(replies) == len(call_ids)
                assert all(
                    part.outcome == "interrupted" for part in replies
                ), "A missing durable result must never be represented as successful execution"
                self.assert_event_contains(
                    self.events_since(checkpoint),
                    name="chat_deferred_review_history_closed",
                    expected={
                        "session_id": session_id,
                        "status": terminal_status,
                        "closed_call_count": len(call_ids),
                    },
                )
                activity = self.call_api("/api/system/activity-log?limit=200").json()
                review_rows = {
                    entry["data"]["event"]: entry["data"]
                    for entry in activity["entries"]
                    if entry.get("data", {}).get("artifact_ref") == artifact_ref
                    and entry["data"]
                    .get("event", "")
                    .startswith("chat_deferred_review_")
                }
                assert (
                    review_rows["chat_deferred_review_created"]["status"] == "pending"
                )
                assert (
                    review_rows["chat_deferred_review_claimed"]["status"] == "resuming"
                )
                for event in (
                    "chat_deferred_review_history_closed",
                    "chat_deferred_review_terminal",
                ):
                    row = review_rows[event]
                    assert row["task_id"] == task_id
                    assert row["resumed_task_id"] == task_id
                    assert row["originating_task_id"] == review.originating_task_id
                    assert row["status"] == terminal_status
                if terminal_status == "failed":
                    assert review_rows["chat_deferred_review_terminal"]["error"]
                    assert (
                        review_rows["chat_deferred_review_terminal"]["reason"]
                        == "review_resume_failed"
                    )
                    # Invoke the real captured failure hook after task context has
                    # unwound. Force persistence failure without changing history.
                    private_marker = "PRIVATE_REVIEW_SETTLEMENT_FAILURE"
                    with patch(
                        "core.chat.task_execution.mark_deferred_review_terminal",
                        side_effect=DeferredReviewError(
                            "FixtureSettlementFailure", private_marker
                        ),
                    ):
                        await resume_hooks[task_id].on_failed(
                            task_id, RuntimeError(private_marker)
                        )
                    activity = self.call_api(
                        "/api/system/activity-log?limit=200"
                    ).json()
                    warnings = [
                        entry["data"]
                        for entry in activity["entries"]
                        if entry.get("data", {}).get("event")
                        == "deferred_review_terminal_record_failed"
                        and entry["data"].get("artifact_ref") == artifact_ref
                    ]
                    assert len(warnings) == 1
                    assert warnings[0]["task_id"] == task_id
                    assert warnings[0]["session_id"] == session_id
                    assert warnings[0]["vault_name"] == vault.name
                    assert warnings[0]["reason"] == "FixtureSettlementFailure"
                    assert private_marker not in str(activity)

                def followup_config(*args, **kwargs):
                    return ("", "", TestModel(call_tools=[]), [])

                chat_executor._prepare_agent_config = followup_config
                followup = await self.run_chat_task(
                    {
                        "vault_name": vault.name,
                        "session_id": session_id,
                        "prompt": "Continue without retrying that operation.",
                        "model": "test",
                        "tools": [],
                    }
                )
                assert followup["terminal_event"]["event"] == "done"
                assert executions == [
                    session_id
                ], "Recovery must not replay approved tools"
                assert analyze_tool_history(
                    ChatStore().get_history(session_id, vault.name) or []
                ).ok
        finally:
            chat_executor._prepare_agent_config = original_config
            await self.stop_system()
            self.teardown_scenario()
