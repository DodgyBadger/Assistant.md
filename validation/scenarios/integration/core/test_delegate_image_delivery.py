"""A real delegate's child model receives the requested embedded image bytes."""

import hashlib

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.delegate_image_delivery import (
    image_payloads,
    image_provider,
    seed_delegate_image,
)
from validation.scenarios.support.delegate_inputs import invoke_delegate

pytestmark = pytest.mark.scenario


async def test_delegate_delivers_embedded_image_to_child_provider(
    app: Application,
) -> None:
    vault = app.vault("DelegateImageDeliveryVault")
    image = seed_delegate_image(app, vault)
    await app.start()
    from core.runtime.state import get_runtime_context

    with image_provider(app) as server:
        model = app.request(
            "/api/system/models/code-tool-model",
            method="PUT",
            data={
                "provider": "code-tool-provider",
                "model_string": "code-tool-model",
                "capabilities": ["text", "vision"],
            },
        )
        assert model.status_code == 200, model.text
        result = await invoke_delegate(
            vault,
            "delegate-image-delivery",
            {
                "prompt": "Read notes/0.md and use the attached image.",
                "model": "code-tool-model",
                "tools": ["file_read"],
            },
        )
        assert result.metadata["status"] == "completed"
        jobs = await get_runtime_context().task_coordinator.list_tasks(kind="delegate")
        assert len(jobs) == 1 and jobs[0].status == "completed"
        images = [
            payload
            for request in server.calls
            for payload in image_payloads(request["messages"])
        ]
        assert images
        assert any(
            hashlib.sha256(payload).digest() == hashlib.sha256(image).digest()
            for payload in images
        )
        assert any(row.get("stream") is True for row in server.calls)
        app.evidence.save_json(
            "delegate-image-delivery.json",
            {
                "source_sha256": hashlib.sha256(image).hexdigest(),
                "child_image_sha256": [
                    hashlib.sha256(payload).hexdigest() for payload in images
                ],
                "provider_requests": len(server.calls),
            },
        )
