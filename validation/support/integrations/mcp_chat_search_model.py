"""Deterministic model input that inspects and invokes real deferred MCP tools."""


def searching_model(connection_id, observed):
    from pydantic_ai.messages import (
        ModelResponse,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
        ToolSearchCallPart,
    )
    from pydantic_ai.models.function import FunctionModel

    def run(messages, info):
        definitions = {tool.name: tool for tool in info.function_tools}
        authored = {
            tool.name: tool for tool in info.model_request_parameters.function_tools
        }
        observed.append({"visible": sorted(definitions), "authored": sorted(authored)})
        if len(observed) == 1:
            assert not definitions["file_read"].defer_loading
            assert "gmail_search_messages" not in definitions
            assert authored["gmail_search_messages"].defer_loading
            assert "gmail_archive_message" not in authored
            assert authored["gmail_search_messages"].metadata["assistantmd"] == {
                "source": "mcp",
                "connection_id": connection_id,
                "connection_name": "Gmail",
                "connection_slug": "gmail",
            }
            return ModelResponse(
                parts=[
                    ToolSearchCallPart(
                        args={"queries": ["search Gmail messages"]},
                        tool_call_id="search-mcp",
                    )
                ]
            )
        if len(observed) == 2:
            assert definitions["gmail_search_messages"].defer_loading
            assert (
                info.model_request_parameters.visibility_of("gmail_search_messages")
                == "visible"
            )
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="gmail_search_messages",
                        args={"query": "project alpha"},
                        tool_call_id="call-mcp",
                    )
                ]
            )
        returns = [
            part
            for message in messages
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_call_id == "call-mcp"
        ]
        assert len(returns) == 1 and "matching message for project alpha" in str(
            returns[0].content
        )
        return ModelResponse(parts=[TextPart(content="MCP result observed")])

    return FunctionModel(run)
