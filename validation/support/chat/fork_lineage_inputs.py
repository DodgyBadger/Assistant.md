"""Explicit native canonical message inputs; seeding does not prove generation."""

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    UserPromptPart,
)


def _user(text):
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _assistant(text):
    return ModelResponse(parts=[TextPart(content=text)])


def _map_context(text):
    return ModelRequest(parts=[SystemPromptPart(content=text)])
