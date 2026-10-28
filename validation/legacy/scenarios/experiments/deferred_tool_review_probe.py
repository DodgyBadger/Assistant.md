"""Probe Pydantic AI deferred tool review behavior for inline editing."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from pydantic_ai import (
    Agent,
    AgentRunResultEvent,
    DeferredToolRequests,
    DeferredToolResults,
    ToolApproved,
    ToolDenied,
)
from pydantic_ai.messages import FunctionToolCallEvent, ToolReturnPart
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import Tool

from core.tools.file_write import FileWrite
from validation.support.base_scenario import BaseScenario


class DeferredToolReviewProbeScenario(BaseScenario):
    """Document deferred approval mechanics before production integration."""

    async def test_scenario(self) -> None:
        vault = self.create_vault("DeferredToolReviewProbeVault")
        calls: list[tuple[str, str]] = []

        def reviewed_write(path: str, content: str) -> str:
            calls.append((path, content))
            return f"wrote {path}: {content}"

        agent = Agent(
            model=TestModel(call_tools=["reviewed_write"], custom_output_text="done"),
            tools=[
                Tool(
                    reviewed_write,
                    name="reviewed_write",
                    requires_approval=True,
                )
            ],
            output_type=[str, DeferredToolRequests],
        )

        first_result = await agent.run("write a file")
        assert isinstance(
            first_result.output, DeferredToolRequests
        ), "Deferred tool call should become run output"
        assert not calls, "Deferred tool should not execute before approval"
        assert (
            len(first_result.output.approvals) == 1
        ), "One approval request should be returned"

        approval_call = first_result.output.approvals[0]
        assert (
            approval_call.tool_name == "reviewed_write"
        ), "Approval request should preserve tool name"
        assert (
            approval_call.tool_call_id
        ), "Approval request should include a tool call id"
        assert dict(approval_call.args) == {
            "path": "a",
            "content": "a",
        }, "TestModel should provide deterministic tool args"

        approved_result = await agent.run(
            message_history=first_result.all_messages(),
            deferred_tool_results=DeferredToolResults(
                approvals={
                    approval_call.tool_call_id: ToolApproved(
                        override_args={
                            "path": "reviewed.md",
                            "content": "edited content",
                        }
                    )
                }
            ),
        )
        assert (
            approved_result.output == "done"
        ), "Approved resume should complete normally"
        assert calls == [
            ("reviewed.md", "edited content")
        ], "ToolApproved override_args should replace original tool args"
        approved_tool_return = _first_tool_return(approved_result.new_messages())
        assert (
            approved_tool_return is not None
        ), "Approved resume should add a canonical tool return message"
        assert (
            approved_tool_return.tool_call_id == approval_call.tool_call_id
        ), "Approved tool return should use the original tool call id"
        assert (
            approved_tool_return.content == "wrote reviewed.md: edited content"
        ), "Approved resumes must return the executed tool's actual response to the model"
        assert approved_tool_return.outcome == "success"

        unchanged_first_result = await agent.run("write without editing")
        unchanged_call = unchanged_first_result.output.approvals[0]
        unchanged_result = await agent.run(
            message_history=unchanged_first_result.all_messages(),
            deferred_tool_results=DeferredToolResults(
                approvals={unchanged_call.tool_call_id: True}
            ),
        )
        unchanged_tool_return = _first_tool_return(unchanged_result.new_messages())
        assert unchanged_tool_return is not None
        assert (
            unchanged_tool_return.content == "wrote a: a"
        ), "Unedited approvals must return the executed tool's actual response to the model"
        assert unchanged_tool_return.outcome == "success"

        denied_first_result = await agent.run("write another file")
        denied_call = denied_first_result.output.approvals[0]
        denied_result = await agent.run(
            message_history=denied_first_result.all_messages(),
            deferred_tool_results=DeferredToolResults(
                approvals={
                    denied_call.tool_call_id: ToolDenied(
                        "Please revise this before writing."
                    )
                }
            ),
        )
        assert (
            denied_result.output == "done"
        ), "Denied resume should continue the agent run"
        assert calls == [
            ("reviewed.md", "edited content"),
            ("a", "a"),
        ], "Denied deferred tool should not execute"
        denied_tool_return = _first_tool_return(denied_result.new_messages())
        assert (
            denied_tool_return is not None
        ), "Denied resume should add a tool return message"
        assert (
            denied_tool_return.content == "Please revise this before writing."
        ), "Denied reason should be the canonical tool response"
        assert denied_tool_return.outcome == "denied"

        default_denied_first_result = await agent.run("deny without a reason")
        default_denied_call = default_denied_first_result.output.approvals[0]
        default_denied_result = await agent.run(
            message_history=default_denied_first_result.all_messages(),
            deferred_tool_results=DeferredToolResults(
                approvals={default_denied_call.tool_call_id: ToolDenied()}
            ),
        )
        default_denied_tool_return = _first_tool_return(
            default_denied_result.new_messages()
        )
        assert default_denied_tool_return is not None
        assert default_denied_tool_return.content == "The tool call was denied."
        assert default_denied_tool_return.outcome == "denied"
        assert calls == [
            ("reviewed.md", "edited content"),
            ("a", "a"),
        ], "Neither denial variant should execute the reviewed tool"

        stream_events: list[Any] = []
        async with agent.run_stream_events("stream one deferred call") as events:
            async for event in events:
                stream_events.append(event)

        assert any(
            isinstance(event, FunctionToolCallEvent) for event in stream_events
        ), "Streaming deferred run should emit a tool-call event"
        final_events = [
            event for event in stream_events if isinstance(event, AgentRunResultEvent)
        ]
        assert (
            len(final_events) == 1
        ), "Streaming deferred run should emit one final result"
        assert isinstance(
            final_events[0].result.output, DeferredToolRequests
        ), "Streaming final output should be DeferredToolRequests"

        await _assert_file_write_result_delivery(vault)
        await _assert_mixed_result_delivery()


async def _assert_file_write_result_delivery(vault: Path) -> None:
    base_tool = FileWrite.get_tool(vault_path=str(vault))
    reviewed_tool = Tool(
        base_tool.function,
        name="file_write",
        description=base_tool.description,
        requires_approval=True,
    )
    agent = Agent(
        model=TestModel(call_tools=["file_write"], custom_output_text="done"),
        tools=[reviewed_tool],
        output_type=[str, DeferredToolRequests],
    )

    create_request = await agent.run("create a reviewed file")
    create_call = create_request.output.approvals[0]
    create_result = await agent.run(
        message_history=create_request.all_messages(),
        deferred_tool_results=DeferredToolResults(
            approvals={
                create_call.tool_call_id: ToolApproved(
                    override_args={
                        "operation": "write",
                        "path": "Actual.md",
                        "content": "actual content",
                    }
                )
            }
        ),
    )
    create_return = _first_tool_return(create_result.new_messages())
    assert create_return is not None
    assert create_return.content == (
        "Successfully created new file 'Actual.md' with 14 characters"
    ), "The model must receive file_write's actual successful response"
    assert (vault / "Actual.md").read_text(encoding="utf-8") == "actual content"

    conflict_request = await agent.run("try the reviewed write again")
    conflict_call = conflict_request.output.approvals[0]
    conflict_result = await agent.run(
        message_history=conflict_request.all_messages(),
        deferred_tool_results=DeferredToolResults(
            approvals={
                conflict_call.tool_call_id: ToolApproved(
                    override_args={
                        "operation": "write",
                        "path": "Actual.md",
                        "content": "must not replace",
                    }
                )
            }
        ),
    )
    conflict_return = _first_tool_return(conflict_result.new_messages())
    assert conflict_return is not None
    assert (
        conflict_return.content == "Cannot write to 'Actual.md' - file already exists."
    ), "The model must receive file_write's actual rejected-operation response"
    assert (vault / "Actual.md").read_text(encoding="utf-8") == "actual content"


async def _assert_mixed_result_delivery() -> None:
    executed: list[tuple[str, str]] = []

    def reviewed_create(value: str) -> str:
        executed.append(("create", value))
        return f"created {value}"

    def reviewed_delete(value: str) -> str:
        executed.append(("delete", value))
        return f"deleted {value}"

    agent = Agent(
        model=TestModel(
            call_tools=["reviewed_create", "reviewed_delete"],
            custom_output_text="done",
        ),
        tools=[
            Tool(reviewed_create, requires_approval=True),
            Tool(reviewed_delete, requires_approval=True),
        ],
        output_type=[str, DeferredToolRequests],
    )
    request = await agent.run("review two calls")
    calls_by_name = {call.tool_name: call for call in request.output.approvals}
    create_call = calls_by_name["reviewed_create"]
    delete_call = calls_by_name["reviewed_delete"]
    result = await agent.run(
        message_history=request.all_messages(),
        deferred_tool_results=DeferredToolResults(
            approvals={
                create_call.tool_call_id: ToolApproved(
                    override_args={"value": "draft.md"}
                ),
                delete_call.tool_call_id: ToolDenied("Keep the existing file."),
            }
        ),
    )
    returns = {
        part.tool_call_id: part
        for message in result.new_messages()
        for part in getattr(message, "parts", ()) or ()
        if isinstance(part, ToolReturnPart)
    }
    assert returns[create_call.tool_call_id].content == "created draft.md"
    assert returns[create_call.tool_call_id].outcome == "success"
    assert returns[delete_call.tool_call_id].content == "Keep the existing file."
    assert returns[delete_call.tool_call_id].outcome == "denied"
    assert executed == [
        ("create", "draft.md")
    ], "Mixed review results must execute only their matching approved calls"


def _first_tool_return(messages: list[Any]) -> ToolReturnPart | None:
    for message in messages:
        for part in getattr(message, "parts", ()) or ():
            if isinstance(part, ToolReturnPart):
                return part
    return None
