"""Deterministic contract for stepped history eviction planning."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from validation.core.base_scenario import BaseScenario
from validation.core.tool_history_fixtures import tool_reply_cases


class SteppedEvictionPlannerScenario(BaseScenario):
    """Prove watermarks never split a complete turn or tool exchange."""

    async def test_scenario(self) -> None:
        import core.chat.compaction as compaction

        for case in tool_reply_cases():
            for force in (False, True):
                arguments = {
                    "high_watermark_tokens": 1,
                    "low_watermark_tokens": 0,
                    "minimum_retained_groups": 1,
                    "force": force,
                }
                plan = compaction.plan_session_map_reduction(case.messages, **arguments)
                expected_reason = "invalid_tool_history" if case.issue_codes else None
                assert (
                    compaction.session_map_reduction_unavailability_reason(
                        case.messages, **arguments
                    )
                    == expected_reason
                ), case.name
                assert plan.status == (
                    "no_op" if case.issue_codes else "planned"
                ), case.name
                if not case.issue_codes:
                    assert plan.eviction_end_index == len(case.messages) - 2, case.name

        ordinary = [
            _user("first question " * 40),
            _assistant("first answer " * 40),
            _user("second question " * 40),
            _assistant("second answer " * 40),
            _user("latest question " * 40),
            _assistant("latest answer " * 40),
        ]
        total_tokens = compaction.estimate_history_tokens(ordinary)
        latest_turn_tokens = compaction.estimate_history_tokens(ordinary[-2:])

        below_high = compaction.plan_stepped_history_eviction(
            ordinary,
            high_watermark_tokens=total_tokens + 1,
            low_watermark_tokens=latest_turn_tokens,
            minimum_retained_groups=1,
        )
        assert below_high.status == "no_op"
        assert below_high.reason == "below_high_watermark"
        assert below_high.eviction_end_index == 0

        at_high = compaction.plan_stepped_history_eviction(
            ordinary,
            high_watermark_tokens=total_tokens,
            low_watermark_tokens=latest_turn_tokens,
            minimum_retained_groups=1,
        )
        assert at_high.status == "planned"
        assert at_high.eviction_end_index == 4

        ordinary_plan = compaction.plan_stepped_history_eviction(
            ordinary,
            high_watermark_tokens=total_tokens - 1,
            low_watermark_tokens=latest_turn_tokens,
            minimum_retained_groups=1,
        )
        assert ordinary_plan.status == "planned"
        assert ordinary_plan.reason == "low_watermark_reached"
        assert ordinary_plan.eviction_end_index == 4
        assert ordinary_plan.evicted_group_count == 2
        assert ordinary_plan.eviction_start_index == 0
        assert ordinary_plan.retained_message_count == 2
        assert ordinary_plan.estimated_tokens_after <= latest_turn_tokens

        tool_history = [
            _user("Use both tools."),
            ModelResponse(
                parts=[
                    ToolCallPart(tool_name="alpha", args={}, tool_call_id="call-alpha"),
                    ToolCallPart(tool_name="beta", args={}, tool_call_id="call-beta"),
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="alpha",
                        content="alpha result " * 30,
                        tool_call_id="call-alpha",
                    ),
                    ToolReturnPart(
                        tool_name="beta",
                        content="beta result " * 30,
                        tool_call_id="call-beta",
                    ),
                ]
            ),
            _assistant("Both results handled."),
            _user("What comes next?"),
            _assistant("The recent answer stays."),
        ]
        tool_total = compaction.estimate_history_tokens(tool_history)
        tool_recent = compaction.estimate_history_tokens(tool_history[-2:])
        tool_plan = compaction.plan_stepped_history_eviction(
            tool_history,
            high_watermark_tokens=tool_total - 1,
            low_watermark_tokens=tool_recent,
            minimum_retained_groups=1,
        )
        assert tool_plan.status == "planned"
        assert tool_plan.eviction_end_index == 4
        assert tool_plan.evicted_group_count == 1
        assert tool_plan.retained_message_count == 2

        recovery_history = [
            ModelRequest(
                parts=[SystemPromptPart(content="AssistantMD compacted chat history")]
            ),
            _user("Continue from the recovery card."),
            _assistant("Continuing with the live task."),
        ]
        recovery_total = compaction.estimate_history_tokens(recovery_history)
        recovery_recent = compaction.estimate_history_tokens(recovery_history[-2:])
        recovery_plan = compaction.plan_stepped_history_eviction(
            recovery_history,
            high_watermark_tokens=recovery_total - 1,
            low_watermark_tokens=recovery_recent,
            minimum_retained_groups=1,
        )
        assert recovery_plan.status == "planned"
        assert recovery_plan.eviction_end_index == 1
        assert recovery_plan.evicted_group_count == 1

        pinned_map = ModelRequest(
            parts=[SystemPromptPart(content="AssistantMD session map")]
        )
        map_history = [pinned_map, *ordinary]
        map_total = compaction.estimate_history_tokens(map_history)
        map_target = compaction.estimate_history_tokens([pinned_map, *ordinary[-2:]])
        map_plan = compaction.plan_stepped_history_eviction(
            map_history,
            high_watermark_tokens=map_total - 1,
            low_watermark_tokens=map_target,
            minimum_retained_groups=1,
            retained_prefix_count=1,
        )
        assert map_plan.status == "planned"
        assert map_plan.retained_prefix_count == 1
        assert map_plan.eviction_start_index == 1
        assert map_plan.eviction_end_index == 5
        assert map_plan.evicted_message_count == 4
        assert map_plan.retained_message_count == 3
        assert map_plan.estimated_tokens_after <= map_target

        oversized_latest = [
            _user("old question"),
            _assistant("old answer"),
            _user("oversized latest question " * 300),
            _assistant("oversized latest answer " * 300),
        ]
        oversized_total = compaction.estimate_history_tokens(oversized_latest)
        oversized_recent = compaction.estimate_history_tokens(oversized_latest[-2:])
        oversized_plan = compaction.plan_stepped_history_eviction(
            oversized_latest,
            high_watermark_tokens=oversized_total - 1,
            low_watermark_tokens=max(1, oversized_recent - 1),
            minimum_retained_groups=1,
        )
        assert oversized_plan.status == "planned"
        assert oversized_plan.reason == "retained_group_floor_exceeds_low_watermark"
        assert oversized_plan.eviction_end_index == 2
        assert oversized_plan.estimated_tokens_after == oversized_recent

        abandoned_prefix = [
            _user("An unanswered old request."),
            _user("A newer request."),
            _assistant("Only the newer request was answered."),
        ]
        abandoned_plan = compaction.plan_stepped_history_eviction(
            abandoned_prefix,
            high_watermark_tokens=1,
            low_watermark_tokens=0,
            minimum_retained_groups=1,
        )
        assert abandoned_plan.status == "planned"
        assert abandoned_plan.reason == "retained_group_floor_exceeds_low_watermark"
        assert abandoned_plan.eviction_end_index == 1
        assert abandoned_plan.evicted_message_count == 1
        assert abandoned_plan.retained_message_count == 2

        newest_incomplete = [
            _user("An answered old request."),
            _assistant("The old answer."),
            _user("The active request must remain verbatim."),
        ]
        newest_incomplete_plan = compaction.plan_stepped_history_eviction(
            newest_incomplete,
            high_watermark_tokens=1,
            low_watermark_tokens=0,
            minimum_retained_groups=1,
        )
        assert newest_incomplete_plan.status == "planned"
        assert newest_incomplete_plan.eviction_end_index == 2
        assert newest_incomplete_plan.evicted_message_count == 2
        assert newest_incomplete_plan.retained_message_count == 1

        malformed_tools = [
            _user("Use a tool."),
            ModelResponse(
                parts=[ToolCallPart(tool_name="probe", args={}, tool_call_id="orphan")]
            ),
            _user("Continue without its return."),
            _assistant("Continuing."),
        ]
        malformed_plan = compaction.plan_stepped_history_eviction(
            malformed_tools,
            high_watermark_tokens=1,
            low_watermark_tokens=0,
            minimum_retained_groups=1,
        )
        assert malformed_plan.status == "no_op"
        assert malformed_plan.reason == "invalid_tool_history"

        for tool_call_id, return_name in (
            ("", "probe"),
            (" \t ", "probe"),
            ("mismatched", "other"),
        ):
            malformed_identity = [
                _user("Use a tool."),
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
                            tool_name=return_name,
                            content="result",
                            tool_call_id=tool_call_id,
                        )
                    ]
                ),
                _assistant("Finished."),
                _user("Continue."),
                _assistant("Latest answer."),
            ]
            for force in (False, True):
                arguments = {
                    "high_watermark_tokens": 1,
                    "low_watermark_tokens": 0,
                    "minimum_retained_groups": 1,
                    "force": force,
                }
                identity_plan = compaction.plan_session_map_reduction(
                    malformed_identity, **arguments
                )
                assert identity_plan.status == "no_op"
                assert identity_plan.reason == "invalid_tool_history"
                assert (
                    compaction.session_map_reduction_unavailability_reason(
                        malformed_identity, **arguments
                    )
                    == "invalid_tool_history"
                )

        try:
            compaction.plan_stepped_history_eviction(
                ordinary,
                high_watermark_tokens=100,
                low_watermark_tokens=100,
                minimum_retained_groups=1,
            )
        except ValueError as exc:
            assert "low watermark" in str(exc).lower()
        else:
            raise AssertionError("Equal watermarks should be rejected")

        long_latest_with_floor = [
            _user("oldest question"),
            _assistant("oldest answer"),
            _user("middle question"),
            _assistant("middle answer"),
            _user("recent question"),
            _assistant("recent answer"),
            _user("very long latest question " * 300),
            _assistant("very long latest answer " * 300),
        ]
        floor_plan = compaction.plan_stepped_history_eviction(
            long_latest_with_floor,
            high_watermark_tokens=2,
            low_watermark_tokens=1,
            minimum_retained_groups=3,
        )
        assert floor_plan.status == "planned"
        assert floor_plan.reason == "retained_group_floor_exceeds_low_watermark"
        assert floor_plan.eviction_end_index == 2
        assert floor_plan.evicted_group_count == 1
        assert floor_plan.group_count - floor_plan.evicted_group_count == 3
        assert floor_plan.estimated_tokens_after > floor_plan.low_watermark_tokens

        exactly_floor = compaction.plan_stepped_history_eviction(
            long_latest_with_floor[-6:],
            high_watermark_tokens=1,
            low_watermark_tokens=0,
            minimum_retained_groups=3,
        )
        assert exactly_floor.status == "no_op"
        assert exactly_floor.reason == "minimum_retained_groups"

        try:
            compaction.plan_stepped_history_eviction(
                ordinary,
                high_watermark_tokens=100,
                low_watermark_tokens=1,
                minimum_retained_groups=0,
            )
        except ValueError as exc:
            assert "at least one" in str(exc).lower()
        else:
            raise AssertionError("A zero retained-group floor should be rejected")

        self.assert_no_failures()


def _user(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _assistant(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])
