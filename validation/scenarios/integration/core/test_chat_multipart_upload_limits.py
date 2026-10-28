"""Oversized multipart chat images fail before a task is created."""

import asyncio

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_surface_journeys import (
    oversized_image_upload,
)

pytestmark = pytest.mark.scenario


async def test_oversized_multipart_image_creates_no_task(app: Application) -> None:
    vault = app.vault("ChatMultipartUploadLimitsVault")
    await app.start()
    updated = app.request(
        "/api/system/settings/general/chunking_max_image_mb_per_image",
        method="PUT",
        data={"value": "1"},
    )
    assert updated.status_code == 200
    before = app.request("/api/tasks", params={"kind": "chat"})
    assert before.status_code == 200
    response = await asyncio.to_thread(oversized_image_upload, app, vault.name)
    assert response.status_code == 413
    assert "ChatImageUploadTooLarge" in response.text
    after = app.request("/api/tasks", params={"kind": "chat"})
    assert after.status_code == 200
    assert len(after.json()["tasks"]) == len(before.json()["tasks"])
