"""Configure a real tool-free chat for retry usage-budget checks."""

import json

from validation.pytest_scenarios.support.chat_persistence import PERSISTENCE_CONTEXT
from validation.pytest_scenarios.support.session_ops_chat import configure_provider


def prepare_tool_free_retry(app, server, vault):
    app.write(vault, "AssistantMD/Authoring/full_history.md", PERSISTENCE_CONTEXT)
    configure_provider(app, server)
    metadata = app.request("/api/metadata")
    assert metadata.status_code == 200
    values = {
        "disabled_tools": json.dumps([row["name"] for row in metadata.json()["tools"]]),
        "model_stream_retries": "1",
        "model_stream_retry_base_delay_seconds": "0",
        "chat_model_requests_limit": "1",
    }
    for key, value in values.items():
        response = app.request(
            f"/api/system/settings/general/{key}", method="PUT", data={"value": value}
        )
        assert response.status_code == 200, response.text
