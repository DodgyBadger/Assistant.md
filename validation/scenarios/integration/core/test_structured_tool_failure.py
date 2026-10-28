"""Unavailable web configuration produces a real structured tool failure."""

import pytest
from pydantic_ai.messages import ToolReturn

from validation.scenarios.application import Application
from validation.scenarios.support.model_failures import require_activity

pytestmark = pytest.mark.scenario


async def test_web_search_missing_credentials_returns_failure_metadata(
    app: Application,
) -> None:
    from core.tools.web_search import WebSearch

    await app.start()
    response = app.request(
        "/api/system/settings/general/web_search_strategy",
        method="PUT",
        data={"value": "tavily"},
    )
    assert response.status_code == 200, response.text
    private_query = "private search phrase"
    app.evidence.register_secret(private_query)
    result = await WebSearch.get_tool().function(query=private_query)
    assert isinstance(result, ToolReturn)
    assert isinstance(result.return_value, str) and result.return_value.strip()
    assert private_query not in result.return_value
    metadata = result.metadata
    assert isinstance(metadata, dict)
    for key, expected in {
        "tool_name": "web_search",
        "capability": "web_search",
        "strategy": "tavily",
        "status": "failed",
        "failure_kind": "unknown",
        "retryable": False,
        "phase": "web_search",
    }.items():
        assert metadata[key] == expected
    assert metadata["suggested_action"]
    app.evidence.append(
        "tool-failures.jsonl", {"metadata": metadata, "text": result.return_value}
    )
    require_activity(
        app,
        "web_capability_failed",
        fields={
            "capability": "web_search",
            "strategy": "tavily",
            "status": "failed",
            "failure_kind": "unknown",
            "retryable": False,
        },
    )
