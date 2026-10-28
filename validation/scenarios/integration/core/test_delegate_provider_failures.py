"""Delegate preserves provider billing metadata and retries real broken connections."""

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.delegate_error_inputs import rejecting_provider
from validation.scenarios.support.delegate_inputs import invoke_delegate

pytestmark = pytest.mark.scenario


async def test_delegate_provider_billing_failure_retains_http_status(app: Application):
    vault = app.vault("DelegateBilling")
    await app.start()
    with rejecting_provider(app) as server:
        result = await invoke_delegate(
            vault,
            "delegate_billing_failure",
            {
                "prompt": "Exercise real rejected provider response",
                "model": "code-tool-model",
            },
        )
        assert result.metadata["status"] == "failed"
        assert result.metadata["failure_kind"] == "billing"
        assert result.metadata["retryable"] is False
        assert result.metadata["http_status"] == 400
        assert len(server.calls) >= 1


async def test_delegate_disconnected_http_response_schedules_correlated_retry(
    app: Application,
):
    vault = app.vault("DelegateTransport")
    await app.start()
    for name, value in (
        ("model_stream_retries", "1"),
        ("model_stream_retry_base_delay_seconds", "0"),
    ):
        response = app.request(
            f"/api/system/settings/general/{name}", method="PUT", data={"value": value}
        )
        assert response.status_code == 200
    with rejecting_provider(app, disconnect=True) as server:
        result = await invoke_delegate(
            vault,
            "delegate_retry_failure",
            {
                "prompt": "Exercise actual disconnected provider stream",
                "model": "code-tool-model",
            },
        )
        assert result.metadata["status"] == "failed"
        assert len(server.calls) >= 2
        started = app.events.require(
            "delegate_started", workflow_id="delegate_retry_failure"
        )
        task_id = started["data"]["task_id"]
        app.events.require(
            "delegate_retry_scheduled",
            task_id=task_id,
            issue=f"delegate_retry:{task_id}:1",
        )
