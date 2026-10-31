"""Explicit canonical conversation inputs for manual compaction journeys."""


def seed_tool_history(store, vault, session):
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    store.ensure_session(session, vault, owner_principal_id="local-user")
    store.add_messages(
        session,
        vault,
        [
            ModelRequest(parts=[UserPromptPart(content="First user decision.")]),
            ModelResponse(parts=[TextPart(content="First assistant answer.")]),
            ModelRequest(parts=[UserPromptPart(content="Please use the probe tool.")]),
            ModelResponse(
                parts=[ToolCallPart(tool_name="probe", args={}, tool_call_id="probe-1")]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="probe",
                        content="probe result",
                        tool_call_id="probe-1",
                    )
                ]
            ),
            ModelResponse(parts=[TextPart(content="Probe result handled.")]),
        ],
    )


def seed_tool_result_shapes(store, vault, session):
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    store.ensure_session(session, vault, owner_principal_id="local-user")
    parts = [ModelRequest(parts=[UserPromptPart(content="Inspect results.")])]
    for name, result, outcome in (
        ("empty_probe", "", "success"),
        (
            "failed_probe",
            "long failure details should not enter compaction prompt",
            "failed",
        ),
        ("large_success_probe", "large successful result " * 100, "success"),
    ):
        parts.append(
            ModelResponse(
                parts=[ToolCallPart(tool_name=name, args={}, tool_call_id=name)]
            )
        )
        parts.append(
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name=name,
                        content=result,
                        tool_call_id=name,
                        outcome=outcome,
                    )
                ]
            )
        )
    parts.append(ModelResponse(parts=[TextPart(content="Results inspected.")]))
    parts.extend(
        [
            ModelRequest(parts=[UserPromptPart(content="Recent turn.")]),
            ModelResponse(parts=[TextPart(content="Recent answer.")]),
        ]
    )
    store.add_messages(session, vault, parts)
