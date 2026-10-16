"""Integration scenario for cancelling an active task-owned chat turn."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from core.chat.executor import PreparedChatExecution
from core.chat.tool_history import analyze_tool_history
from core.runtime.execution_tasks import chat_session_scope
from core.runtime.state import get_runtime_context
from core.utils.messages import extract_role_and_text
from core.vault_state.file_mutations import write_vault_file
from validation.core.base_scenario import BaseScenario


class ChatCancellationScenario(BaseScenario):
    """Validate active chat task lookup and cancellation by session id."""

    async def test_scenario(self):
        vault = self.create_vault("ChatCancellationVault")

        await self.start_system()

        import core.chat.executor as chat_executor

        tool_started = asyncio.Event()

        async def hanging_tool() -> str:
            write_vault_file(
                vault_path=vault,
                path="notes/cancelled-chat-write.md",
                content="created before cancellation\n",
            )
            tool_started.set()
            await asyncio.Event().wait()
            return "unreachable"

        async def _prepared_hanging_chat(*args, **kwargs):
            return PreparedChatExecution(
                agent=Agent(
                    TestModel(call_tools=["hanging_tool"]), tools=[hanging_tool]
                ),
                message_history=None,
                prompt_for_history="Cancel this chat.",
                user_prompt="Cancel this chat.",
                attached_image_count=0,
                model="test",
                tools=[],
            )

        original_prepare_chat_execution = chat_executor._prepare_chat_execution
        original_prepare_agent_config = chat_executor._prepare_agent_config
        chat_executor._prepare_chat_execution = _prepared_hanging_chat
        session_id = "chat_cancellation_session"
        try:
            start_response = self.call_api(
                "/api/chat/tasks",
                method="POST",
                data={
                    "vault_name": vault.name,
                    "prompt": "Cancel this chat.",
                    "session_id": session_id,
                    "tools": [],
                    "model": "test",
                },
            )
            assert start_response.status_code == 200, "Chat task should start"
            task_id = start_response.json().get("task", {}).get("task_id")
            assert task_id, "Chat task start should return a task id"

            runtime = get_runtime_context()
            active = []
            for _ in range(50):
                active = await runtime.task_coordinator.list_tasks(
                    scope=chat_session_scope(session_id),
                    include_terminal=False,
                )
                if active and active[-1].status == "running":
                    break
                await asyncio.sleep(0.05)

            assert (
                active and active[-1].status == "running"
            ), "Chat execution should register a running task"
            self.soft_assert_equal(
                active[-1].task_id,
                task_id,
                "Active task list should include the started chat task",
            )
            written_path = Path(vault) / "notes/cancelled-chat-write.md"
            for _ in range(50):
                if written_path.exists():
                    break
                await asyncio.sleep(0.05)
            assert (
                written_path.exists()
            ), "Hanging chat should write the rollback probe before cancellation"
            assert (
                tool_started.is_set()
            ), "Stop must interrupt an actual model tool call"

            active_response = self.call_api(
                f"/api/chat/sessions/{session_id}/active-task"
            )
            assert (
                active_response.status_code == 200
            ), "Active chat task endpoint succeeds"
            assert (
                active_response.json().get("task_id") == task_id
            ), "Active chat task endpoint returns the running task"

            checkpoint = self.event_checkpoint()
            cancel_response = self.call_api(
                f"/api/chat/sessions/{session_id}/cancel",
                method="POST",
            )
            assert (
                cancel_response.status_code == 200
            ), "Chat session cancel endpoint succeeds"
            assert (
                cancel_response.json().get("cancelled") is True
            ), "Chat session cancel should acknowledge cancellation"

            for _ in range(50):
                task_snapshot = await runtime.task_coordinator.get_task(task_id)
                if task_snapshot is not None and task_snapshot.is_terminal:
                    break
                await asyncio.sleep(0.05)
            rollback_events = self.events_since(checkpoint)

            task_detail = self.call_api(f"/api/tasks/{task_id}")
            assert (
                task_detail.status_code == 200
            ), "Cancelled task detail remains queryable"
            assert (
                task_detail.json().get("status") == "cancelled"
            ), "Cancelled chat task should have cancelled terminal status"
            assert not (
                Path(vault) / "notes/cancelled-chat-write.md"
            ).exists(), (
                "Cancelled chat task should rollback files created before cancellation"
            )
            self.assert_event_contains(
                rollback_events,
                name="task_rollback_completed",
                expected={
                    "task_id": task_id,
                    "terminal_status": "cancelled",
                },
            )

            active_after_cancel = self.call_api(
                f"/api/chat/sessions/{session_id}/active-task"
            )
            assert (
                active_after_cancel.status_code == 404
            ), "Cancelled chat session should no longer have an active task"

            detail = self.call_api(
                f"/api/chat/sessions/{session_id}?vault_name={vault.name}"
            )
            assert (
                detail.status_code == 200
            ), "Cancelled chat session detail is persisted"
            messages = detail.json().get("messages", [])
            assert (
                len(messages) == 1
            ), "Cancelled chat should persist only the user turn"
            assert (
                messages[0].get("role") == "user"
            ), "Cancelled chat persisted message should be the user prompt"
            assert "Cancel this chat." in messages[0].get(
                "content", ""
            ), "Cancelled chat should retain the submitted prompt"

            def _patched_prepare_agent_config(
                vault_name, vault_path, tools, model, thinking=None, chat_mode=None
            ):
                del vault_name, vault_path, tools, model, thinking
                return ("Answer briefly.", "", TestModel(), [])

            captured_preflight_history = []

            async def _capturing_prepare_chat_execution(*args, **kwargs):
                prepared = await original_prepare_chat_execution(*args, **kwargs)
                captured_preflight_history.extend(
                    extract_role_and_text(message)
                    for message in (prepared.message_history or [])
                )
                return prepared

            chat_executor._prepare_agent_config = _patched_prepare_agent_config
            chat_executor._prepare_chat_execution = _capturing_prepare_chat_execution

            follow_up = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "Continue after cancellation.",
                    "session_id": session_id,
                    "tools": [],
                    "model": "test",
                }
            )
            assert follow_up["text"], "Follow-up chat execution should complete"
            assert (
                follow_up["terminal_event"].get("event") == "done"
            ), "Follow-up chat task should complete"
            assert captured_preflight_history == [
                ("user", "Cancel this chat.")
            ], "The next turn should load the cancelled user prompt from persisted session history"
            history = chat_executor._CHAT_STORE.get_history(session_id, vault.name)
            assert analyze_tool_history(
                history or []
            ).ok, "Stop and follow-up must not leave unresolved tool calls in model history"
            activity = self.call_api("/api/system/activity-log?limit=200").json()
            rows = [
                entry["data"]
                for entry in activity["entries"]
                if entry.get("data", {}).get("session_id") == session_id
            ]
            assert any(
                row.get("event") == "chat_turn_cancelled"
                and row.get("task_id") == task_id
                for row in rows
            )
            assert any(
                row.get("event") == "chat_turn_completed"
                and row.get("task_id")
                and row["task_id"] != task_id
                for row in rows
            )
        finally:
            chat_executor._prepare_chat_execution = original_prepare_chat_execution
            chat_executor._prepare_agent_config = original_prepare_agent_config
            await self.stop_system()
            self.teardown_scenario()
