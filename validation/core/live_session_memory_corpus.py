"""Shared loading and replay helpers for the live session-memory corpus."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

CORPUS_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "live_session_memory"
    / "representative_sessions.json"
)


def load_live_session_memory_corpus() -> dict[str, Any]:
    """Load the frozen privacy-safe evaluation corpus."""
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


def corpus_message_to_model_message(
    item: dict[str, Any],
) -> ModelRequest | ModelResponse:
    """Convert one corpus event into its provider-native canonical message."""
    kind = item["kind"]
    if kind == "user":
        return ModelRequest(parts=[UserPromptPart(content=item["content"])])
    if kind == "assistant":
        return ModelResponse(parts=[TextPart(content=item["content"])])
    if kind == "assistant_tool_call":
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name=item["tool_name"],
                    tool_call_id=item["tool_call_id"],
                    args={"task": item["content"]},
                )
            ]
        )
    if kind == "tool":
        return ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name=item["tool_name"],
                    tool_call_id=item["tool_call_id"],
                    content=item["content"],
                )
            ]
        )
    raise ValueError(f"Unsupported corpus message kind: {kind}")
