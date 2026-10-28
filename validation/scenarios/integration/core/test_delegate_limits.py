"""Real delegate model request limits and public retry setting validation."""

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.delegate_inputs import (
    delegate_provider,
    invoke_delegate,
)

pytestmark = pytest.mark.scenario


async def test_delegate_real_model_request_limit_returns_checkpoint_guidance(
    app: Application,
):
    vault = app.vault("DelegateUsageLimit")
    app.write(vault, "notes/0.md", "Actual read before request limit")
    await app.start()
    response = app.request(
        "/api/system/settings/general/delegate_model_requests_limit",
        method="PUT",
        data={"value": "1"},
    )
    assert response.status_code == 200, response.text
    with delegate_provider(app) as server:
        result = await invoke_delegate(
            vault,
            "delegate_model_request_limit_failure",
            {
                "prompt": "Read bounded source and finish",
                "model": "code-tool-model",
                "tools": ["file_read"],
            },
        )
        assert result.metadata["status"] == "failed"
        assert result.metadata["limit_kind"] == "model_requests"
        assert result.metadata["limit_setting"] == "delegate_model_requests_limit"
        assert "model-request limit" in result.return_value
        assert "goal_ops" in result.return_value
        assert len(server.calls) == 1


async def test_delegate_retry_settings_accept_valid_inputs_and_reject_unsafe_bounds(
    app: Application,
):
    await app.start()
    for setting, value in (
        ("delegate_repeated_failure_limit", "2"),
        ("delegate_timeout_seconds", "90"),
        ("model_stream_retries", "1"),
        ("model_stream_retry_base_delay_seconds", "0"),
        ("model_stream_retry_max_delay_seconds", "10"),
    ):
        response = app.request(
            f"/api/system/settings/general/{setting}",
            method="PUT",
            data={"value": value},
        )
        assert response.status_code == 200, response.text
    for setting, value in (
        ("model_stream_retries", "6"),
        ("model_stream_retry_max_delay_seconds", "-1"),
    ):
        response = app.request(
            f"/api/system/settings/general/{setting}",
            method="PUT",
            data={"value": value},
        )
        assert response.status_code == 400, response.text
