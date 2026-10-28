"""Retained mixed tool returns exclude retrieval and keep independent results."""

from dataclasses import replace

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.session_map_retrieval_inputs import (
    seed_retrieval,
)

pytestmark = pytest.mark.scenario


async def test_mixed_retained_tool_return_preserves_independent_result(
    app: Application,
) -> None:
    vault = app.vault("MapMixedRetainedVault")
    await app.start()
    from pydantic_ai.messages import ModelRequest, ToolReturnPart

    from core.memory.session_map.retained_evidence import (
        project_retained_session_map_evidence,
    )
    from core.runtime.state import get_runtime_context
    from core.utils.messages import project_message

    store = get_runtime_context().chat_store
    session = "mixed-retained"
    seed_retrieval(store, vault.name, session)
    original = store.get_stored_messages(session, vault.name, mode="raw")[4]
    mixed = ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name="session_ops", content="private window", tool_call_id="window"
            ),
            ToolReturnPart(
                tool_name="other_tool",
                content="independent result",
                tool_call_id="other",
            ),
        ]
    )
    input_row = replace(
        original, message=mixed, content_text=project_message(mixed).content_text
    )
    projected = project_retained_session_map_evidence([input_row])
    assert [item.content_text for item in projected] == [
        "[other_tool] independent result"
    ]
