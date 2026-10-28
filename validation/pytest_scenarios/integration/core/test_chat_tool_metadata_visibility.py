"""Chat metadata lists the supported tool identities."""

import pytest

from validation.pytest_scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_chat_tool_metadata_visibility(app: Application) -> None:
    app.vault("ChatToolMetadataVisibilityVault")
    await app.start()
    response = app.request("/api/metadata")
    assert response.status_code == 200
    names = {tool["name"] for tool in response.json()["tools"]}
    assert {
        "browser",
        "code_execution",
        "session_ops",
        "web_search",
        "web_extract",
        "web_crawl",
    } <= names
