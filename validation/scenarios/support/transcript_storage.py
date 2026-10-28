"""Explicit provider-native canonical transcript input fixtures."""

from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart


def message(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def tool_result(tool_name: str, content: object, tool_call_id: str) -> ModelRequest:
    return ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name=tool_name,
                content=content,
                tool_call_id=tool_call_id,
            )
        ]
    )
