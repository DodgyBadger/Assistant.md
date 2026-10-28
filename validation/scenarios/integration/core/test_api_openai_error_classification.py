"""Actual OpenAI SDK failures retain permanent and transient classifications."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.openai_status_http import openai_status_server

pytestmark = pytest.mark.scenario


async def test_real_openai_sdk_status_failures_classify_retryability(
    app: Application,
) -> None:
    app.evidence.register_secret("openai-status-fixture-key")
    await app.start()
    import httpx
    import openai

    from core.tools.failures import classify_exception

    with openai_status_server() as base:
        for status, retryable in ((400, False), (500, True)):
            async with httpx.AsyncClient(trust_env=False) as http_client:
                client = openai.AsyncOpenAI(
                    api_key="openai-status-fixture-key",
                    base_url=f"{base}/{status}/v1",
                    max_retries=0,
                    http_client=http_client,
                )
                with pytest.raises(openai.APIStatusError) as failure:
                    await client.chat.completions.create(
                        model="fixture", messages=[{"role": "user", "content": "test"}]
                    )
                assert failure.value.status_code == status
                classification = classify_exception(failure.value, phase="agent_stream")
                assert classification.retryable is retryable
                if status == 500:
                    assert classification.failure_kind == "provider_unavailable"
                    assert classification.http_status == 500
