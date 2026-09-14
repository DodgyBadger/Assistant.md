"""Validate bounded automatic recovery for tool-free primary chat streams."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
from pydantic_ai import Agent, AgentRunResultEvent, PartStartEvent
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai_harness.step_persistence import (
    ContinuableSnapshot,
    RunRecord,
    StepEvent,
    ToolEffectRecord,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.chat.executor import PreparedChatExecution
from core.chat.run_recovery import ChatRunRecoveryCoordinator
from core.tools.base import ToolRecoveryPolicy
from core.vault_state.file_mutations import write_vault_file
from validation.core.base_scenario import BaseScenario
from validation.core.streaming import stream_events_context


class _FakeStreamResult:
    def __init__(self, prompt: str, response: str) -> None:
        self._prompt = prompt
        self._response = response
        self.output = response

    def new_messages(self):
        return [
            ModelRequest(parts=[UserPromptPart(content=self._prompt)]),
            ModelResponse(parts=[TextPart(self._response)]),
        ]


class _FlakyStreamAgent:
    def __init__(self, failures: int) -> None:
        self.remaining_failures = failures
        self.attempts = 0
        self.usage_ids: list[int] = []

    @stream_events_context
    async def run_stream_events(self, prompt, **kwargs):
        self.attempts += 1
        self.usage_ids.append(id(kwargs.get("usage")))
        if self.remaining_failures:
            self.remaining_failures -= 1
            yield PartStartEvent(index=0, part=TextPart("discarded partial response"))
            request = httpx.Request("POST", "https://provider.invalid/stream")
            raise httpx.ReadError("stream disconnected", request=request)
        response = "recovered primary response"
        yield PartStartEvent(index=0, part=TextPart(response))
        yield AgentRunResultEvent(result=_FakeStreamResult(prompt, response))


class _RollbackRestartAgent:
    def __init__(
        self,
        *,
        vault_path: Path,
        recovery: ChatRunRecoveryCoordinator,
        mutation_path: str = "notes/rollback-restart.md",
    ):
        self.vault_path = vault_path
        self.recovery = recovery
        self.mutation_path = mutation_path
        self.attempts = 0

    @stream_events_context
    async def run_stream_events(self, prompt, **kwargs):
        self.attempts += 1
        if self.attempts == 1:
            write_vault_file(
                vault_path=self.vault_path,
                path=self.mutation_path,
                content="must be rolled back\n",
            )
            await _seed_unresolved_vault_effect(
                self.recovery,
                conversation_id=str(kwargs.get("conversation_id")),
            )
            yield PartStartEvent(index=0, part=TextPart("discarded before rollback"))
            request = httpx.Request("POST", "https://provider.invalid/stream")
            raise httpx.ReadError("stream disconnected", request=request)
        response = "recovered after rollback restart"
        yield PartStartEvent(index=0, part=TextPart(response))
        yield AgentRunResultEvent(result=_FakeStreamResult(prompt, response))


class ChatStreamAutoRetryScenario(BaseScenario):
    """Prove safe primary-chat retries and the explicit disable control."""

    async def test_scenario(self):
        vault = self.create_vault("ChatStreamAutoRetryVault")
        await self.start_system()

        import core.chat.executor as chat_executor

        agents: list[_FlakyStreamAgent] = []

        async def _prepared_failure(*args, **kwargs):
            del args
            prompt = kwargs.get("prompt", "retry primary stream")
            agent = _FlakyStreamAgent(failures=1)
            agents.append(agent)
            return PreparedChatExecution(
                agent=agent,
                message_history=None,
                prompt_for_history=prompt,
                user_prompt=prompt,
                attached_image_count=0,
                model="test",
                tools=[],
            )

        original_prepare = chat_executor._prepare_chat_execution
        chat_executor._prepare_chat_execution = _prepared_failure
        try:
            stalled_agent, stalled_attempts = _stalled_stream_agent()

            async def _prepared_stall(*args, **kwargs):
                del args, kwargs
                return PreparedChatExecution(
                    agent=stalled_agent,
                    message_history=None,
                    prompt_for_history="stall primary stream",
                    user_prompt="stall primary stream",
                    attached_image_count=0,
                    model="test",
                    tools=[],
                )

            idle_update = self.call_api(
                "/api/system/settings/general/model_stream_idle_timeout_seconds",
                method="PUT",
                data={"value": "0.05"},
            )
            assert idle_update.status_code == 200
            retry_disable = self.call_api(
                "/api/system/settings/general/model_stream_retries",
                method="PUT",
                data={"value": "0"},
            )
            assert retry_disable.status_code == 200
            checkpoint = self.event_checkpoint()
            chat_executor._prepare_chat_execution = _prepared_stall
            stalled = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "stall primary stream",
                    "session_id": "primary_idle_timeout",
                    "tools": [],
                    "model": "test",
                }
            )
            assert stalled["terminal_event"].get("event") == "error"
            assert (
                stalled["terminal_event"].get("details", {}).get("failure_kind")
                == "model_stream_idle_timeout"
            )
            assert len(stalled_attempts) == 1
            stalled_task = self.call_api(f"/api/tasks/{stalled['task_ids'][-1]}").json()
            assert (
                stalled_task.get("metadata", {}).get("model_stream_state")
                == "waiting_for_model"
            )
            self.assert_event_contains(
                self.events_since(checkpoint),
                name="model_stream_idle_timed_out",
                expected={
                    "session_id": "primary_idle_timeout",
                    "model": "test",
                    "attempt": 1,
                    "active_tool_count": 0,
                },
            )

            slow_tool_agent = _slow_tool_agent()

            async def _prepared_slow_tool(*args, **kwargs):
                del args, kwargs
                return PreparedChatExecution(
                    agent=slow_tool_agent,
                    message_history=None,
                    prompt_for_history="run slow visible tool",
                    user_prompt="run slow visible tool",
                    attached_image_count=0,
                    model="test",
                    tools=["slow_probe"],
                )

            chat_executor._prepare_chat_execution = _prepared_slow_tool
            slow_tool = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "run slow visible tool",
                    "session_id": "primary_slow_tool",
                    "tools": ["slow_probe"],
                    "model": "test",
                }
            )
            assert slow_tool["terminal_event"].get("event") == "done"
            assert slow_tool["text"] == "slow tool completed"
            idle_restore = self.call_api(
                "/api/system/settings/general/model_stream_idle_timeout_seconds",
                method="PUT",
                data={"value": "120"},
            )
            assert idle_restore.status_code == 200
            retry_restore = self.call_api(
                "/api/system/settings/general/model_stream_retries",
                method="PUT",
                data={"value": "1"},
            )
            assert retry_restore.status_code == 200
            chat_executor._prepare_chat_execution = _prepared_failure

            delay_update = self.call_api(
                "/api/system/settings/general/model_stream_retry_base_delay_seconds",
                method="PUT",
                data={"value": "0"},
            )
            assert delay_update.status_code == 200, "Retry delay should be configurable"

            recovered = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "retry primary stream",
                    "session_id": "primary_auto_retry",
                    "tools": [],
                    "model": "test",
                }
            )
            retry_events = [
                event
                for event in recovered["events"]
                if event.get("event") == "chat_retry_scheduled"
            ]
            assert recovered["terminal_event"].get("event") == "done"
            assert recovered["text"] == "recovered primary response"
            assert agents[0].attempts == 2
            assert len(set(agents[0].usage_ids)) == 1, "Attempts share Pydantic usage"
            assert len(retry_events) == 1
            assert retry_events[0].get("reset_response") is True
            assert retry_events[0].get("replay_scope") == "no_chat_tools"

            checkpoint_agent, tool_effects = _checkpoint_recovery_agent(
                session_id="primary_checkpoint_retry"
            )

            async def _prepared_checkpoint(*args, **kwargs):
                del args, kwargs
                agent, recovery = checkpoint_agent
                return PreparedChatExecution(
                    agent=agent,
                    message_history=None,
                    prompt_for_history="recover after tool",
                    user_prompt="recover after tool",
                    attached_image_count=0,
                    model="test",
                    tools=["read_probe"],
                    recovery=recovery,
                )

            chat_executor._prepare_chat_execution = _prepared_checkpoint
            checkpoint_recovered = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "recover after tool",
                    "session_id": "primary_checkpoint_retry",
                    "tools": ["read_probe"],
                    "model": "test",
                }
            )
            checkpoint_events = [
                event
                for event in checkpoint_recovered["events"]
                if event.get("event") == "chat_retry_scheduled"
            ]
            selected_events = [
                event
                for event in checkpoint_recovered["events"]
                if event.get("event") == "chat_recovery_checkpoint_selected"
            ]
            assert checkpoint_recovered["terminal_event"].get("event") == "done"
            assert checkpoint_recovered["text"] == "recovered after checkpoint"
            assert tool_effects == [
                "first",
                "second",
            ], "Completed tools must not be replayed"
            assert len(checkpoint_events) == 1
            assert len(selected_events) == 1
            assert checkpoint_events[0].get("strategy") == "resume_snapshot"
            assert checkpoint_events[0].get("trimmed_failed_response") is True

            history = chat_executor._CHAT_STORE.get_history(
                "primary_checkpoint_retry", vault.name
            )
            parts = [part for message in history for part in message.parts]
            assert sum(isinstance(part, ToolCallPart) for part in parts) == 2
            assert sum(isinstance(part, ToolReturnPart) for part in parts) == 2
            assert not any(
                isinstance(part, TextPart) and "discarded" in part.content
                for part in parts
            )

            rollback_recovery = ChatRunRecoveryCoordinator(
                tool_policies={"file_write": ToolRecoveryPolicy.VAULT_TRANSACTIONAL}
            )
            rollback_agent = _RollbackRestartAgent(
                vault_path=Path(vault),
                recovery=rollback_recovery,
            )

            async def _prepared_rollback(*args, **kwargs):
                del args, kwargs
                return PreparedChatExecution(
                    agent=rollback_agent,
                    message_history=None,
                    prompt_for_history="recover vault mutation",
                    user_prompt="recover vault mutation",
                    attached_image_count=0,
                    model="test",
                    tools=["file_write"],
                    recovery=rollback_recovery,
                )

            chat_executor._prepare_chat_execution = _prepared_rollback
            rollback_recovered = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "recover vault mutation",
                    "session_id": "primary_rollback_restart",
                    "tools": ["file_write"],
                    "model": "test",
                }
            )
            redirects = [
                event
                for event in rollback_recovered["events"]
                if event.get("event") == "chat_retry_redirect"
            ]
            assert rollback_recovered["terminal_event"].get("event") == "done"
            assert rollback_recovered["text"] == "recovered after rollback restart"
            assert rollback_agent.attempts == 2
            assert len(rollback_recovered["task_ids"]) == 2
            assert len(redirects) == 1
            assert redirects[0].get("strategy") == "terminal_rollback_restart"
            assert not (Path(vault) / "notes/rollback-restart.md").exists()
            rollback_history = chat_executor._CHAT_STORE.get_history(
                "primary_rollback_restart", vault.name
            )
            rollback_parts = [
                part for message in rollback_history for part in message.parts
            ]
            assert sum(isinstance(part, UserPromptPart) for part in rollback_parts) == 1
            assert (
                sum(
                    isinstance(part, TextPart)
                    and part.content == "recovered after rollback restart"
                    for part in rollback_parts
                )
                == 1
            )

            rollback_disabled_update = self.call_api(
                "/api/system/settings/general/task_rollback_enabled",
                method="PUT",
                data={"value": "false"},
            )
            assert rollback_disabled_update.status_code == 200
            disabled_recovery = ChatRunRecoveryCoordinator(
                tool_policies={"file_write": ToolRecoveryPolicy.VAULT_TRANSACTIONAL}
            )
            disabled_rollback_agent = _RollbackRestartAgent(
                vault_path=Path(vault),
                recovery=disabled_recovery,
                mutation_path="notes/rollback-disabled.md",
            )

            async def _prepared_disabled_rollback(*args, **kwargs):
                del args, kwargs
                return PreparedChatExecution(
                    agent=disabled_rollback_agent,
                    message_history=None,
                    prompt_for_history="do not restart without rollback",
                    user_prompt="do not restart without rollback",
                    attached_image_count=0,
                    model="test",
                    tools=["file_write"],
                    recovery=disabled_recovery,
                )

            chat_executor._prepare_chat_execution = _prepared_disabled_rollback
            rollback_blocked = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "do not restart without rollback",
                    "session_id": "primary_rollback_disabled",
                    "tools": ["file_write"],
                    "model": "test",
                }
            )
            assert rollback_blocked["terminal_event"].get("event") == "error"
            assert disabled_rollback_agent.attempts == 1
            assert len(rollback_blocked["task_ids"]) == 1
            assert (Path(vault) / "notes/rollback-disabled.md").exists()

            rollback_enabled_update = self.call_api(
                "/api/system/settings/general/task_rollback_enabled",
                method="PUT",
                data={"value": "true"},
            )
            assert rollback_enabled_update.status_code == 200

            disabled_update = self.call_api(
                "/api/system/settings/general/model_stream_retries",
                method="PUT",
                data={"value": "0"},
            )
            assert disabled_update.status_code == 200, "Automatic retry can be disabled"
            chat_executor._prepare_chat_execution = _prepared_failure
            disabled = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "do not retry primary stream",
                    "session_id": "primary_retry_disabled",
                    "tools": [],
                    "model": "test",
                }
            )
            assert disabled["terminal_event"].get("event") == "error"
            assert agents[1].attempts == 1
            assert not any(
                event.get("event") == "chat_retry_scheduled"
                for event in disabled["events"]
            )
        finally:
            chat_executor._prepare_chat_execution = original_prepare
            await self.stop_system()
            self.teardown_scenario()


def _checkpoint_recovery_agent(
    *, session_id: str
) -> tuple[tuple[Agent[Any, str], ChatRunRecoveryCoordinator], list[str]]:
    """Build an agent that disconnects after one completed read-only tool."""
    stream_calls = 0
    tool_effects: list[str] = []

    async def stream(
        messages: list[Any], _info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        nonlocal stream_calls
        stream_calls += 1
        tool_return_count = sum(
            isinstance(part, ToolReturnPart)
            for message in messages
            for part in message.parts
        )
        if tool_return_count < 2:
            value = "first" if tool_return_count == 0 else "second"
            yield {
                0: DeltaToolCall(
                    name="read_probe",
                    json_args=f'{{"value":"{value}"}}',
                    tool_call_id=f"read-probe-{tool_return_count + 1}",
                )
            }
            return
        if stream_calls == 3:
            yield "discarded partial response"
            request = httpx.Request("POST", "https://provider.invalid/stream")
            raise httpx.ReadError("stream disconnected", request=request)
        yield "recovered after checkpoint"

    recovery = ChatRunRecoveryCoordinator()
    agent = Agent(
        FunctionModel(stream_function=stream),
        capabilities=[recovery.capability(session_id=session_id)],
    )

    @agent.tool_plain
    async def read_probe(value: str) -> str:
        tool_effects.append(value)
        return "read complete"

    return (agent, recovery), tool_effects


def _slow_tool_agent() -> Agent[Any, str]:
    """Build an agent whose visible tool legitimately exceeds the model idle limit."""

    async def stream(
        messages: list[Any], _info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        has_result = any(
            isinstance(part, ToolReturnPart)
            for message in messages
            for part in message.parts
        )
        if not has_result:
            yield {
                0: DeltaToolCall(
                    name="slow_probe",
                    json_args="{}",
                    tool_call_id="slow-probe-1",
                )
            }
            return
        yield "slow tool completed"

    agent = Agent(FunctionModel(stream_function=stream))

    @agent.tool_plain
    async def slow_probe() -> str:
        await asyncio.sleep(0.1)
        return "complete"

    return agent


def _stalled_stream_agent() -> tuple[Agent[Any, str], list[bool]]:
    """Build a Pydantic agent whose model stream never emits an event."""
    attempts: list[bool] = []

    async def stream(
        _messages: list[Any], _info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        attempts.append(True)
        await asyncio.Event().wait()
        yield "unreachable"  # pragma: no cover - unreachable

    return Agent(FunctionModel(stream_function=stream)), attempts


async def _seed_unresolved_vault_effect(
    recovery: ChatRunRecoveryCoordinator,
    *,
    conversation_id: str,
) -> None:
    run_id = "rollback-source-run"
    await recovery.store.register_run(
        RunRecord(run_id=run_id, conversation_id=conversation_id)
    )
    await recovery.store.append_event(
        StepEvent(
            run_id=run_id,
            conversation_id=conversation_id,
            kind="run_started",
            step_index=0,
        )
    )
    await recovery.store.save_snapshot(
        ContinuableSnapshot(
            run_id=run_id,
            conversation_id=conversation_id,
            step_index=1,
            state="interrupted",
            messages=[
                ModelRequest(parts=[UserPromptPart(content="recover vault mutation")]),
                ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name="file_write",
                            args={"operation": "write"},
                            tool_call_id="write-1",
                        )
                    ]
                ),
            ],
        )
    )
    await recovery.store.record_tool_effect(
        ToolEffectRecord(
            run_id=run_id,
            tool_call_id="write-1",
            tool_name="file_write",
            status="started",
        )
    )
    await recovery.store.append_event(
        StepEvent(
            run_id=run_id,
            conversation_id=conversation_id,
            kind="run_failed",
            step_index=1,
        )
    )
