import json
from collections.abc import AsyncIterable, AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast

from pydantic_ai.agent import Agent
from pydantic_ai.messages import AgentStreamEvent, ModelMessage, UserContent
from pydantic_ai.tools import RunContext
from pydantic_ai.usage import RunUsage, UsageLimits

from core.constants import DEFAULT_TOOL_RETRIES
from core.llm.model_factory import build_model_instance
from core.llm.model_selection import ModelExecutionSpec
from core.llm.thinking import ThinkingValue
from core.settings import get_default_model_thinking
from core.settings.store import get_general_settings

_THINKING_UNSET = object()

PromptInput = str | Sequence[UserContent]


@dataclass(frozen=True)
class CollectedAgentRun:
    """Final output and messages from an internally streamed agent run."""

    output: Any
    messages: list[ModelMessage]


@dataclass
class AgentRunProgress:
    """Latest process-local settled state observed while collecting one run."""

    messages: list[ModelMessage] = field(default_factory=list)
    output: Any = None
    usage: RunUsage = field(default_factory=RunUsage)


async def create_agent(
    model: Any = None,
    tools: list[Any] | None = None,
    retries: int | None = None,
    output_type: Any | None = None,
    history_processors: list[Any] | None = None,
    capabilities: list[Any] | None = None,
    thinking: ThinkingValue | object = _THINKING_UNSET,
) -> Agent:
    """Create agent by composing pre-configured components following Pydantic AI patterns.

    Pure composition function that assembles pre-configured model and tools into a Pydantic AI Agent.

    Args:
        model: Pre-configured Pydantic AI model instance, or None to use default model
        tools: Optional list of tool functions (extracted from BaseTool classes by directives)
        retries: Number of retries for tool validation errors (defaults to DEFAULT_TOOL_RETRIES)
        output_type: Optional structured output specification for the agent
        history_processors: Optional list of history processors to apply
        capabilities: Optional PydanticAI capabilities to attach to the agent

    Returns:
        Configured Pydantic AI Agent ready for use
    """

    # Handle default model if none provided
    if model is None:
        general_settings = get_general_settings()
        default_model_entry = general_settings.get("default_model")
        default_model_value = default_model_entry.value if default_model_entry else None
        if not default_model_value:
            raise ValueError(
                "Set 'default_model' in system/settings.yaml before creating agents without an explicit model."
            )

        default_model_name = str(default_model_value).lower().strip()

        resolved_thinking: ThinkingValue = (
            get_default_model_thinking()
            if thinking is _THINKING_UNSET
            else cast(ThinkingValue, thinking)
        )
        model = build_model_instance(default_model_name, thinking=resolved_thinking)
        if isinstance(model, ModelExecutionSpec) and model.mode == "skip":
            raise ValueError(
                "default_model cannot use skip mode ('none'). "
                "Set a concrete LLM alias in system/settings.yaml."
            )

    # Pure composition - assemble the pre-configured pieces
    agent_kwargs = {
        "model": model,
        "retries": retries if retries is not None else DEFAULT_TOOL_RETRIES,
    }
    if history_processors:
        agent_kwargs["history_processors"] = history_processors
    if tools:
        agent_kwargs["tools"] = tools
    if output_type is not None:
        agent_kwargs["output_type"] = output_type
    if capabilities:
        agent_kwargs["capabilities"] = capabilities

    agent = Agent(**agent_kwargs)

    agent.instructions(
        lambda _: f"The current date is {datetime.today().strftime('%A, %B %d, %Y')}."
    )

    return agent


async def generate_stream(
    agent: Agent,
    prompt: PromptInput,
    message_history: Sequence[ModelMessage] | None,
) -> AsyncIterator[str]:
    try:
        async with agent.run_stream(prompt, message_history=message_history) as result:
            full_response = ""
            async for text in result.stream_text():
                delta_text = text[len(full_response) :]
                full_response = text

                chunk = {
                    "choices": [
                        {
                            "delta": {"content": delta_text},
                            "index": 0,
                            "finish_reason": None,
                        }
                    ]
                }
                yield f"data: {json.dumps(chunk)}\n\n"

            yield f"data: {json.dumps({'choices': [{'delta': {}, 'index': 0, 'finish_reason': 'stop'}]})}\n\n"

    except Exception:
        raise


async def generate_response(
    agent: Agent,
    prompt: PromptInput,
    message_history: Sequence[ModelMessage] | None = None,
    deps: Any = None,
) -> Any:
    """Return a final response using streamed requests and the full retry graph."""

    try:
        kwargs: dict[str, Any] = {
            "deps": deps,
            "event_stream_handler": _consume_agent_events,
        }
        if message_history:
            kwargs["message_history"] = message_history
        result = await agent.run(prompt, **kwargs)
        return result.output

    except Exception:
        raise


async def _consume_agent_events(
    _run_context: RunContext[Any],
    events: AsyncIterable[AgentStreamEvent],
) -> None:
    """Drain model events so ``Agent.run`` uses streaming transport."""

    async for _ in events:
        pass


async def collect_response(
    agent: Agent,
    prompt: PromptInput,
    message_history: Sequence[ModelMessage] | None = None,
    deps: Any = None,
    usage_limits: UsageLimits | None = None,
    usage: RunUsage | None = None,
    progress: AgentRunProgress | None = None,
) -> CollectedAgentRun:
    """Run an agent through the streaming transport and return one final result.

    The ChatGPT/Codex OAuth backend requires streamed model requests. Background
    tasks still need a single final value, so this helper consumes the stream
    internally and preserves the old final-output contract.
    """

    kwargs: dict[str, Any] = {"deps": deps}
    if message_history:
        kwargs["message_history"] = message_history
    if usage_limits is not None:
        kwargs["usage_limits"] = usage_limits
    effective_usage = usage
    if progress is not None:
        if effective_usage is None:
            effective_usage = progress.usage
        else:
            progress.usage = effective_usage
    if effective_usage is not None:
        kwargs["usage"] = effective_usage

    async with agent.run_stream(prompt, **kwargs) as result:
        try:
            async for streamed_output in result.stream_output(debounce_by=None):
                if progress is not None:
                    progress.output = streamed_output
                    progress.messages = list(result.all_messages())
            output = await result.get_output()
            messages = list(result.all_messages())
            if progress is not None:
                progress.output = output
                progress.messages = messages
            return CollectedAgentRun(output=output, messages=messages)
        finally:
            if progress is not None:
                progress.messages = list(result.all_messages())
