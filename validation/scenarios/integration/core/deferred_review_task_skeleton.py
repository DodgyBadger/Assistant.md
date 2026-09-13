"""Validate chat task skeleton support for deferred inline reviews."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai import (
    AgentRunResultEvent,
    DeferredToolRequests,
    DeferredToolResults,
    PartStartEvent,
    ToolDenied,
)
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from core.chat import executor as chat_executor
from core.chat.chat_store import ChatStore
from core.chat.deferred_reviews import create_deferred_review, get_deferred_review
from core.chat.executor import PreparedChatExecution
from core.chat.task_execution import (
    CHAT_TASK_EVENT_BUFFER,
    start_queued_chat_stream_task,
)
from core.constants import INLINE_EDIT_DENIAL_MESSAGE
from core.runtime.state import get_runtime_context
from validation.core.base_scenario import BaseScenario
from validation.core.streaming import stream_events_context


class _DeferredReviewResult:
    def __init__(self, prompt: str) -> None:
        self._tool_calls = [
            ToolCallPart(
                tool_name="file_write",
                args={
                    "operation": "write",
                    "path": "Draft.md",
                    "content": "Draft content",
                    "overwrite": True,
                },
                tool_call_id="review-call-1",
            ),
            ToolCallPart(
                tool_name="file_write",
                args={
                    "operation": "write",
                    "path": "Notes.md",
                    "content": "Notes content",
                },
                tool_call_id="review-call-2",
            ),
        ]
        self.output = DeferredToolRequests(approvals=self._tool_calls)
        self._messages = [
            ModelRequest(parts=[UserPromptPart(content=prompt)]),
            ModelResponse(parts=self._tool_calls),
        ]

    def new_messages(self):
        return list(self._messages)

    def all_messages(self):
        return list(self._messages)


class _DeferredReviewAgent:
    @stream_events_context
    async def run_stream_events(self, prompt, **kwargs):
        del kwargs
        yield AgentRunResultEvent(result=_DeferredReviewResult(str(prompt)))


class _ResumeResult:
    output = "resumed"

    def __init__(self, deferred_tool_results: DeferredToolResults) -> None:
        tool_parts = []
        for tool_call_id, decision in deferred_tool_results.approvals.items():
            if isinstance(decision, ToolDenied):
                tool_parts.append(
                    ToolReturnPart(
                        tool_name="file_write",
                        content=decision.message,
                        tool_call_id=tool_call_id,
                        outcome="denied",
                    )
                )
            else:
                tool_parts.append(
                    ToolReturnPart(
                        tool_name="file_write",
                        content=f"executed {tool_call_id}",
                        tool_call_id=tool_call_id,
                    )
                )
        self._messages = [
            ModelRequest(parts=tool_parts),
            ModelResponse(parts=[TextPart("resumed")]),
        ]

    def new_messages(self):
        return list(self._messages)

    def all_messages(self):
        return self.new_messages()


class _ResumeAgent:
    def __init__(self, capture: dict) -> None:
        self.capture = capture

    @stream_events_context
    async def run_stream_events(self, prompt, **kwargs):
        self.capture["prompt"] = prompt
        deferred_tool_results = kwargs.get("deferred_tool_results")
        self.capture["deferred_tool_results"] = deferred_tool_results
        self.capture["message_history"] = kwargs.get("message_history")
        yield PartStartEvent(index=0, part=TextPart("resumed"))
        yield AgentRunResultEvent(result=_ResumeResult(deferred_tool_results))


class DeferredReviewTaskSkeletonScenario(BaseScenario):
    """Validate pending review persistence from the production chat task path."""

    async def test_scenario(self) -> None:
        vault = self.create_vault("DeferredReviewTaskVault")
        self.create_file(vault, "Draft.md", "Original draft\n")
        await self.start_system()
        ChatStore().ensure_session(
            "deferred-review-session",
            vault.name,
            owner_principal_id="local-user",
        )
        ChatStore().ensure_session(
            "deferred-denial-session",
            vault.name,
            owner_principal_id="local-user",
        )
        await _assert_deferred_review_commit_is_atomic(vault.name)

        original_prepare = chat_executor._prepare_chat_execution
        original_prepare_resume = (
            chat_executor._prepare_deferred_review_resume_execution
        )
        resume_capture: dict = {}

        async def fake_prepare_chat_execution(
            *,
            vault_name,
            vault_path,
            prompt,
            image_paths,
            image_uploads,
            session_id,
            tools,
            model,
            thinking=None,
            context_template=None,
            chat_mode="normal",
            message_history_override=None,
            display_prompt=None,
        ):
            del (
                vault_path,
                image_paths,
                image_uploads,
                thinking,
                message_history_override,
            )
            del display_prompt
            history = (
                chat_executor._CHAT_STORE.get_history(session_id, vault_name) or []
            )
            return PreparedChatExecution(
                agent=_DeferredReviewAgent(),
                message_history=list(history),
                prompt_for_history=prompt,
                user_prompt=prompt,
                attached_image_count=0,
                model=model,
                tools=list(tools),
                thinking="low",
                context_template=context_template,
                chat_mode=chat_mode,
            )

        async def fake_prepare_deferred_review_resume_execution(
            *,
            vault_name,
            vault_path,
            session_id,
            tools,
            model,
            message_history,
            deferred_tool_results,
            thinking=None,
            context_template=None,
            chat_mode="normal",
        ):
            del vault_name, vault_path, session_id
            resume_capture["prepared_tools"] = list(tools)
            resume_capture["prepared_model"] = model
            resume_capture["prepared_thinking"] = thinking
            resume_capture["prepared_context_template"] = context_template
            resume_capture["prepared_chat_mode"] = chat_mode
            return PreparedChatExecution(
                agent=_ResumeAgent(resume_capture),
                message_history=list(message_history),
                prompt_for_history="",
                user_prompt=None,
                attached_image_count=0,
                model=model,
                tools=list(tools),
                context_template=context_template,
                chat_mode=chat_mode,
                deferred_tool_results=deferred_tool_results,
            )

        chat_executor._prepare_chat_execution = fake_prepare_chat_execution
        chat_executor._prepare_deferred_review_resume_execution = (
            fake_prepare_deferred_review_resume_execution
        )
        try:
            started = await start_queued_chat_stream_task(
                vault_name=vault.name,
                vault_path=str(vault),
                prompt="write draft",
                image_paths=[],
                image_uploads=[],
                session_id="deferred-review-session",
                tools=[],
                model="test",
                chat_mode="inline_edit",
            )
            task = await self._wait_for_task_terminal(started.task.task_id)
            assert (
                task is not None
            ), "Deferred review task should complete current stream"
            assert task.status == "completed", "Deferred review task should be terminal"

            events = await CHAT_TASK_EVENT_BUFFER.events_after(started.task.task_id)
            event_names = [event.event for event in events]
            assert event_names == [
                "review_required",
                "done",
            ], "Deferred review task should emit review_required before done"
            review_event = events[0].data
            assert (
                review_event["artifact_kind"] == "deferred_tool_review"
            ), "Review event should identify deferred review artifacts"
            assert (
                review_event["review_count"] == 2
            ), "Independent write calls should share one review artifact"
            assert review_event["approvals"][0]["args"]["path"] == (
                "Draft.md"
            ), "Review events should include proposed arguments for informed approval"
            assert (
                events[-1].data["choices"][0]["finish_reason"] == "tool_review_required"
            ), "Done event should identify review-required finish reason"

            review = get_deferred_review(
                vault_name=vault.name,
                session_id="deferred-review-session",
                artifact_ref=review_event["artifact_ref"],
            )
            assert review is not None, "Deferred review record should be persisted"
            assert review.status == "pending", "Deferred review should start pending"
            assert review.originating_task_id == started.task.task_id
            assert review.review_count == 2
            assert (
                len(review.resume_messages) == 2
            ), "Resume history should be persisted"
            assert review.resume_config.get("model") == "test"
            assert review.resume_config.get("tools") == []
            assert review.resume_config.get("thinking") == "low"
            assert review.resume_config.get("chat_mode") == "inline_edit"

            session_detail = self.call_api(
                "/api/chat/sessions/deferred-review-session" f"?vault_name={vault.name}"
            )
            assert session_detail.status_code == 200
            session_payload = session_detail.json()
            assert (
                session_payload.get("chat_mode") == "inline_edit"
            ), "Session detail should preserve the selected chat mode"
            assert session_payload.get("pending_review", {}).get("artifact_ref") == (
                review_event["artifact_ref"]
            ), "Session reload should expose the active pending review"
            assert (
                session_payload["pending_review"]["approvals"][0]["args"]["path"]
                == "Draft.md"
            ), "Reloaded review cards should retain proposed arguments"

            api_response = self.call_api(
                f"/api/vaults/{vault.name}/chat/deferred-review-session/"
                f"deferred-reviews/{review_event['artifact_ref']}"
            )
            assert (
                api_response.status_code == 200
            ), "Deferred review API should return artifact"
            api_payload = api_response.json()
            assert api_payload.get("artifact_kind") == "deferred_tool_review"
            assert api_payload.get("status") == "pending"
            assert api_payload.get("originating_task_id") == started.task.task_id
            assert [
                call.get("tool_call_id") for call in api_payload.get("approvals", [])
            ] == ["review-call-1", "review-call-2"]

            blocked_start = await start_queued_chat_stream_task(
                vault_name=vault.name,
                vault_path=str(vault),
                prompt="continue before review",
                image_paths=[],
                image_uploads=[],
                session_id="deferred-review-session",
                tools=[],
                model="test",
                chat_mode="inline_edit",
            )
            blocked_task = await self._wait_for_task_terminal(
                blocked_start.task.task_id
            )
            assert blocked_task is not None and blocked_task.status == "failed"
            blocked_events = await CHAT_TASK_EVENT_BUFFER.events_after(
                blocked_start.task.task_id
            )
            assert blocked_events[-1].event == "error"
            assert "Review pending" in str(
                blocked_events[-1]
                .data.get("choices", [{}])[0]
                .get("delta", {})
                .get("content", "")
            )

            duplicate_response = self.call_api(
                (
                    f"/api/vaults/{vault.name}/chat/deferred-review-session/"
                    f"deferred-reviews/{review_event['artifact_ref']}/submit"
                ),
                method="POST",
                data={
                    "decisions": [
                        {"tool_call_id": "review-call-1", "decision": "approve"},
                        {"tool_call_id": "review-call-1", "decision": "deny"},
                        {"tool_call_id": "review-call-2", "decision": "approve"},
                    ],
                },
            )
            assert (
                duplicate_response.status_code == 400
            ), "A tool call must receive exactly one unambiguous review result"
            assert duplicate_response.json().get("details", {}).get(
                "duplicate_tool_call_ids"
            ) == ["review-call-1"]

            (vault / "Draft.md").write_text(
                "Changed while review was pending\n", encoding="utf-8"
            )
            stale_target_response = self.call_api(
                (
                    f"/api/vaults/{vault.name}/chat/deferred-review-session/"
                    f"deferred-reviews/{review_event['artifact_ref']}/submit"
                ),
                method="POST",
                data={
                    "decisions": [
                        {"tool_call_id": "review-call-1", "decision": "approve"},
                        {"tool_call_id": "review-call-2", "decision": "deny"},
                    ],
                },
            )
            assert stale_target_response.status_code == 409
            assert (
                stale_target_response.json().get("error")
                == "DeferredReviewTargetConflict"
            )
            (vault / "Draft.md").write_text("Original draft\n", encoding="utf-8")

            submit_response = self.call_api(
                (
                    f"/api/vaults/{vault.name}/chat/deferred-review-session/"
                    f"deferred-reviews/{review_event['artifact_ref']}/submit"
                ),
                method="POST",
                data={
                    "decisions": [
                        {
                            "tool_call_id": "review-call-1",
                            "decision": "approve",
                            "override_args": {
                                "content": "Reviewed content",
                            },
                        },
                        {
                            "tool_call_id": "review-call-2",
                            "decision": "deny",
                            "message": "Keep the existing notes structure.",
                        },
                    ],
                },
            )
            assert (
                submit_response.status_code == 200
            ), "Submit should start a resume task"
            submit_payload = submit_response.json()
            assert submit_payload.get("status") in {"resuming", "completed"}
            resumed_task_id = submit_payload.get("task", {}).get("task_id")
            assert resumed_task_id, "Submit response should include resumed task id"
            resumed_task = await self._wait_for_task_terminal(resumed_task_id)
            assert resumed_task is not None, "Resumed task should complete"
            assert resumed_task.status == "completed"
            resumed_events = await CHAT_TASK_EVENT_BUFFER.events_after(resumed_task_id)
            assert [event.event for event in resumed_events] == [
                "delta",
                "done",
            ], "Resumed task should stream normal completion events"
            assert (
                resume_capture["prompt"] is None
            ), "Resume should not send a new prompt"
            result = resume_capture["deferred_tool_results"]
            assert result is not None, "Resume should receive DeferredToolResults"
            assert resume_capture["prepared_model"] == "test"
            assert resume_capture["prepared_tools"] == []
            assert resume_capture["prepared_thinking"] == "low"
            assert resume_capture["prepared_chat_mode"] == "inline_edit"
            approved = result.approvals["review-call-1"]
            assert approved.override_args == {
                "operation": "write",
                "path": "Draft.md",
                "content": "Reviewed content",
                "overwrite": True,
            }, "Submit should preserve edited override args"
            denied = result.approvals["review-call-2"]
            assert denied.message == (
                f"{INLINE_EDIT_DENIAL_MESSAGE} User reason: Keep the existing notes structure."
            )
            submitted_review = get_deferred_review(
                vault_name=vault.name,
                session_id="deferred-review-session",
                artifact_ref=review_event["artifact_ref"],
            )
            assert submitted_review is not None
            assert submitted_review.status == "completed"
            assert submitted_review.resumed_task_id == resumed_task_id
            completed_detail = self.call_api(
                "/api/chat/sessions/deferred-review-session" f"?vault_name={vault.name}"
            ).json()
            assert (
                completed_detail.get("pending_review") is None
            ), "Resolved reviews should not be reconstructed on session reload"
            mode_update = self.call_api(
                "/api/chat/sessions/deferred-review-session/mode",
                method="PATCH",
                data={"vault_name": vault.name, "chat_mode": "normal"},
            )
            assert mode_update.status_code == 200
            assert mode_update.json().get("chat_mode") == "normal"
            assert (
                self.call_api(
                    "/api/chat/sessions/deferred-review-session"
                    f"?vault_name={vault.name}"
                )
                .json()
                .get("chat_mode")
                == "normal"
            ), "Explicit session mode changes should persist without sending a prompt"

            stale_response = self.call_api(
                (
                    f"/api/vaults/{vault.name}/chat/deferred-review-session/"
                    f"deferred-reviews/{review_event['artifact_ref']}/submit"
                ),
                method="POST",
                data={
                    "decisions": [
                        {
                            "tool_call_id": "review-call-1",
                            "decision": "approve",
                        }
                    ],
                },
            )
            assert (
                stale_response.status_code == 409
            ), "Repeated submit should be rejected"

            denied_start = await start_queued_chat_stream_task(
                vault_name=vault.name,
                vault_path=str(vault),
                prompt="write another draft",
                image_paths=[],
                image_uploads=[],
                session_id="deferred-denial-session",
                tools=[],
                model="test",
                chat_mode="inline_edit",
            )
            denied_task = await self._wait_for_task_terminal(denied_start.task.task_id)
            assert denied_task is not None
            denied_events = await CHAT_TASK_EVENT_BUFFER.events_after(
                denied_start.task.task_id
            )
            denied_review_event = denied_events[0].data
            deny_path = (
                f"/api/vaults/{vault.name}/chat/deferred-denial-session/"
                f"deferred-reviews/{denied_review_event['artifact_ref']}/submit"
            )
            deny_data = {
                "decisions": [
                    {
                        "tool_call_id": "review-call-1",
                        "decision": "deny",
                        "message": "",
                    },
                    {
                        "tool_call_id": "review-call-2",
                        "decision": "deny",
                        "message": "Do not create either file.",
                    },
                ],
            }
            concurrent_responses = await asyncio.gather(
                asyncio.to_thread(
                    self.call_api, deny_path, method="POST", data=deny_data
                ),
                asyncio.to_thread(
                    self.call_api, deny_path, method="POST", data=deny_data
                ),
            )
            assert sorted(
                response.status_code for response in concurrent_responses
            ) == [
                200,
                409,
            ], "A pending review must be claimed atomically before any resume task starts"
            deny_response = next(
                response
                for response in concurrent_responses
                if response.status_code == 200
            )
            denied_resume_task_id = deny_response.json().get("task", {}).get("task_id")
            assert denied_resume_task_id
            denied_resume_task = await self._wait_for_task_terminal(
                denied_resume_task_id
            )
            assert denied_resume_task is not None
            denied_result = resume_capture["deferred_tool_results"]
            denial = denied_result.approvals["review-call-1"]
            assert (
                denial.message == INLINE_EDIT_DENIAL_MESSAGE
            ), "An empty optional comment must not hide the denial from the resumed model"
            assert (
                denied_result.approvals["review-call-2"].message
                == f"{INLINE_EDIT_DENIAL_MESSAGE} User reason: Do not create either file."
            )
        finally:
            chat_executor._prepare_chat_execution = original_prepare
            chat_executor._prepare_deferred_review_resume_execution = (
                original_prepare_resume
            )
            await self.stop_system()

    async def _wait_for_task_terminal(self, task_id: str):
        runtime = get_runtime_context()
        for _ in range(100):
            task = await runtime.task_coordinator.get_task(task_id)
            if task is not None and task.is_terminal:
                return task
            await asyncio.sleep(0.02)
        return None


async def _assert_deferred_review_commit_is_atomic(vault_name: str) -> None:
    """Prove messages, marker clearing, and review creation roll back together."""
    store = ChatStore()
    session_id = "deferred-atomicity-session"
    store.ensure_session(session_id, vault_name, owner_principal_id="local-user")
    store.update_session_metadata(
        session_id=session_id,
        vault_name=vault_name,
        metadata_update={"latest_turn_failure": {"status": "failed"}},
    )
    result = _DeferredReviewResult("atomic review")
    review = None
    try:
        with store.transaction() as connection:
            store.add_messages(
                session_id,
                vault_name,
                result.new_messages(),
                connection=connection,
            )
            store.update_session_metadata(
                session_id=session_id,
                vault_name=vault_name,
                remove_keys=("latest_turn_failure",),
                connection=connection,
            )
            review = create_deferred_review(
                vault_name=vault_name,
                session_id=session_id,
                originating_task_id="atomicity-probe",
                requests=result.output,
                resume_messages=result.all_messages(),
                resume_config={},
                connection=connection,
                log_created=False,
            )
            raise RuntimeError("force atomic rollback")
    except RuntimeError as exc:
        assert str(exc) == "force atomic rollback"
    else:
        raise AssertionError("Atomicity probe should force a rollback")

    assert review is not None
    assert store.get_history(session_id, vault_name) is None
    assert store.get_session_metadata(session_id, vault_name).get(
        "latest_turn_failure"
    ) == {"status": "failed"}
    assert (
        get_deferred_review(
            vault_name=vault_name,
            session_id=session_id,
            artifact_ref=review.artifact_ref,
        )
        is None
    )
