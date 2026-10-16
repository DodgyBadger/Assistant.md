"""Integration scenario for tool-history integrity handling."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    NativeToolCallPart,
    NativeToolReturnPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from validation.core.base_scenario import BaseScenario, with_local_user_authority
from validation.core.tool_history_fixtures import tool_reply_cases


class ToolHistoryIntegrityScenario(BaseScenario):
    """Validate batch-safe tool history retrieval and integrity diagnostics."""

    @with_local_user_authority
    async def test_scenario(self):
        self._check_invocation_identity()
        await self._check_agent_retry()
        vault = self.create_vault("ToolHistoryIntegrityVault")

        await self.start_system()

        from core.authoring.runtime import WorkflowAuthoringHost, run_authoring_monty

        now = datetime(2026, 6, 12, 12, 0, 0)
        workflow_id = f"{vault.name}/tool-history-integrity"

        batch_host = WorkflowAuthoringHost(
            workflow_id=workflow_id,
            vault_path=str(vault),
            reference_date=now,
            session_key="tool_history_batch_session",
            chat_session_id="tool_history_batch_session",
            message_history=_batch_history(),
        )

        checkpoint = self.event_checkpoint()
        batch_result = await run_authoring_monty(
            workflow_id=workflow_id,
            code=TOOL_HISTORY_BATCH_CODE,
            host=batch_host,
            script_name="tool_history_batch.py",
        )
        batch_events = self.events_since(checkpoint)

        self.assert_event_contains(
            batch_events,
            name="authoring_retrieve_history_completed",
            expected={
                "workflow_id": workflow_id,
                "item_count": 3,
                "tool_history_integrity_status": "ok",
                "multi_call_batch_count": 1,
                "multi_return_batch_count": 1,
            },
        )
        self.soft_assert_equal(
            batch_result.value["batch_item_indexes"],
            [1],
            "Parallel tool calls should be returned as one atomic batch history item",
        )
        self.soft_assert_equal(
            batch_result.value["exchange_count"],
            2,
            "ToolExchangeBatch should expose both exchanges",
        )
        self.soft_assert(
            batch_result.value["assembled_batch_role_seen"],
            "assemble_context should preserve the batch as a structured history item",
        )

        orphan_host = WorkflowAuthoringHost(
            workflow_id=workflow_id,
            vault_path=str(vault),
            reference_date=now,
            session_key="tool_history_orphan_session",
            chat_session_id="tool_history_orphan_session",
            message_history=_orphan_return_history(),
        )

        checkpoint = self.event_checkpoint()
        orphan_result = await run_authoring_monty(
            workflow_id=workflow_id,
            code=TOOL_HISTORY_ORPHAN_CODE,
            host=orphan_host,
            script_name="tool_history_orphan.py",
        )
        orphan_events = self.events_since(checkpoint)

        self.assert_event_contains(
            orphan_events,
            name="authoring_retrieve_history_tool_integrity_issue",
            expected={
                "workflow_id": workflow_id,
                "status": "issues",
            },
        )
        self.soft_assert_equal(
            orphan_result.value["issue_codes"],
            ["orphan_tool_return"],
            "Orphan tool return should be surfaced through integrity metadata",
        )

        await self.stop_system()
        self.teardown_scenario()
        self.assert_no_failures()

    async def _check_agent_retry(self) -> None:
        """A recoverable real tool failure must produce an admissible transcript."""
        from pydantic_ai import Agent, ModelRetry
        from pydantic_ai.models.function import FunctionModel

        from core.chat.tool_history import analyze_tool_history

        response_count = 0
        invocation_count = 0

        def provider(_messages: object, _info: object) -> ModelResponse:
            nonlocal response_count
            response_count += 1
            if response_count <= 2:
                return ModelResponse(
                    parts=[ToolCallPart("probe", {}, f"call-{response_count}")]
                )
            return ModelResponse(parts=[TextPart("Recovered")])

        agent = Agent(FunctionModel(provider), retries=1)

        @agent.tool_plain
        def probe() -> str:
            nonlocal invocation_count
            invocation_count += 1
            if invocation_count == 1:
                raise ModelRetry("Retry this tool")
            return "Succeeded"

        result = await agent.run("Try the tool")
        integrity = analyze_tool_history(result.all_messages())
        assert invocation_count == 2
        assert integrity.ok, integrity.to_dict()
        assert (integrity.tool_call_count, integrity.tool_return_count) == (2, 1)

    def _check_invocation_identity(self) -> None:
        from core.chat.tool_history import analyze_tool_history

        for case in tool_reply_cases():
            integrity = analyze_tool_history(case.messages)
            assert {
                issue.code for issue in integrity.issues
            } == case.issue_codes, case.name
            assert integrity.ok == (not case.issue_codes), case.name
            assert (integrity.tool_call_count, integrity.tool_return_count) == (
                case.call_count,
                case.return_count,
            ), case.name
            if case.name == "valid-mixed-batch":
                assert integrity.multi_call_batch_count == 1
                assert integrity.multi_return_batch_count == 0

        for tool_call_id in ("", " \t "):
            integrity = analyze_tool_history(
                [
                    ModelResponse(
                        parts=[
                            ToolCallPart(
                                tool_name="probe", args={}, tool_call_id=tool_call_id
                            )
                        ]
                    ),
                    ModelRequest(
                        parts=[
                            ToolReturnPart(
                                tool_name="probe",
                                content="result",
                                tool_call_id=tool_call_id,
                            )
                        ]
                    ),
                ]
            )
            assert {issue.code for issue in integrity.issues} == {
                "missing_tool_call_id",
                "missing_tool_return_id",
            }
            assert (integrity.tool_call_count, integrity.tool_return_count) == (1, 1)
            assert all(issue.severity == "error" for issue in integrity.issues)

        mismatched = analyze_tool_history(
            [
                ModelResponse(
                    parts=[ToolCallPart(tool_name="alpha", args={}, tool_call_id="id")]
                ),
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            tool_name="beta", content="result", tool_call_id="id"
                        )
                    ]
                ),
            ]
        )
        assert [issue.code for issue in mismatched.issues] == [
            "tool_return_name_mismatch"
        ]
        assert mismatched.issues[0].message_index == 1
        assert mismatched.issues[0].tool_call_id == "id"
        assert not mismatched.ok
        assert analyze_tool_history(_batch_history()).ok
        assert analyze_tool_history(
            [
                ModelResponse(
                    parts=[
                        NativeToolCallPart(
                            tool_name="web_search", args={}, tool_call_id="native-id"
                        ),
                        NativeToolReturnPart(
                            tool_name="web_search",
                            content="native result",
                            tool_call_id="native-id",
                        ),
                    ]
                )
            ]
        ).ok


def _batch_history():
    return [
        ModelRequest(parts=[UserPromptPart(content="Use two tools.")]),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="source_lookup",
                    args={"path": "sources/a.md"},
                    tool_call_id="call-a",
                ),
                ToolCallPart(
                    tool_name="source_lookup",
                    args={"path": "sources/b.md"},
                    tool_call_id="call-b",
                ),
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="source_lookup",
                    content="Source A notes.",
                    tool_call_id="call-a",
                ),
                ToolReturnPart(
                    tool_name="source_lookup",
                    content="Source B notes.",
                    tool_call_id="call-b",
                ),
            ]
        ),
        ModelResponse(parts=[TextPart(content="Both sources reviewed.")]),
    ]


def _orphan_return_history():
    return [
        ModelRequest(parts=[UserPromptPart(content="Continue.")]),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="source_lookup",
                    content="No matching call.",
                    tool_call_id="missing-call",
                )
            ]
        ),
    ]


TOOL_HISTORY_BATCH_CODE = """
history_payload = await retrieve_history(scope="session", limit="all")
assembled = await assemble_context(history=history_payload.items)
batch = history_payload.items[1]
batch_item_indexes = []
for index, item in enumerate(history_payload.items):
    try:
        item.exchanges
        batch_item_indexes.append(index)
    except AttributeError:
        pass

assembled_batch_role_seen = False
for message in assembled.messages:
    try:
        message.exchanges
        assembled_batch_role_seen = True
    except AttributeError:
        pass

{
    "batch_item_indexes": batch_item_indexes,
    "exchange_count": len(batch.exchanges),
    "integrity": history_payload.metadata["tool_history_integrity"],
    "assembled_batch_role_seen": assembled_batch_role_seen,
}
"""


TOOL_HISTORY_ORPHAN_CODE = """
history_payload = await retrieve_history(scope="session", limit="all")
integrity = history_payload.metadata["tool_history_integrity"]
{
    "status": integrity["status"],
    "issue_codes": [issue["code"] for issue in integrity["issues"]],
}
"""
