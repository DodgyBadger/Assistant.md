"""Real chat API enforces tool-call and model capability limits."""

from __future__ import annotations

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.chat_tasks import chat_turn
from validation.pytest_scenarios.support.code_tool_chat import configure_provider
from validation.pytest_scenarios.support.file_tools import copy_image_fixture
from validation.pytest_scenarios.support.two_review_provider import two_review_provider

pytestmark = pytest.mark.scenario


async def test_tool_call_limit_rejects_multiple_real_model_calls(
    app: Application,
) -> None:
    vault = app.vault("ApiToolLimitVault")
    with two_review_provider() as server:
        await app.start()
        configure_provider(app, server)
        limit = app.request(
            "/api/system/settings/general/chat_tool_calls_limit", "PUT", {"value": "1"}
        )
        assert limit.status_code == 200
        for session in ("tool-limit-task", "tool-limit-stream"):
            result = await chat_turn(
                app,
                {
                    "vault_name": vault.name,
                    "session_id": session,
                    "prompt": "Write both files.",
                    "model": "code-tool-model",
                    "chat_mode": "normal",
                },
            )
            assert result["start_response"].status_code == 200
            assert result["terminal_event"]["event"] == "error"
            assert (
                result["terminal_event"]["details"]["setting"]
                == "chat_tool_calls_limit"
            )
        assert (
            app.request(
                "/api/system/settings/general/chat_tool_calls_limit",
                "PUT",
                {"value": "0"},
            ).status_code
            == 200
        )


async def test_text_only_model_rejects_image_input(app: Application) -> None:
    vault = app.vault("ApiImageLimitVault")
    copy_image_fixture(vault)
    image = vault / "images/test_image.jpg"
    await app.start()
    result = await chat_turn(
        app,
        {
            "vault_name": vault.name,
            "prompt": "Describe this image.",
            "model": "test",
            "image_paths": [str(image)],
        },
    )
    assert result["start_response"].status_code == 200
    assert result["terminal_event"]["event"] == "error"
    assert "vision" in result["text"].lower()
